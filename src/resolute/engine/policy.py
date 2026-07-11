"""Deterministic policy pre-score: objective and household lanes."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Policy
from ..schemas import Confidence, Recommendation, Resolution, ScoreComponent
from .features import FeatureSet

_MODERN_ERA_YEAR = 2016
_PRE_HD_YEAR = 2005


@dataclass
class PreScore:
    objective: Recommendation
    household: Recommendation
    score: float  # household lane; drives thresholds and the ambiguity band
    objective_score: float = 0.0  # objective lane only (ADR-0002 worth endpoint)
    components: list[ScoreComponent] = field(default_factory=list)
    ambiguous: bool = False


def _confidence(score: float, policy: Policy) -> Confidence:
    t = policy.thresholds
    if t.hd_score < score < t.uhd_score:
        return Confidence.LOW
    distance = score - t.uhd_score if score >= t.uhd_score else t.hd_score - score
    return Confidence.HIGH if distance >= t.high_confidence_margin else Confidence.MEDIUM


def _resolution(score: float, policy: Policy) -> Resolution:
    # Inside the ambiguous band, lean 1080p: cheaper to upgrade later than to rework.
    return Resolution.P2160 if score >= policy.thresholds.uhd_score else Resolution.P1080


def _objective_components(f: FeatureSet, policy: Policy) -> list[ScoreComponent]:
    w = policy.weights
    parts: list[ScoreComponent] = []

    if f.matches_visual_genre:
        parts.append(
            ScoreComponent(
                name="visual_genre",
                contribution=w.visual_genre,
                note="genre/keywords suggest strong visual payoff",
            )
        )
    if f.matches_low_payoff_genre:
        parts.append(
            ScoreComponent(
                name="low_payoff_genre",
                contribution=-w.visual_genre,
                note="genre is typically story-led with limited 4K payoff",
            )
        )
    if f.matches_premium_network:
        parts.append(
            ScoreComponent(
                name="network_tier",
                contribution=w.network_tier,
                note="premium network/platform production values",
            )
        )
    if f.year is not None:
        if f.year >= _MODERN_ERA_YEAR:
            parts.append(
                ScoreComponent(
                    name="era",
                    contribution=0.5 * w.era,
                    note="modern production, native UHD masters likely",
                )
            )
        elif f.year < _PRE_HD_YEAR:
            parts.append(
                ScoreComponent(
                    name="era",
                    contribution=-w.era,
                    note="pre-HD era source, 2160p unlikely to add value without remaster",
                )
            )
    if f.vote_average is not None:
        if f.vote_average >= 8.0 and (f.vote_count or 0) >= 100:
            parts.append(
                ScoreComponent(
                    name="acclaim",
                    contribution=w.acclaim,
                    note="widely acclaimed title",
                )
            )
        elif f.vote_average < 6.0:
            parts.append(
                ScoreComponent(
                    name="acclaim",
                    contribution=-0.5 * w.acclaim,
                    note="weak ratings reduce showcase value",
                )
            )
    return parts


def _household_components(f: FeatureSet, policy: Policy) -> list[ScoreComponent]:
    w = policy.weights
    parts: list[ScoreComponent] = []

    if f.pinned_2160p_franchise:
        parts.append(
            ScoreComponent(
                name="franchise_priority",
                contribution=w.franchise_priority,
                note=f"household priority franchise: {f.pinned_2160p_franchise}",
            )
        )
    if f.pinned_1080p_title:
        parts.append(
            ScoreComponent(
                name="title_override_1080p",
                contribution=-w.franchise_priority,
                note=f"household 1080p-default title: {f.pinned_1080p_title}",
            )
        )
    if f.requester and f.requester in policy.requesters:
        bias = policy.requesters[f.requester].bias_2160p
        if bias:
            parts.append(
                ScoreComponent(
                    name="requester_preference",
                    contribution=bias * w.requester_preference,
                    note=f"requester preference for {f.requester}",
                )
            )
    if f.requested_episodes:
        if f.requested_episodes > policy.max_episodes_2160p:
            parts.append(
                ScoreComponent(
                    name="episode_burden",
                    contribution=-w.episode_burden,
                    note=f"{f.requested_episodes} episodes make 2160p storage-expensive",
                )
            )
        elif f.requested_episodes <= 13:
            parts.append(
                ScoreComponent(
                    name="episode_burden",
                    contribution=0.3 * w.episode_burden,
                    note="short season keeps storage impact bounded",
                )
            )
    if policy.storage_pressure == "high":
        parts.append(
            ScoreComponent(
                name="storage_pressure",
                contribution=-w.storage_pressure,
                note="library storage pressure is high",
            )
        )
    elif policy.storage_pressure == "medium":
        parts.append(
            ScoreComponent(
                name="storage_pressure",
                contribution=-0.5 * w.storage_pressure,
                note="library storage pressure is elevated",
            )
        )
    return parts


def prescore(features: FeatureSet, policy: Policy) -> PreScore:
    objective_parts = _objective_components(features, policy)
    household_parts = _household_components(features, policy)

    objective_score = sum(p.contribution for p in objective_parts)
    household_score = objective_score + sum(p.contribution for p in household_parts)

    def reasons(parts: list[ScoreComponent]) -> list[str]:
        ranked = sorted(parts, key=lambda p: abs(p.contribution), reverse=True)
        return [p.note for p in ranked[:4]]

    objective = Recommendation(
        resolution=_resolution(objective_score, policy),
        confidence=_confidence(objective_score, policy),
        reasons=reasons(objective_parts) or ["no strong objective signals"],
    )
    household = Recommendation(
        resolution=_resolution(household_score, policy),
        confidence=_confidence(household_score, policy),
        reasons=reasons(objective_parts + household_parts) or ["no strong household signals"],
    )
    t = policy.thresholds
    return PreScore(
        objective=objective,
        household=household,
        score=round(household_score, 3),
        objective_score=round(objective_score, 3),
        components=objective_parts + household_parts,
        ambiguous=t.hd_score < household_score < t.uhd_score,
    )
