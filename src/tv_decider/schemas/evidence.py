"""Evidence bundle: deterministic facts gathered before any scoring or judgement."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field


class ShowFacts(BaseModel):
    """Objective show metadata, typically sourced through Seerr's TMDB proxy."""

    model_config = ConfigDict(extra="forbid")

    canonical_title: str | None = None
    year: int | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None
    imdb_id: str | None = None
    genres: list[str] = Field(default_factory=list)
    networks: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    status: str | None = None  # Returning Series / Ended / Canceled ...
    number_of_seasons: int | None = None
    number_of_episodes: int | None = None
    episode_run_time_minutes: int | None = None
    vote_average: float | None = None  # TMDB 0-10
    vote_count: int | None = None
    popularity: float | None = None
    original_language: str | None = None
    overview: str | None = None


class SonarrState(BaseModel):
    """Current Sonarr view of the series, if it already exists downstream."""

    model_config = ConfigDict(extra="forbid")

    series_id: int | None = None
    exists: bool = False
    quality_profile_id: int | None = None
    quality_profile_name: str | None = None
    monitored: bool | None = None
    episode_file_count: int | None = None
    size_on_disk_bytes: int | None = None
    tags: list[str] = Field(default_factory=list)


class SeerrRequestState(BaseModel):
    """Current Seerr view of the request being decided, if any."""

    model_config = ConfigDict(extra="forbid")

    request_id: int | None = None
    status: str | None = None  # pending / approved / declined / ...
    is4k: bool | None = None
    profile_id: int | None = None
    server_id: int | None = None
    root_folder: str | None = None
    requested_by: str | None = None
    requested_seasons: list[int] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    """Everything the engine and judge are allowed to look at."""

    model_config = ConfigDict(extra="forbid")

    facts: ShowFacts = Field(default_factory=ShowFacts)
    sonarr: SonarrState = Field(default_factory=SonarrState)
    seerr_request: SeerrRequestState = Field(default_factory=SeerrRequestState)
    gaps: list[str] = Field(default_factory=list)  # named missing-evidence markers
    sources: list[str] = Field(default_factory=list)  # provenance, e.g. "seerr:/tv/95396"

    def bundle_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
