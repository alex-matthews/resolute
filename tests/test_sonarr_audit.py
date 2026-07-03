from tv_decider.schemas import Resolution, SonarrState
from tv_decider.sonarr.audit import audit_series_profile, sonarr_state_from_series

from conftest import load_fixture

PROFILES = {"profile_name_1080p": "HD-1080p", "profile_name_2160p": "Ultra-HD"}
PROFILES_BY_ID = {6: "HD-1080p", 5: "Ultra-HD"}


def test_sonarr_state_mapping_from_fixture():
    series = load_fixture("sonarr", "series_severance.json")
    state = sonarr_state_from_series(series, PROFILES_BY_ID)
    assert state.exists
    assert state.series_id == 42
    assert state.quality_profile_name == "HD-1080p"
    assert state.episode_file_count == 9


def test_sonarr_state_for_missing_series():
    assert sonarr_state_from_series(None, PROFILES_BY_ID).exists is False


def test_audit_detects_profile_drift():
    series = load_fixture("sonarr", "series_severance.json")
    state = sonarr_state_from_series(series, PROFILES_BY_ID)
    result = audit_series_profile(state, Resolution.P2160, tvdb_id=371980, **PROFILES)
    assert result.series_found
    assert result.matches is False
    assert result.expected_profile == "Ultra-HD"
    assert result.actual_profile == "HD-1080p"


def test_audit_confirms_match():
    state = SonarrState(exists=True, series_id=42, quality_profile_name="Ultra-HD")
    result = audit_series_profile(state, Resolution.P2160, **PROFILES)
    assert result.matches is True


def test_audit_handles_series_not_yet_created():
    result = audit_series_profile(SonarrState(exists=False), Resolution.P1080, **PROFILES)
    assert result.series_found is False
    assert result.matches is None
    assert "pending" in result.note
