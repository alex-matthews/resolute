"""Decision engine orchestrator: the one pipeline shared by CLI, API, and
webhooks (ADR-0003, LLM-primary):

    evidence -> metadata floor -> model verdict (strict, one retry)
             -> hard rails -> action plan -> Decision

The model is the decision-maker; the deterministic layer is only the safety
envelope in engine/rails.py. With the model disabled or failing, every
normal decision degrades to the conservative fallback (1080p + hold).
"""

from __future__ import annotations

import logging

from ..config import HouseholdPolicy, Settings
from ..ids import new_id
from ..judge.judge import Judge
from ..metadata.source import EvidenceSource
from ..schemas import (
    AutomationMode,
    Confidence,
    Decision,
    DecisionRequest,
    ModelInvolvement,
    Recommendation,
    Resolution,
)
from ..seerr.planner import build_action_plan, shadow_delta
from .rails import apply_rails, conservative_fallback, metadata_floor, metadata_gaps

logger = logging.getLogger(__name__)

_FALLBACK_REASONS = ["model unavailable; conservative 1080p default"]


class DecisionEngine:
    def __init__(
        self,
        settings: Settings,
        household: HouseholdPolicy,
        evidence_source: EvidenceSource,
        judge: Judge | None = None,
    ) -> None:
        self.settings = settings
        self.household = household
        self.evidence_source = evidence_source
        self.judge = judge

    def decide(self, request: DecisionRequest, mode: AutomationMode | None = None) -> Decision:
        mode = mode or self.settings.mode
        evidence = self.evidence_source.collect(request)
        gaps = metadata_gaps(evidence)

        verdict = None
        involvement = ModelInvolvement(used=False)

        result = metadata_floor(gaps)
        if result is None:
            if self.judge is None:
                result = conservative_fallback("model disabled in settings")
            else:
                verdict, involvement = self.judge.judge_request(evidence, self.household)
                if verdict is not None:
                    result = apply_rails(verdict, gaps)
                else:
                    logger.warning(
                        "model unavailable/invalid for %s; conservative fallback",
                        request.identity_hint(),
                    )
                    result = conservative_fallback(involvement.error or "no valid model output")

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

        if verdict is not None:
            objective = Recommendation(
                resolution=verdict.objective.resolution,
                confidence=verdict.objective.confidence,
                reasons=verdict.objective.reasons,
            )
            household_rec = Recommendation(
                resolution=verdict.household.resolution,
                confidence=verdict.household.confidence,
                reasons=verdict.household.reasons,
            )
            top_reasons = list(dict.fromkeys(verdict.household.reasons + result.notes))[:5]
        else:
            fallback_rec = Recommendation(
                resolution=Resolution.P1080,
                confidence=Confidence.LOW,
                reasons=list(_FALLBACK_REASONS),
            )
            objective = fallback_rec
            household_rec = fallback_rec.model_copy(deep=True)
            top_reasons = list(dict.fromkeys(result.notes))[:5]

        return Decision(
            decision_id=new_id(),
            request=request,
            evidence=evidence,
            title=evidence.facts.canonical_title,
            year=evidence.facts.year,
            seasons=request.seasons or evidence.seerr_request.requested_seasons,
            trigger=request.trigger,
            mode=mode,
            objective=objective,
            household=household_rec,
            final_resolution=result.resolution,
            confidence=result.confidence,
            top_reasons=top_reasons,
            risk_flags=result.risk_flags,
            metadata_gaps=gaps,
            model_involvement=involvement,
            verdict=verdict,
            action_plan=actions,
            shadow_delta=delta,
        )
