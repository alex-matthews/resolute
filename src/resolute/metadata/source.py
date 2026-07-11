"""Evidence sources: turn a canonical request into an EvidenceBundle.

LiveEvidenceSource talks to Seerr (TMDB proxy + request state) and Sonarr
(existing series state). FixtureEvidenceSource serves tests and `fixtures test`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

from ..schemas import DecisionRequest, EvidenceBundle, SeerrRequestState, ShowFacts
from ..seerr.client import SeerrClient, SeerrError
from ..sonarr.audit import sonarr_state_from_series
from ..sonarr.client import SonarrClient, SonarrError

logger = logging.getLogger(__name__)

_REQUEST_STATUS = {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"}


class EvidenceSource(Protocol):
    def collect(self, request: DecisionRequest) -> EvidenceBundle: ...


def facts_from_seerr_tv(tv: dict) -> ShowFacts:
    """Map Seerr's GET /tv/{tmdbId} response (TMDB shape) to ShowFacts."""
    first_air = tv.get("firstAirDate") or ""
    runtimes = tv.get("episodeRunTime") or []
    keywords = [k.get("name", "") for k in (tv.get("keywords") or [])]
    external = tv.get("externalIds") or {}
    return ShowFacts(
        canonical_title=tv.get("name"),
        year=int(first_air[:4]) if len(first_air) >= 4 and first_air[:4].isdigit() else None,
        tmdb_id=tv.get("id"),
        tvdb_id=external.get("tvdbId"),
        imdb_id=external.get("imdbId"),
        genres=[g.get("name", "") for g in (tv.get("genres") or [])],
        networks=[n.get("name", "") for n in (tv.get("networks") or [])],
        keywords=keywords,
        status=tv.get("status"),
        number_of_seasons=tv.get("numberOfSeasons"),
        number_of_episodes=tv.get("numberOfEpisodes"),
        episode_run_time_minutes=runtimes[0] if runtimes else None,
        vote_average=tv.get("voteAverage"),
        vote_count=tv.get("voteCount"),
        popularity=tv.get("popularity"),
        original_language=tv.get("originalLanguage"),
        overview=(tv.get("overview") or "")[:600] or None,
    )


def seerr_request_state_from_api(req: dict) -> SeerrRequestState:
    requested_by = req.get("requestedBy") or {}
    seasons = [
        s.get("seasonNumber")
        for s in (req.get("seasons") or [])
        if isinstance(s.get("seasonNumber"), int)
    ]
    return SeerrRequestState(
        request_id=req.get("id"),
        status=_REQUEST_STATUS.get(req.get("status"), str(req.get("status"))),
        is4k=req.get("is4k"),
        profile_id=req.get("profileId"),
        server_id=req.get("serverId"),
        root_folder=req.get("rootFolder"),
        requested_by=requested_by.get("displayName") or requested_by.get("username"),
        requested_seasons=seasons,
    )


def resolve_tv_by_tvdb(
    seerr: SeerrClient, tvdb_id: int, title: str, max_candidates: int = 5
) -> dict | None:
    """Map a Sonarr-native tvdb_id to Seerr TV details (ADR-0002 worth endpoint).

    Seerr has no external-id lookup, so this searches by title and *confirms*
    each TV candidate by fetching /tv/{tmdbId} and matching externalIds.tvdbId.
    A wrong search hit can therefore never be scored: no confirmation, no facts.
    """
    try:
        results = seerr.search(title)
    except SeerrError as exc:
        logger.warning("seerr search for tvdb %s failed: %s", tvdb_id, exc)
        return None
    candidates = [r for r in results if r.get("mediaType") == "tv"][:max_candidates]
    for candidate in candidates:
        tmdb_id = candidate.get("id")
        if tmdb_id is None:
            continue
        try:
            tv = seerr.get_tv_details(tmdb_id)
        except SeerrError as exc:
            logger.warning("seerr tv details for candidate %s failed: %s", tmdb_id, exc)
            continue
        if (tv.get("externalIds") or {}).get("tvdbId") == tvdb_id:
            return tv
    return None


class LiveEvidenceSource:
    def __init__(self, seerr: SeerrClient, sonarr: SonarrClient | None = None) -> None:
        self._seerr = seerr
        self._sonarr = sonarr

    def collect(self, request: DecisionRequest) -> EvidenceBundle:
        bundle = EvidenceBundle()

        if request.seerr_request_id is not None:
            try:
                req = self._seerr.get_request(request.seerr_request_id)
                bundle.seerr_request = seerr_request_state_from_api(req)
                bundle.sources.append(f"seerr:/request/{request.seerr_request_id}")
                media = req.get("media") or {}
                if request.tmdb_id is None:
                    request = request.model_copy(update={"tmdb_id": media.get("tmdbId")})
                if request.tvdb_id is None:
                    request = request.model_copy(update={"tvdb_id": media.get("tvdbId")})
            except SeerrError as exc:
                logger.warning("seerr request lookup failed: %s", exc)
                bundle.gaps.append("seerr_request")

        if request.tmdb_id is not None:
            try:
                tv = self._seerr.get_tv_details(request.tmdb_id)
                bundle.facts = facts_from_seerr_tv(tv)
                bundle.sources.append(f"seerr:/tv/{request.tmdb_id}")
            except SeerrError as exc:
                logger.warning("seerr tv details failed: %s", exc)
                bundle.gaps.append("show_facts")
        else:
            bundle.gaps.append("tmdb_id")

        if bundle.facts.canonical_title is None and request.title:
            bundle.facts.canonical_title = request.title
        if bundle.facts.year is None and request.year:
            bundle.facts.year = request.year
        if bundle.facts.tvdb_id is None and request.tvdb_id is not None:
            bundle.facts.tvdb_id = request.tvdb_id

        if self._sonarr is not None and bundle.facts.tvdb_id is not None:
            try:
                series = self._sonarr.get_series_by_tvdb(bundle.facts.tvdb_id)
                profiles = {
                    int(p["id"]): str(p["name"]) for p in self._sonarr.list_quality_profiles()
                }
                bundle.sonarr = sonarr_state_from_series(series, profiles)
                bundle.sources.append(f"sonarr:/series?tvdbId={bundle.facts.tvdb_id}")
            except SonarrError as exc:
                logger.warning("sonarr lookup failed: %s", exc)
                bundle.gaps.append("sonarr_state")

        return bundle


class FixtureEvidenceSource:
    """Loads EvidenceBundle JSON fixtures by tmdb_id or normalized title."""

    def __init__(self, fixtures_dir: str | Path) -> None:
        self._dir = Path(fixtures_dir)

    def collect(self, request: DecisionRequest) -> EvidenceBundle:
        candidates = []
        if request.tmdb_id is not None:
            candidates.append(f"tmdb_{request.tmdb_id}.json")
        if request.title:
            slug = "".join(c if c.isalnum() else "_" for c in request.title.lower()).strip("_")
            candidates.append(f"{slug}.json")
        bundle: EvidenceBundle | None = None
        for name in candidates:
            path = self._dir / name
            if path.is_file():
                bundle = EvidenceBundle.model_validate(json.loads(path.read_text()))
                break
        if bundle is None:
            # Unknown title: an empty bundle exercises the insufficient-metadata path.
            bundle = EvidenceBundle()
            bundle.gaps.extend(["show_facts", "tmdb_id"])
        # Overlay Seerr request context from the trigger so planning works offline.
        if request.seerr_request_id is not None and bundle.seerr_request.request_id is None:
            bundle.seerr_request = SeerrRequestState(
                request_id=request.seerr_request_id,
                status="pending",
                requested_seasons=request.seasons,
            )
        return bundle
