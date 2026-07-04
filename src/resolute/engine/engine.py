"""Decision engine orchestrator: the one pipeline shared by CLI, API, and webhooks.

    evidence -> features -> deterministic pre-score
             -> optional LLM judge (ambiguous band or forced)
             -> guardrails -> action plan -> Decision
"""

from __future__ import annotations

import logging

from ..config import Policy, Settings
from ..ids import new_id
from ..judge.judge import Judge
from ..metadata.source import EvidenceSource
from ..schemas import (
    AutomationMode,
    Decision,
    DecisionRequest,
    ModelInvolvement,
)
from ..seerr.planner import build_action_plan, shadow_delta
from .features import extract_features
from .guardrails import apply_guardrails
from .policy import prescore

logger = logging.getLogger(__name__)


class DecisionEngine:
    def __init__(
        self,
        settings: Settings,
        policy: Policy,
        evidence_source: EvidenceSource,
        judge: Judge | None = None,
    ) -> None:
        self.settings = settings
        self.policy = policy
        self.evidence_source = evidence_source
        self.judge = judge

    def decide(self, request: DecisionRequest, mode: AutomationMode | None = None) -> Decision:
        mode = mode or self.settings.mode
        evidence = self.evidence_source.collect(request)
        features = extract_features(request, evidence, self.policy)
        pre = prescore(features, self.policy)

        verdict = None
        involvement = ModelInvolvement(used=False)
        should_judge = self.judge is not None and (
            request.force_judge
            or pre.ambiguous
            or not self.settings.judge.judge_ambiguous_only
        )
        if should_judge and self.judge is not None:
            verdict, involvement = self.judge.judge(evidence, pre, self.policy)
            if verdict is None:
                logger.warning(
                    "judge unavailable/invalid for %s; falling back to deterministic result",
                    request.identity_hint(),
                )

        result = apply_guardrails(features, pre, verdict, self.policy)
        if involvement.used and verdict is None and "model_error" not in result.risk_flags:
            result.risk_flags.append("model_error")

        writes_possible = self.settings.allow_writes
        auto_profile_allowed = writes_possible and mode in (
            AutomationMode.AUTO_PROFILE,
            AutomationMode.AUTO_APPROVE,
        )
        auto_approve_allowed = (
            writes_possible
            and mode is AutomationMode.AUTO_APPROVE
            and self.settings.auto_approve_enabled
        )
        actions = build_action_plan(
            result,
            evidence,
            profile_name_1080p=self.settings.seerr.profile_name_1080p,
            profile_name_2160p=self.settings.seerr.profile_name_2160p,
            auto_profile_allowed=auto_profile_allowed,
            auto_approve_allowed=auto_approve_allowed,
        )
        delta = shadow_delta(
            result,
            evidence,
            profile_name_1080p=self.settings.seerr.profile_name_1080p,
            profile_name_2160p=self.settings.seerr.profile_name_2160p,
        )

        top_reasons = list(
            dict.fromkeys(
                (verdict.household.reasons if verdict else [])
                + pre.household.reasons
                + result.notes
            )
        )[:5]

        return Decision(
            decision_id=new_id(),
            request=request,
            evidence=evidence,
            title=features.title,
            year=features.year,
            seasons=request.seasons or evidence.seerr_request.requested_seasons,
            trigger=request.trigger,
            mode=mode,
            objective=pre.objective,
            household=pre.household,
            final_resolution=result.resolution,
            confidence=result.confidence,
            score=pre.score,
            score_components=pre.components,
            top_reasons=top_reasons,
            risk_flags=result.risk_flags,
            metadata_gaps=features.metadata_gaps,
            model_involvement=involvement,
            verdict=verdict,
            action_plan=actions,
            shadow_delta=delta,
        )
