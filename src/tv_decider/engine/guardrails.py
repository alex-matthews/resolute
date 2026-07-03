"""Deterministic guardrails: the last word after policy scoring and any model verdict.

The judge may inform the final call inside the ambiguous band; it may never
override hard policy pins, unlock writes, or upgrade past deterministic caps.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Policy
from ..schemas import Confidence, ModelVerdict, Resolution
from .features import FeatureSet
from .policy import PreScore

# Missing all of these makes a decision untrustworthy.
_CRITICAL_GAPS = {"title", "genres"}


@dataclass
class GuardrailResult:
    resolution: Resolution
    confidence: Confidence
    hold_for_review: bool = False
    insufficient_metadata: bool = False
    risk_flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def apply_guardrails(
    features: FeatureSet,
    pre: PreScore,
    verdict: ModelVerdict | None,
    policy: Policy,
) -> GuardrailResult:
    result = GuardrailResult(
        resolution=pre.household.resolution,
        confidence=pre.household.confidence,
    )

    # 1. Metadata floor: refuse to decide from nothing.
    if _CRITICAL_GAPS.issubset(set(features.metadata_gaps)):
        result.insufficient_metadata = True
        result.hold_for_review = True
        result.resolution = Resolution.P1080
        result.confidence = Confidence.LOW
        result.risk_flags.append("insufficient_metadata")
        result.notes.append("critical metadata missing; defaulting safe and holding")
        return result
    if features.metadata_gaps:
        result.risk_flags.append("metadata_gap")

    # 2. Hard household pins beat everything, including the judge.
    pinned: Resolution | None = None
    if features.pinned_1080p_title:
        pinned = Resolution.P1080
        result.notes.append(f"pinned 1080p by policy title override: {features.pinned_1080p_title}")
    elif features.pinned_2160p_franchise:
        pinned = Resolution.P2160
        result.notes.append(
            f"pinned 2160p by policy franchise priority: {features.pinned_2160p_franchise}"
        )
    if pinned is not None:
        result.resolution = pinned
        result.confidence = Confidence.HIGH

    # 3. Judge verdict: advisory inside the ambiguous band, never above pins.
    if verdict is not None:
        result.risk_flags.extend(f for f in verdict.risk_flags if f not in result.risk_flags)
        if pinned is not None and verdict.automation.resolution != pinned:
            result.risk_flags.append("judge_conflict_pinned")
            result.notes.append("judge disagreed with policy pin; pin kept")
        elif pinned is None:
            if pre.ambiguous:
                result.resolution = verdict.automation.resolution
                result.confidence = min(
                    verdict.automation.confidence,
                    Confidence.MEDIUM,
                    key=[Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH].index,
                )
                result.notes.append("ambiguous band resolved by model judge")
            elif verdict.automation.resolution != pre.household.resolution:
                # Outside the band the judge cannot flip, only lower confidence.
                result.risk_flags.append("judge_disagrees_deterministic")
                result.confidence = Confidence.MEDIUM
        if verdict.automation.action in (
            "hold_for_manual_review",
            "hold_seerr_request_for_manual_review",
            "insufficient_metadata",
        ):
            result.hold_for_review = True
            result.notes.append("judge requested manual review")

    # 4. Episode-burden cap: big 2160p commitments need high confidence.
    if (
        result.resolution is Resolution.P2160
        and pinned is None
        and features.requested_episodes
        and features.requested_episodes > policy.max_episodes_2160p
        and result.confidence is not Confidence.HIGH
    ):
        result.resolution = Resolution.P1080
        result.risk_flags.append("episode_burden_cap")
        result.notes.append(
            f"downgraded to 1080p: {features.requested_episodes} episodes exceeds "
            f"cap of {policy.max_episodes_2160p} without high confidence"
        )

    # 5. Storage pressure: high pressure blocks non-pinned 2160p at medium confidence.
    if (
        result.resolution is Resolution.P2160
        and pinned is None
        and policy.storage_pressure == "high"
        and result.confidence is not Confidence.HIGH
    ):
        result.resolution = Resolution.P1080
        result.risk_flags.append("storage_pressure_block")
        result.notes.append("downgraded to 1080p under high storage pressure")

    # 6. Unresolved ambiguity (no judge, or judge failed) goes to a human.
    if pre.ambiguous and verdict is None and pinned is None:
        result.hold_for_review = True
        result.risk_flags.append("near_threshold")

    # 7. Low confidence never auto-executes.
    if result.confidence is Confidence.LOW:
        result.hold_for_review = True
        if "low_confidence" not in result.risk_flags:
            result.risk_flags.append("low_confidence")

    return result
