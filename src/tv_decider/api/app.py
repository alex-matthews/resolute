"""HTTP API. Same engine as the CLI; the webhook route is just another trigger."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, ValidationError

from ..config import Policy, Settings
from ..engine.engine import DecisionEngine
from ..executor import ExecutionBlocked, Executor
from ..schemas import AutomationMode, Decision, DecisionRequest, FeedbackIn, Resolution
from ..seerr.client import SeerrClient, SeerrError
from ..seerr.webhook import WebhookRejection, normalize_webhook
from ..sonarr.audit import audit_series_profile
from ..store.db import Store

logger = logging.getLogger(__name__)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seerr_request_id: int
    mode: AutomationMode | None = None


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str | None = None
    tvdb_id: int | None = None
    expected_resolution: Resolution | None = None


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator: str


class DecideBody(DecisionRequest):
    mode: AutomationMode | None = None


def create_app(
    settings: Settings,
    policy: Policy,
    engine: DecisionEngine,
    store: Store,
    executor: Executor | None = None,
    seerr: SeerrClient | None = None,
) -> FastAPI:
    app = FastAPI(title="tv-decider", version="0.1.0")
    metrics: Counter[str] = Counter()

    def _decide_and_store(request: DecisionRequest, mode: AutomationMode | None) -> Decision:
        decision = engine.decide(request, mode)
        store.save_decision(decision)
        metrics[f"decisions_total{{resolution=\"{decision.final_resolution}\"}}"] += 1
        return decision

    # -- health / observability -------------------------------------------

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict:
        store.list_decisions(limit=1)  # proves the DB is reachable
        return {"status": "ready", "mode": settings.mode}

    @app.get("/metrics")
    def metrics_endpoint() -> Response:
        lines = ["# TYPE tvdecider_events counter"]
        lines += [f"tvdecider_{key} {value}" for key, value in sorted(metrics.items())]
        return Response("\n".join(lines) + "\n", media_type="text/plain")

    # -- decisions -----------------------------------------------------------

    @app.post("/api/decisions")
    def post_decision(body: DecideBody) -> Decision:
        mode = body.mode
        request = DecisionRequest(**body.model_dump(exclude={"mode"}))
        return _decide_and_store(request, mode)

    @app.get("/api/decisions/{decision_id}")
    def get_decision(decision_id: str) -> Decision:
        decision = store.get_decision(decision_id)
        if decision is None:
            raise HTTPException(404, "decision not found")
        return decision

    @app.get("/api/decisions")
    def list_decisions(limit: int = 20) -> list[Decision]:
        return store.list_decisions(limit=min(limit, 200))

    @app.post("/api/decisions/{decision_id}/execute")
    def execute_decision(decision_id: str, body: ExecuteRequest, request: Request) -> dict:
        if executor is None:
            raise HTTPException(503, "executor not configured")
        if not settings.execute_token:
            raise HTTPException(
                403,
                "HTTP execution is disabled: set execute_token in config and send it"
                " as X-TVD-Operator-Token (or execute via the CLI)",
            )
        if request.headers.get("X-TVD-Operator-Token") != settings.execute_token:
            raise HTTPException(403, "invalid operator token")
        decision = store.get_decision(decision_id)
        if decision is None:
            raise HTTPException(404, "decision not found")
        try:
            executed = executor.execute(decision, operator_approved=True)
        except ExecutionBlocked as exc:
            raise HTTPException(409, str(exc)) from exc
        store.mark_executed(decision_id, [a.value for a in executed], operator=body.operator)
        metrics["executions_total"] += 1
        return {"decision_id": decision_id, "executed_actions": executed}

    # -- Seerr webhook ---------------------------------------------------------

    @app.post("/api/webhooks/seerr")
    async def seerr_webhook(request: Request) -> dict:
        secret = settings.seerr.webhook_shared_secret
        if secret and request.headers.get("X-TVD-Token") != secret:
            metrics["webhook_unauthorized_total"] += 1
            raise HTTPException(401, "invalid webhook token")
        payload: dict[str, Any] = await request.json()

        try:
            decision_request = normalize_webhook(
                payload, settings.seerr.trigger_notification_types
            )
        except WebhookRejection as exc:
            store.save_webhook_event(payload, outcome=f"skipped: {exc}")
            metrics["webhook_skipped_total"] += 1
            return {"status": "skipped", "reason": str(exc)}
        except ValidationError as exc:
            store.save_webhook_event(payload, outcome="invalid")
            metrics["webhook_invalid_total"] += 1
            raise HTTPException(422, f"unrecognized webhook payload: {exc}") from exc

        decision = _decide_and_store(decision_request, None)
        store.save_webhook_event(payload, outcome="decided", decision_id=decision.decision_id)
        metrics["webhook_decided_total"] += 1

        executed: list[str] = []
        if (
            executor is not None
            and settings.mode in (AutomationMode.AUTO_PROFILE, AutomationMode.AUTO_APPROVE)
        ):
            try:
                executed = [a.value for a in executor.execute(decision)]
                if executed:
                    store.mark_executed(decision.decision_id, executed, operator="auto")
            except ExecutionBlocked as exc:
                logger.info("auto-execution blocked: %s", exc)

        return {
            "status": "decided",
            "decision_id": decision.decision_id,
            "final_resolution": decision.final_resolution,
            "confidence": decision.confidence,
            "mode": decision.mode,
            "action_plan": [a.model_dump() for a in decision.action_plan],
            "executed_actions": executed,
            "shadow_delta": decision.shadow_delta,
        }

    # -- feedback / calibration -------------------------------------------------

    @app.post("/api/feedback")
    def post_feedback(body: FeedbackIn) -> dict:
        if store.get_decision(body.decision_id) is None:
            raise HTTPException(404, "decision not found")
        if body.reason_tag and body.reason_tag not in policy.feedback_reason_tags:
            raise HTTPException(
                422,
                f"unknown reason_tag '{body.reason_tag}';"
                f" allowed: {policy.feedback_reason_tags}",
            )
        record = store.save_feedback(body)
        metrics["feedback_total"] += 1
        return {"feedback_id": record.feedback_id, "decision_id": record.decision_id}

    @app.get("/api/calibration/summary")
    def calibration_summary() -> dict:
        return store.calibration_summary()

    # -- scheduled review ---------------------------------------------------------

    @app.post("/api/reviews/pending")
    def review_pending(limit: int = 50) -> dict:
        """Decide every pending Seerr TV request. Decisions are stored, never executed
        — this endpoint is shadow-safe in every mode, so schedulers can hit it freely."""
        if seerr is None:
            raise HTTPException(503, "no Seerr client configured")
        from ..metadata.source import seerr_request_state_from_api
        from ..schemas import TriggerSource

        try:
            pending = seerr.list_requests(filter="pending", take=min(limit, 200))
        except SeerrError as exc:
            raise HTTPException(502, f"seerr unavailable: {exc}") from exc
        reviewed = []
        for req in pending:
            media = req.get("media") or {}
            if media.get("mediaType") != "tv":
                continue
            state = seerr_request_state_from_api(req)
            decision = _decide_and_store(
                DecisionRequest(
                    seerr_request_id=state.request_id,
                    tmdb_id=media.get("tmdbId"),
                    tvdb_id=media.get("tvdbId"),
                    trigger=TriggerSource.SCHEDULED_REVIEW,
                ),
                None,
            )
            reviewed.append(
                {
                    "seerr_request_id": state.request_id,
                    "decision_id": decision.decision_id,
                    "title": decision.title,
                    "final_resolution": decision.final_resolution,
                    "confidence": decision.confidence,
                }
            )
        metrics["reviews_total"] += 1
        return {"reviewed": len(reviewed), "decisions": reviewed}

    # -- planning / audit --------------------------------------------------------

    @app.post("/api/seerr/plan")
    def seerr_plan(body: PlanRequest) -> Decision:
        request = DecisionRequest(seerr_request_id=body.seerr_request_id)
        return _decide_and_store(request, body.mode)

    @app.post("/api/sonarr/audit")
    def sonarr_audit(body: AuditRequest) -> dict:
        if body.decision_id:
            decision = store.get_decision(body.decision_id)
            if decision is None:
                raise HTTPException(404, "decision not found")
            tvdb_id = decision.evidence.facts.tvdb_id
            expected = decision.final_resolution
        elif body.tvdb_id and body.expected_resolution:
            tvdb_id, expected = body.tvdb_id, body.expected_resolution
        else:
            raise HTTPException(422, "provide decision_id, or tvdb_id + expected_resolution")
        if tvdb_id is None:
            raise HTTPException(422, "decision has no tvdb id to audit")

        # Re-collect current Sonarr state through the engine's evidence source.
        evidence = engine.evidence_source.collect(DecisionRequest(tvdb_id=tvdb_id))
        result = audit_series_profile(
            evidence.sonarr,
            expected,
            profile_name_1080p=settings.seerr.profile_name_1080p,
            profile_name_2160p=settings.seerr.profile_name_2160p,
            tvdb_id=tvdb_id,
        )
        store.save_audit(result.model_dump(), decision_id=body.decision_id)
        metrics["audits_total"] += 1
        return result.model_dump()

    return app
