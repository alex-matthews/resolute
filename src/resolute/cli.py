"""CLI: same decision engine as the API, plus calibration and ops helpers."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from .config import load_policy, load_settings
from .engine.engine import DecisionEngine
from .metadata.source import FixtureEvidenceSource
from .schemas import (
    AutomationMode,
    Decision,
    DecisionRequest,
    FeedbackIn,
    FeedbackVerdict,
    Resolution,
    TriggerSource,
)
from .store.db import Store

app = typer.Typer(help="resolute: Seerr-first 1080p vs 2160p TV decision engine")

_config_option = typer.Option(None, "--config", help="Path to config YAML")
_fixtures_option = typer.Option(
    None, "--fixtures", help="Decide from evidence fixtures in this directory (no network)"
)


def _build(config: str | None, fixtures: str | None):
    """Build (settings, policy, engine, store): live by default, offline with --fixtures."""
    if fixtures:
        settings = load_settings(config)
        policy = load_policy(settings.policy_path)
        engine = DecisionEngine(settings, policy, FixtureEvidenceSource(fixtures))
        store = Store(settings.db_path)
        return settings, policy, engine, store
    from .runtime import build_runtime

    rt = build_runtime(config)
    return rt.settings, rt.policy, rt.engine, rt.store


def _print_decision(decision: Decision, as_json: bool) -> None:
    if as_json:
        typer.echo(decision.model_dump_json(indent=2))
        return
    typer.echo(f"decision : {decision.decision_id}")
    typer.echo(f"title    : {decision.title} ({decision.year})")
    typer.echo(f"final    : {decision.final_resolution}  confidence={decision.confidence}")
    typer.echo(f"objective: {decision.objective.resolution}  household: {decision.household.resolution}")
    typer.echo(f"score    : {decision.score}  mode={decision.mode}")
    if decision.risk_flags:
        typer.echo(f"risks    : {', '.join(decision.risk_flags)}")
    if decision.shadow_delta:
        typer.echo(f"shadow   : {decision.shadow_delta}")
    typer.echo("reasons  :")
    for reason in decision.top_reasons:
        typer.echo(f"  - {reason}")
    typer.echo("plan     :")
    for action in decision.action_plan:
        gate = "needs-approval" if action.requires_approval else "auto-ok"
        typer.echo(f"  - {action.type} [{gate}] {action.note or ''}")


@app.command()
def decide(
    title: str,
    year: int | None = typer.Option(None),
    season: list[int] = typer.Option([], "--season", help="Season number (repeatable)"),
    requester: str | None = typer.Option(None),
    tmdb_id: int | None = typer.Option(None),
    mode: AutomationMode | None = typer.Option(None),
    force_judge: bool = typer.Option(False, help="Consult the LLM judge even if unambiguous"),
    as_json: bool = typer.Option(False, "--json"),
    config: str | None = _config_option,
    fixtures: str | None = _fixtures_option,
) -> None:
    """Decide 1080p vs 2160p for a title."""
    _, _, engine, store = _build(config, fixtures)
    request = DecisionRequest(
        title=title,
        year=year,
        seasons=season,
        requester=requester,
        tmdb_id=tmdb_id,
        trigger=TriggerSource.MANUAL_CLI,
        force_judge=force_judge,
    )
    decision = engine.decide(request, mode)
    store.save_decision(decision)
    _print_decision(decision, as_json)


@app.command("plan-seerr")
def plan_seerr(
    seerr_request_id: int = typer.Option(..., "--seerr-request-id"),
    as_json: bool = typer.Option(False, "--json"),
    config: str | None = _config_option,
) -> None:
    """Reconstruct a Seerr request and produce its decision/action plan (no writes)."""
    _, _, engine, store = _build(config, None)
    request = DecisionRequest(
        seerr_request_id=seerr_request_id, trigger=TriggerSource.MANUAL_CLI
    )
    decision = engine.decide(request, None)
    store.save_decision(decision)
    _print_decision(decision, as_json)


@app.command("audit-sonarr")
def audit_sonarr(
    decision_id: str = typer.Option(..., "--decision-id"),
    config: str | None = _config_option,
) -> None:
    """Check whether the decided profile actually landed in Sonarr."""
    from .sonarr.audit import audit_series_profile

    settings, _, engine, store = _build(config, None)
    decision = store.get_decision(decision_id)
    if decision is None:
        typer.echo("decision not found", err=True)
        raise typer.Exit(1)
    tvdb_id = decision.evidence.facts.tvdb_id
    if tvdb_id is None:
        typer.echo("decision has no tvdb id to audit", err=True)
        raise typer.Exit(1)
    evidence = engine.evidence_source.collect(DecisionRequest(tvdb_id=tvdb_id))
    result = audit_series_profile(
        evidence.sonarr,
        decision.final_resolution,
        profile_name_1080p=settings.seerr.profile_name_1080p,
        profile_name_2160p=settings.seerr.profile_name_2160p,
        tvdb_id=tvdb_id,
    )
    store.save_audit(result.model_dump(), decision_id=decision_id)
    typer.echo(json.dumps(result.model_dump(), indent=2))


@app.command()
def execute(
    decision_id: str = typer.Argument(help="Decision id, or 'last' for the most recent"),
    operator: str = typer.Option(..., "--operator", help="Who is approving this execution"),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt"),
    config: str | None = _config_option,
) -> None:
    """Execute a stored decision's action plan (operator-approved writes to Seerr/Sonarr).

    Respects the decision's automation mode and all write gates: shadow/recommend
    decisions execute nothing, and allow_writes must be true for any write.
    """
    from .executor import ExecutionBlocked, ExecutionFailed
    from .runtime import build_runtime

    rt = build_runtime(config)
    if decision_id == "last":
        last = rt.store.last_decision()
        if last is None:
            typer.echo("no decisions recorded yet", err=True)
            raise typer.Exit(1)
        decision_id = last.decision_id
    decision = rt.store.get_decision(decision_id)
    if decision is None:
        typer.echo("decision not found", err=True)
        raise typer.Exit(1)

    _print_decision(decision, False)
    writes = [a for a in decision.action_plan if a.is_write]
    if writes and not yes:
        typer.confirm(
            f"Execute {len(writes)} write action(s) as {operator}?", abort=True
        )
    try:
        executed = rt.executor.execute(decision, operator_approved=True)
    except ExecutionBlocked as exc:
        typer.echo(f"blocked: {exc}", err=True)
        raise typer.Exit(1) from exc
    except ExecutionFailed as exc:
        partial = [a.value for a in exc.executed]
        if partial:
            rt.store.mark_executed(decision_id, partial, operator=f"{operator} (partial)")
        typer.echo(f"failed after {partial or 'no actions'}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if executed:
        rt.store.mark_executed(decision_id, [a.value for a in executed], operator=operator)
        typer.echo(f"executed: {[a.value for a in executed]}")
    else:
        typer.echo("nothing executed (mode/plan permits no writes)")


@app.command()
def preflight(config: str | None = _config_option) -> None:
    """Live contract check against Seerr/Sonarr: connectivity, profile resolution,
    pending-request visibility. Read-only; run before enabling any write mode."""
    from .runtime import build_runtime

    rt = build_runtime(config)
    failures = 0

    def check(name: str, fn) -> None:
        nonlocal failures
        try:
            typer.echo(f"[ok]   {name}: {fn()}")
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            failures += 1
            typer.echo(f"[fail] {name}: {exc}")

    check(
        "seerr sonarr servers",
        lambda: [s.get("name") for s in rt.seerr.list_sonarr_servers()],
    )
    for profile_name in (
        rt.settings.seerr.profile_name_1080p,
        rt.settings.seerr.profile_name_2160p,
    ):
        check(
            f"resolve profile '{profile_name}'",
            lambda name=profile_name: rt.seerr.resolve_profile_id(name),
        )
    check(
        "pending TV requests visible",
        lambda: sum(
            1
            for r in rt.seerr.list_requests(filter="pending")
            if (r.get("media") or {}).get("mediaType") == "tv"
        ),
    )
    if rt.sonarr is not None:
        check(
            "sonarr quality profiles",
            lambda: [p.get("name") for p in rt.sonarr.list_quality_profiles()],
        )
    else:
        typer.echo("[skip] sonarr: not configured (shadow deltas and audits disabled)")
    typer.echo(
        f"mode={rt.settings.mode} allow_writes={rt.settings.allow_writes}"
        f" auto_approve_enabled={rt.settings.auto_approve_enabled}"
    )
    if failures:
        typer.echo(f"{failures} check(s) failed", err=True)
        raise typer.Exit(1)
    typer.echo("preflight passed")


@app.command("review-pending")
def review_pending(
    limit: int = typer.Option(20),
    remote: str | None = typer.Option(
        None,
        help="Base URL of a running resolute API to review through, instead of "
        "opening the local store. For schedulers/cronjobs: the API pod owns "
        "the single-writer SQLite store, so out-of-pod runs must go through "
        "it. Sends X-Resolute-Api-Token from $RESOLUTE_API_TOKEN when set.",
    ),
    config: str | None = _config_option,
) -> None:
    """Scheduled review: decide every pending Seerr TV request (shadow-safe)."""
    if remote is not None:
        import os

        import httpx

        headers = {}
        token = os.environ.get("RESOLUTE_API_TOKEN")
        if token:
            headers["X-Resolute-Api-Token"] = token
        try:
            resp = httpx.post(
                f"{remote.rstrip('/')}/api/reviews/pending",
                params={"limit": limit},
                headers=headers,
                timeout=300,
            )
        except httpx.HTTPError as exc:
            # Sanitized by construction: URL carries no credentials.
            typer.echo(f"remote review failed: {type(exc).__name__}", err=True)
            raise typer.Exit(1) from None
        if resp.status_code != 200:
            typer.echo(
                f"remote review failed: HTTP {resp.status_code}: {resp.text}",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(resp.text)
        return

    from .metadata.source import seerr_request_state_from_api
    from .runtime import build_runtime

    rt = build_runtime(config)
    pending = rt.seerr.list_requests(filter="pending", take=limit)
    reviewed = 0
    for req in pending:
        media = req.get("media") or {}
        if media.get("mediaType") != "tv":
            continue
        state = seerr_request_state_from_api(req)
        request = DecisionRequest(
            seerr_request_id=state.request_id,
            tmdb_id=media.get("tmdbId"),
            tvdb_id=media.get("tvdbId"),
            trigger=TriggerSource.SCHEDULED_REVIEW,
        )
        decision = rt.engine.decide(request, None)
        rt.store.save_decision(decision)
        typer.echo(
            f"request {state.request_id}: {decision.title} -> "
            f"{decision.final_resolution} ({decision.confidence})"
        )
        reviewed += 1
    typer.echo(f"reviewed {reviewed} pending TV request(s)")


@app.command("audit-library")
def audit_library(
    limit: int = typer.Option(0, help="Max series to audit (0 = all)"),
    config: str | None = _config_option,
) -> None:
    """Scheduled shadow audit: compare recommendations against the whole Sonarr library."""
    from .runtime import build_runtime

    rt = build_runtime(config)
    if rt.sonarr is None:
        typer.echo("sonarr is not configured", err=True)
        raise typer.Exit(1)
    series_list = rt.sonarr.list_series()
    if limit:
        series_list = series_list[:limit]
    mismatches = 0
    for series in series_list:
        tvdb_id = series.get("tvdbId")
        if not tvdb_id:
            continue
        request = DecisionRequest(
            title=series.get("title"),
            tvdb_id=tvdb_id,
            trigger=TriggerSource.SCHEDULED_REVIEW,
        )
        decision = rt.engine.decide(request, AutomationMode.SHADOW)
        rt.store.save_decision(decision)
        if decision.shadow_delta and decision.shadow_delta.startswith("mismatch"):
            mismatches += 1
            typer.echo(f"{decision.title}: {decision.shadow_delta}")
    typer.echo(f"audited {len(series_list)} series, {mismatches} mismatch(es)")


@app.command()
def feedback(
    decision_id: str = typer.Argument(help="Decision id, or 'last' for the most recent"),
    verdict: FeedbackVerdict = typer.Argument(),
    reason_tag: str | None = typer.Option(None, "--reason-tag"),
    comment: str | None = typer.Option(None),
    config: str | None = _config_option,
    fixtures: str | None = _fixtures_option,
) -> None:
    """Record household feedback on a decision (e.g. `feedback last prefer_2160p --reason-tag showcase`)."""
    _, policy, _, store = _build(config, fixtures)
    if decision_id == "last":
        last = store.last_decision()
        if last is None:
            typer.echo("no decisions recorded yet", err=True)
            raise typer.Exit(1)
        decision_id = last.decision_id
    if reason_tag and reason_tag not in policy.feedback_reason_tags:
        typer.echo(
            f"unknown reason tag '{reason_tag}'; allowed: {policy.feedback_reason_tags}",
            err=True,
        )
        raise typer.Exit(1)
    record = store.save_feedback(
        FeedbackIn(
            decision_id=decision_id,
            verdict=verdict,
            reason_tag=reason_tag,
            comment=comment,
            source="cli",
        )
    )
    typer.echo(f"recorded feedback {record.feedback_id} on decision {decision_id}")


@app.command()
def calibrate(
    config: str | None = _config_option,
    fixtures: str | None = _fixtures_option,
) -> None:
    """Print the calibration summary (decision mix, agreement rate, override clusters)."""
    _, _, _, store = _build(config, fixtures)
    typer.echo(json.dumps(store.calibration_summary(), indent=2))


@app.command("review-overrides")
def review_overrides(
    limit: int = typer.Option(50),
    config: str | None = _config_option,
    fixtures: str | None = _fixtures_option,
) -> None:
    """List decisions the household disagreed with, newest first."""
    _, _, _, store = _build(config, fixtures)
    rows = store.overrides(limit=limit)
    if not rows:
        typer.echo("no overrides recorded")
        return
    for row in rows:
        typer.echo(
            f"{row['created_at']}  {row['title']}: decided {row['final_resolution']}"
            f" ({row['confidence']}), household said {row['verdict']}"
            f" [{row['reason_tag'] or '-'}] {row['comment'] or ''}"
        )


@app.command("fixtures-test")
def fixtures_test(
    fixtures: str = typer.Option("fixtures/evidence", "--fixtures"),
    golden: str = typer.Option("fixtures/golden/expectations.json", "--golden"),
    config: str | None = _config_option,
) -> None:
    """Run golden expectations against fixture evidence. Exit 1 on any mismatch."""
    settings = load_settings(config)
    policy = load_policy(settings.policy_path)
    engine = DecisionEngine(settings, policy, FixtureEvidenceSource(fixtures))
    cases = json.loads(Path(golden).read_text())
    failures = 0
    for case in cases:
        request = DecisionRequest(**case["request"])
        decision = engine.decide(request, AutomationMode.SHADOW)
        expected = Resolution(case["expected_resolution"])
        expect_hold = bool(case.get("expect_hold", False))
        held = any("hold" in a.type or a.type == "insufficient_metadata" for a in decision.action_plan)
        ok = decision.final_resolution is expected and held == expect_hold
        status = "PASS" if ok else "FAIL"
        typer.echo(
            f"[{status}] {case.get('name', request.identity_hint())}: "
            f"got {decision.final_resolution} hold={held}, "
            f"want {expected} hold={expect_hold}"
        )
        failures += 0 if ok else 1
    typer.echo(f"{len(cases) - failures}/{len(cases)} golden cases passed")
    if failures:
        raise typer.Exit(1)


@app.command("export-jsonl")
def export_jsonl(
    out: str = typer.Option("data/decisions.jsonl", "--out"),
    config: str | None = _config_option,
    fixtures: str | None = _fixtures_option,
) -> None:
    """Export all decisions as append-only JSONL for backup/portability."""
    _, _, _, store = _build(config, fixtures)
    count = store.export_jsonl(out)
    typer.echo(f"exported {count} decision(s) to {out}")


@app.command()
def serve(
    host: str | None = typer.Option(None),
    port: int | None = typer.Option(None),
    config: str | None = _config_option,
) -> None:
    """Run the HTTP API."""
    import asyncio

    import uvicorn

    from .runtime import build_runtime
    from .api.app import create_app, create_metrics_app

    rt = build_runtime(config)
    api = create_app(rt.settings, rt.policy, rt.engine, rt.store, rt.executor, rt.seerr)
    bind_host = host or rt.settings.listen_host
    log_level = rt.settings.log_level.lower()
    configs = [
        uvicorn.Config(
            api, host=bind_host, port=port or rt.settings.listen_port, log_level=log_level
        )
    ]
    # Metrics on a dedicated listener (org convention: 8081, off the main port).
    if rt.settings.metrics_enabled:
        metrics_api = create_metrics_app(api.state.metrics)
        configs.append(
            uvicorn.Config(
                metrics_api,
                host=bind_host,
                port=rt.settings.metrics_port,
                log_level=log_level,
            )
        )
    servers = [uvicorn.Server(c) for c in configs]

    async def _run() -> None:
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(_run())


if __name__ == "__main__":
    app()
