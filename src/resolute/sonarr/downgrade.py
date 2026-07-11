"""ADR-0002 downgrade executor: reclaim a TV series to 1080p via Sonarr's own
upgrade flow (profile-set + monitored search -> Sonarr imports 1080p, then
deletes the out-of-profile 2160p). Resolute deletes nothing.

Trust ladder: `plan_downgrade` (report-only) is always available and writes
nothing. `execute_downgrade` additionally requires settings.allow_writes AND
settings.downgrade.admin_confirm_enabled, both shipping off, and is
exactly-once per Costanza decision via the write-ahead audit row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings
from .client import SonarrClient, SonarrError

_UHD_RESOLUTION = 2160
_GB = 1024**3


class DowngradeHandoff(BaseModel):
    """The Costanza -> Resolute seam: the council decided `downgrade`,
    Resolute owns the Sonarr write (Costanza ADR-0011)."""

    model_config = ConfigDict(extra="forbid")

    costanza_decision_id: str
    tvdb_id: int
    # Defaults to settings.downgrade.target_profile_name when omitted.
    target_profile_name: str | None = None
    # When the council decided; older than max_decision_age_days blocks.
    decided_at: datetime | None = None
    # Costanza-attested: a protected title must never reach execution.
    protected: bool = False


class DowngradeReport(BaseModel):
    """Dry-run plan / execution record: what would be (or was) reclaimed."""

    model_config = ConfigDict(extra="forbid")

    costanza_decision_id: str
    tvdb_id: int
    series_id: int | None = None
    title: str | None = None
    current_profile_id: int | None = None
    target_profile_name: str | None = None
    target_profile_id: int | None = None
    resident_uhd_files: int = 0
    resident_uhd_bytes: int = 0
    estimated_gb_reclaimed: float = 0.0
    blockers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    executed: bool = False


def profile_allows_resolution(profile: dict, resolution: int) -> bool:
    """Walk a Sonarr quality profile's item tree (leaves and allowed groups)
    checking whether any allowed quality carries the given resolution."""

    def walk(items: list[dict], group_allowed: bool) -> bool:
        for item in items:
            allowed = bool(item.get("allowed")) or group_allowed
            quality = item.get("quality")
            if quality is not None:
                if allowed and quality.get("resolution") == resolution:
                    return True
            elif item.get("items"):
                if walk(item["items"], allowed):
                    return True
        return False

    return walk(profile.get("items") or [], False)


def plan_downgrade(
    handoff: DowngradeHandoff, settings: Settings, sonarr: SonarrClient
) -> DowngradeReport:
    """Assemble the reclaim plan and every ADR-0002 precondition. Read-only."""
    target_name = handoff.target_profile_name or settings.downgrade.target_profile_name
    report = DowngradeReport(
        costanza_decision_id=handoff.costanza_decision_id,
        tvdb_id=handoff.tvdb_id,
        target_profile_name=target_name,
    )

    if handoff.protected:
        report.blockers.append("title carries a Costanza protection")

    if handoff.decided_at is not None:
        age = datetime.now(UTC) - handoff.decided_at
        if age > timedelta(days=settings.downgrade.max_decision_age_days):
            report.blockers.append(
                f"decision is stale ({age.days}d old, max "
                f"{settings.downgrade.max_decision_age_days}d)"
            )
    else:
        report.notes.append("handoff carries no decided_at; staleness not assessed")

    try:
        series = sonarr.get_series_by_tvdb(handoff.tvdb_id)
    except SonarrError as exc:
        report.blockers.append(f"sonarr unavailable: {exc}")
        return report
    if not series:
        report.blockers.append(f"no Sonarr series with tvdb_id {handoff.tvdb_id}")
        return report

    report.series_id = series.get("id")
    report.title = series.get("title")
    report.current_profile_id = series.get("qualityProfileId")

    if series.get("status") == "continuing":
        report.blockers.append("series is airing (status=continuing)")

    try:
        report.target_profile_id = sonarr.resolve_profile_id(target_name)
        profile = sonarr.get_quality_profile(report.target_profile_id)
        # The load-bearing invariant: the target profile must EXCLUDE 2160p so
        # the resident file is out-of-profile and Sonarr's upgrade flow
        # replaces it. A profile that includes 2160p would leave it in place.
        if profile_allows_resolution(profile, _UHD_RESOLUTION):
            report.blockers.append(
                f"target profile '{target_name}' still lists 2160p in its "
                "quality list; reclaim requires a profile that excludes it"
            )
    except SonarrError as exc:
        report.blockers.append(str(exc))

    if report.target_profile_id is not None and (
        report.current_profile_id == report.target_profile_id
    ):
        report.blockers.append("series is already on the target profile")

    try:
        queue = sonarr.get_queue_details(report.series_id)
        if queue:
            report.blockers.append(
                f"{len(queue)} item(s) queued/downloading for this series"
            )
    except SonarrError as exc:
        report.blockers.append(f"queue state unavailable: {exc}")

    try:
        files = sonarr.list_episode_files(report.series_id)
    except SonarrError as exc:
        report.blockers.append(f"episode files unavailable: {exc}")
        return report
    uhd = [
        f
        for f in files
        if ((f.get("quality") or {}).get("quality") or {}).get("resolution")
        == _UHD_RESOLUTION
    ]
    report.resident_uhd_files = len(uhd)
    report.resident_uhd_bytes = sum(int(f.get("size") or 0) for f in uhd)
    report.estimated_gb_reclaimed = round(report.resident_uhd_bytes / _GB, 1)
    if not uhd:
        # Not a blocker (ADR-0002): Sonarr deletes only on import, so with no
        # 2160p resident there is simply nothing to reclaim.
        report.notes.append("no resident 2160p files; reclaim would free nothing")
    return report


class DowngradeBlocked(Exception):
    """Pre-flight refusal: nothing has been written."""


def execute_downgrade(
    handoff: DowngradeHandoff,
    settings: Settings,
    sonarr: SonarrClient,
    store,
    operator: str,
) -> DowngradeReport:
    """Apply the reclaim: write-ahead audit row, profile set, monitored search.

    Raises DowngradeBlocked on any precondition failure, disabled gate, or a
    duplicate Costanza decision id (exactly-once). Sonarr performs the actual
    replacement (and deletion) through its ordinary upgrade flow.
    """
    report = plan_downgrade(handoff, settings, sonarr)
    if report.blockers:
        raise DowngradeBlocked("; ".join(report.blockers))
    if not settings.allow_writes:
        raise DowngradeBlocked("allow_writes is false: downgrade forced to dry-run")
    if not settings.downgrade.admin_confirm_enabled:
        raise DowngradeBlocked(
            "downgrade.admin_confirm_enabled is false: report-only phase"
        )

    # Write-ahead audit: the row lands (UNIQUE per Costanza decision) before
    # any Sonarr write, so a crash mid-execution is visible and re-runs refuse.
    if not store.save_downgrade(report, operator=operator):
        raise DowngradeBlocked(
            f"downgrade for Costanza decision {handoff.costanza_decision_id} "
            "already recorded; refusing to run twice"
        )

    sonarr.update_series_profile(report.series_id, report.target_profile_id)
    sonarr.trigger_series_search(report.series_id)
    report.executed = True
    report.notes.append(
        "profile set and monitored search triggered; Sonarr's upgrade flow "
        "imports 1080p then deletes the out-of-profile 2160p"
    )
    store.mark_downgrade_executed(handoff.costanza_decision_id, report)
    return report
