"""Sonarr audit: did the decided profile actually land downstream?

Used post-hoc after Seerr routes an approved request, and by scheduled
library review to find drift between recommendations and reality.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..schemas import Resolution, SonarrState


class AuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tvdb_id: int | None = None
    series_id: int | None = None
    series_found: bool = False
    expected_profile: str | None = None
    actual_profile: str | None = None
    matches: bool | None = None
    note: str = ""


def sonarr_state_from_series(series: dict | None, profiles_by_id: dict[int, str]) -> SonarrState:
    if not series:
        return SonarrState(exists=False)
    profile_id = series.get("qualityProfileId")
    stats = series.get("statistics") or {}
    return SonarrState(
        exists=True,
        series_id=series.get("id"),
        quality_profile_id=profile_id,
        quality_profile_name=profiles_by_id.get(profile_id),
        monitored=series.get("monitored"),
        episode_file_count=stats.get("episodeFileCount"),
        size_on_disk_bytes=stats.get("sizeOnDisk"),
        tags=[str(t) for t in series.get("tags", [])],
    )


def audit_series_profile(
    state: SonarrState,
    expected_resolution: Resolution,
    *,
    profile_name_1080p: str,
    profile_name_2160p: str,
    tvdb_id: int | None = None,
) -> AuditResult:
    expected = (
        profile_name_2160p
        if expected_resolution is Resolution.P2160
        else profile_name_1080p
    )
    if not state.exists:
        return AuditResult(
            tvdb_id=tvdb_id,
            series_found=False,
            expected_profile=expected,
            note="series not present in Sonarr yet (request may still be pending)",
        )
    matches = (
        state.quality_profile_name is not None
        and state.quality_profile_name.strip().lower() == expected.strip().lower()
    )
    return AuditResult(
        tvdb_id=tvdb_id,
        series_id=state.series_id,
        series_found=True,
        expected_profile=expected,
        actual_profile=state.quality_profile_name,
        matches=matches,
        note="profile matches decision" if matches else "profile drift detected",
    )
