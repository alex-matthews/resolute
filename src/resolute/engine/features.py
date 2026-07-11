"""Deterministic feature extraction from an evidence bundle."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import Policy
from ..schemas import DecisionRequest, EvidenceBundle


@dataclass
class FeatureSet:
    """Flat, engine-facing view of the evidence. No scoring happens here."""

    title: str | None = None
    year: int | None = None
    genres: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    requested_episodes: int | None = None  # episodes in scope of this request
    total_episodes: int | None = None
    runtime_minutes: int | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    requester: str | None = None
    matches_visual_genre: bool = False
    matches_low_payoff_genre: bool = False
    matches_premium_network: bool = False
    pinned_2160p_franchise: str | None = None
    pinned_1080p_title: str | None = None
    estimated_season_gb_2160p: float | None = None
    metadata_gaps: list[str] = field(default_factory=list)


# Rough planning numbers for storage impact notes (not precise accounting).
_GB_PER_EPISODE_HOUR_2160P = 12.0


def _norm(values: list[str]) -> list[str]:
    return [v.strip().lower() for v in values if v and v.strip()]


def _contains_term(item: str, term: str) -> bool:
    """Whole-word/phrase containment: 'dune' matches 'dune: prophecy' but not
    'dunedin', and 'max' matches 'hbo max' but not 'cinemax'."""
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", item) is not None


def _contains_any(haystack: list[str], needles: list[str]) -> str | None:
    for needle in _norm(needles):
        for item in haystack:
            if _contains_term(item, needle):
                return needle
    return None


def extract_features(
    request: DecisionRequest, evidence: EvidenceBundle, policy: Policy
) -> FeatureSet:
    facts = evidence.facts
    genres = _norm(facts.genres)
    networks = _norm(facts.networks)
    keywords = _norm(facts.keywords)

    total_episodes = facts.number_of_episodes
    requested_episodes = total_episodes
    if request.seasons and facts.number_of_seasons and total_episodes:
        per_season = max(1, total_episodes // max(1, facts.number_of_seasons))
        requested_episodes = per_season * len(request.seasons)
    elif evidence.seerr_request.requested_seasons and facts.number_of_seasons and total_episodes:
        per_season = max(1, total_episodes // max(1, facts.number_of_seasons))
        requested_episodes = per_season * len(evidence.seerr_request.requested_seasons)

    title = facts.canonical_title or request.title
    title_lower = (title or "").lower()
    # Genre signal comes from genres/keywords only: a title *containing* a genre
    # word ("Animation Domination") is not evidence of that genre. Pins are the
    # deliberate title-matching mechanism.
    genre_haystack = genres + keywords

    features = FeatureSet(
        title=title,
        year=facts.year or request.year,
        genres=genres,
        networks=networks,
        keywords=keywords,
        requested_episodes=requested_episodes,
        total_episodes=total_episodes,
        runtime_minutes=facts.episode_run_time_minutes,
        vote_average=facts.vote_average,
        vote_count=facts.vote_count,
        requester=request.requester or evidence.seerr_request.requested_by,
        matches_visual_genre=_contains_any(genre_haystack, policy.visual_genres) is not None,
        matches_low_payoff_genre=_contains_any(genres, policy.low_payoff_genres) is not None,
        matches_premium_network=_contains_any(networks, policy.premium_networks) is not None,
        pinned_2160p_franchise=_contains_any([title_lower], policy.franchises_2160p),
        pinned_1080p_title=_contains_any([title_lower], policy.titles_1080p),
        metadata_gaps=list(evidence.gaps),
    )

    if requested_episodes and facts.episode_run_time_minutes:
        hours = requested_episodes * facts.episode_run_time_minutes / 60
        features.estimated_season_gb_2160p = round(hours * _GB_PER_EPISODE_HOUR_2160P, 1)

    if not title:
        features.metadata_gaps.append("title")
    if not genres:
        features.metadata_gaps.append("genres")
    if not networks:
        features.metadata_gaps.append("networks")
    if facts.vote_average is None:
        features.metadata_gaps.append("ratings")
    if not requested_episodes:
        features.metadata_gaps.append("episode_count")
    # de-duplicate, stable order
    features.metadata_gaps = list(dict.fromkeys(features.metadata_gaps))
    return features
