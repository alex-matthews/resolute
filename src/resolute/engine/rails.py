"""ADR-0003's hard safety envelope. Deliberately small: these are failure
fallbacks and evidence preconditions, not a second decision engine.

The five rails, and where each lives:

1. Closed choice set — the model picks only among trusted profiles and
   constrained action types (the ModelVerdict schema enums).
2. Strict validation, one retry — the judge module; a second invalid result
   or an unavailable provider yields `conservative_fallback` here.
3. Pending-state protection — the planner emits Seerr writes only for
   pending requests and the executor re-reads pending state at execution
   time (ADR-0001).
4. Executor authority — allow_writes, the mode matrix, approval gates, and
   plan ordering govern regardless of model output (executor.py).
5. Bounded blast radius — 1+3+4 together: untrusted evidence can at worst
   pick a trusted profile or cause a hold.

Plus one evidence precondition retained on its own merits (ADR-0003): the
metadata floor. With both title and genre evidence absent there is nothing
trustworthy to judge, so the case holds without spending a model call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import Confidence, EvidenceBundle, ModelVerdict, Resolution

_HOLD_ACTIONS = {
    "hold_for_manual_review",
    "hold_seerr_request_for_manual_review",
    "insufficient_metadata",
}
_CRITICAL_GAPS = {"title", "genres"}


@dataclass
class RailsResult:
    """What the stack should do; consumed by the planner."""

    resolution: Resolution
    confidence: Confidence
    hold_for_review: bool = False
    insufficient_metadata: bool = False
    risk_flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def metadata_gaps(evidence: EvidenceBundle) -> list[str]:
    """Named missing-evidence markers. Only title+genres are load-bearing
    (the floor); the rest are informational for audit and the model."""
    gaps = list(evidence.gaps)
    facts = evidence.facts
    if not (facts.canonical_title or "").strip():
        gaps.append("title")
    if not facts.genres:
        gaps.append("genres")
    if not facts.networks:
        gaps.append("networks")
    if facts.vote_average is None:
        gaps.append("ratings")
    if not facts.number_of_episodes:
        gaps.append("episode_count")
    return list(dict.fromkeys(gaps))


def metadata_floor(gaps: list[str]) -> RailsResult | None:
    """Evidence precondition: both title AND genres absent -> hold without
    invoking the model. Missing genres alone does not block judgment."""
    if not _CRITICAL_GAPS.issubset(set(gaps)):
        return None
    return RailsResult(
        resolution=Resolution.P1080,
        confidence=Confidence.LOW,
        hold_for_review=True,
        insufficient_metadata=True,
        risk_flags=["insufficient_metadata"],
        notes=["critical metadata missing; defaulting safe and holding"],
    )


def conservative_fallback(reason: str) -> RailsResult:
    """Model unavailable, disabled, or twice-invalid: recommend 1080p and
    hold for review; never write. Degraded operation is the design (ADR-0003)
    — there is no deterministic engine to fall back to."""
    return RailsResult(
        resolution=Resolution.P1080,
        confidence=Confidence.LOW,
        hold_for_review=True,
        risk_flags=["model_unavailable"],
        notes=[f"conservative fallback (1080p + hold): {reason}"],
    )


def apply_rails(verdict: ModelVerdict, gaps: list[str]) -> RailsResult:
    """Turn a schema-valid verdict into the stack's intent. The schema already
    constrained the choice set; what remains is honoring the model's own
    request for review and surfacing evidence quality."""
    result = RailsResult(
        resolution=verdict.automation.resolution,
        confidence=verdict.automation.confidence,
        risk_flags=list(dict.fromkeys(verdict.risk_flags)),
    )
    if gaps and "metadata_gap" not in result.risk_flags:
        result.risk_flags.append("metadata_gap")
    if verdict.automation.action in _HOLD_ACTIONS:
        result.hold_for_review = True
        result.notes.append("model requested manual review")
    if result.confidence is Confidence.LOW and not result.hold_for_review:
        # Mirrors the executor's low-confidence write refusal so the plan
        # says what will actually happen.
        result.hold_for_review = True
        if "low_confidence" not in result.risk_flags:
            result.risk_flags.append("low_confidence")
    return result
