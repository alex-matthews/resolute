"""ADR-0002 downgrade executor: preconditions, quality-list invariant,
write-ahead audit, exactly-once, and the report-only default."""

from datetime import UTC, datetime, timedelta

import pytest

from resolute.config import Settings
from resolute.sonarr.downgrade import (
    DowngradeBlocked,
    DowngradeHandoff,
    execute_downgrade,
    plan_downgrade,
    profile_allows_resolution,
)

HD_PROFILE = {
    "id": 6,
    "name": "HD-1080p",
    "items": [
        {"quality": {"id": 9, "name": "HDTV-1080p", "resolution": 1080}, "allowed": True},
        {"quality": {"id": 19, "name": "WEBDL-2160p", "resolution": 2160}, "allowed": False},
    ],
}
UHD_PROFILE = {
    "id": 5,
    "name": "Ultra-HD",
    "items": [
        {
            "name": "WEB 2160p",
            "allowed": True,
            "items": [
                {"quality": {"id": 18, "name": "WEBRip-2160p", "resolution": 2160}, "allowed": True},
            ],
        },
    ],
}


class FakeDowngradeSonarr:
    def __init__(
        self,
        series: dict | None = None,
        queue: list | None = None,
        files: list | None = None,
    ):
        self.series = series
        self.queue = queue or []
        self.files = files if files is not None else [
            {"id": 1, "size": 30 * 1024**3, "quality": {"quality": {"resolution": 2160}}},
            {"id": 2, "size": 4 * 1024**3, "quality": {"quality": {"resolution": 1080}}},
        ]
        self.profile_updates: list[tuple] = []
        self.searches: list[int] = []

    def get_series_by_tvdb(self, tvdb_id):
        return self.series

    def resolve_profile_id(self, name):
        return {"HD-1080p": 6, "Ultra-HD": 5}[name]

    def get_quality_profile(self, profile_id):
        return {6: HD_PROFILE, 5: UHD_PROFILE}[profile_id]

    def get_queue_details(self, series_id):
        return self.queue

    def list_episode_files(self, series_id):
        return self.files

    def update_series_profile(self, series_id, profile_id):
        self.profile_updates.append((series_id, profile_id))
        return {}

    def trigger_series_search(self, series_id):
        self.searches.append(series_id)
        return {}


SERIES = {"id": 42, "title": "The Continental", "status": "ended", "qualityProfileId": 5}


def handoff(**overrides) -> DowngradeHandoff:
    base = {"costanza_decision_id": "cz-001", "tvdb_id": 404171}
    return DowngradeHandoff(**{**base, **overrides})


@pytest.fixture
def dg_settings(tmp_path) -> Settings:
    return Settings(db_path=tmp_path / "dg.db", policy_path=tmp_path / "missing.yaml")


def test_profile_quality_list_invariant():
    assert not profile_allows_resolution(HD_PROFILE, 2160)
    assert profile_allows_resolution(HD_PROFILE, 1080)
    assert profile_allows_resolution(UHD_PROFILE, 2160)  # group-nested


def test_clean_plan_reports_reclaim(dg_settings):
    report = plan_downgrade(handoff(), dg_settings, FakeDowngradeSonarr(series=SERIES))
    assert report.blockers == []
    assert report.series_id == 42
    assert report.target_profile_id == 6
    assert report.resident_uhd_files == 1
    assert report.estimated_gb_reclaimed == 30.0
    assert report.executed is False


def test_plan_blockers(dg_settings):
    sonarr = FakeDowngradeSonarr(series=SERIES)
    assert "Costanza protection" in plan_downgrade(
        handoff(protected=True), dg_settings, sonarr
    ).blockers[0]

    stale = handoff(decided_at=datetime.now(UTC) - timedelta(days=30))
    assert any("stale" in b for b in plan_downgrade(stale, dg_settings, sonarr).blockers)

    missing = plan_downgrade(handoff(), dg_settings, FakeDowngradeSonarr(series=None))
    assert any("no Sonarr series" in b for b in missing.blockers)

    airing = plan_downgrade(
        handoff(), dg_settings, FakeDowngradeSonarr(series={**SERIES, "status": "continuing"})
    )
    assert any("airing" in b for b in airing.blockers)

    queued = plan_downgrade(
        handoff(), dg_settings, FakeDowngradeSonarr(series=SERIES, queue=[{"id": 1}])
    )
    assert any("queued/downloading" in b for b in queued.blockers)

    # Misconfiguration for reclaim: target still lists 2160p.
    bad_target = plan_downgrade(
        handoff(target_profile_name="Ultra-HD"),
        dg_settings,
        FakeDowngradeSonarr(series={**SERIES, "qualityProfileId": 6}),
    )
    assert any("still lists 2160p" in b for b in bad_target.blockers)

    already = plan_downgrade(
        handoff(), dg_settings, FakeDowngradeSonarr(series={**SERIES, "qualityProfileId": 6})
    )
    assert any("already on the target profile" in b for b in already.blockers)


def test_no_uhd_resident_is_not_a_blocker(dg_settings):
    sonarr = FakeDowngradeSonarr(
        series=SERIES,
        files=[{"id": 2, "size": 4 * 1024**3, "quality": {"quality": {"resolution": 1080}}}],
    )
    report = plan_downgrade(handoff(), dg_settings, sonarr)
    assert report.blockers == []
    assert report.estimated_gb_reclaimed == 0.0
    assert any("nothing" in n for n in report.notes)


def test_execute_forced_dry_run_without_gates(dg_settings, store):
    sonarr = FakeDowngradeSonarr(series=SERIES)
    with pytest.raises(DowngradeBlocked, match="allow_writes"):
        execute_downgrade(handoff(), dg_settings, sonarr, store, "alex")

    dg_settings.allow_writes = True
    with pytest.raises(DowngradeBlocked, match="admin_confirm_enabled"):
        execute_downgrade(handoff(), dg_settings, sonarr, store, "alex")
    assert sonarr.profile_updates == []
    assert sonarr.searches == []
    assert store.get_downgrade("cz-001") is None  # no write-ahead row on refusal


def test_execute_applies_reclaim_exactly_once(dg_settings, store):
    dg_settings.allow_writes = True
    dg_settings.downgrade.admin_confirm_enabled = True
    sonarr = FakeDowngradeSonarr(series=SERIES)

    report = execute_downgrade(handoff(), dg_settings, sonarr, store, "alex")
    assert report.executed is True
    assert sonarr.profile_updates == [(42, 6)]
    assert sonarr.searches == [42]
    record = store.get_downgrade("cz-001")
    assert record["executed"] is True
    assert record["operator"] == "alex"

    # Same Costanza decision id again: the write-ahead UNIQUE row refuses.
    with pytest.raises(DowngradeBlocked, match="refusing to run twice"):
        execute_downgrade(handoff(), dg_settings, sonarr, store, "alex")
    assert sonarr.profile_updates == [(42, 6)]  # unchanged


def test_blocked_plan_never_writes(dg_settings, store):
    dg_settings.allow_writes = True
    dg_settings.downgrade.admin_confirm_enabled = True
    sonarr = FakeDowngradeSonarr(series={**SERIES, "status": "continuing"})
    with pytest.raises(DowngradeBlocked, match="airing"):
        execute_downgrade(handoff(), dg_settings, sonarr, store, "alex")
    assert sonarr.profile_updates == []
    assert store.get_downgrade("cz-001") is None
