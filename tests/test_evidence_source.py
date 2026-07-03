"""Provider abstraction tests: Seerr/Sonarr responses -> EvidenceBundle, no network."""

from tv_decider.metadata.source import (
    LiveEvidenceSource,
    facts_from_seerr_tv,
    seerr_request_state_from_api,
)
from tv_decider.schemas import DecisionRequest
from tv_decider.seerr.client import SeerrError

from conftest import load_fixture


class FakeSeerrClient:
    def __init__(self, tv=None, request=None):
        self._tv = tv
        self._request = request

    def get_request(self, request_id):
        if self._request is None:
            raise SeerrError("boom")
        return self._request

    def get_tv_details(self, tmdb_id):
        if self._tv is None:
            raise SeerrError("boom")
        return self._tv


class FakeSonarrClient:
    def __init__(self, series=None):
        self._series = series

    def get_series_by_tvdb(self, tvdb_id):
        return self._series

    def list_quality_profiles(self):
        return load_fixture("sonarr", "quality_profiles.json")


def test_facts_mapping_from_seerr_tv_fixture():
    facts = facts_from_seerr_tv(load_fixture("seerr", "tv_details_severance.json"))
    assert facts.canonical_title == "Severance"
    assert facts.year == 2022
    assert facts.tvdb_id == 371980
    assert facts.genres == ["Drama", "Mystery", "Sci-Fi & Fantasy"]
    assert facts.networks == ["Apple TV+"]
    assert facts.episode_run_time_minutes == 50
    assert facts.keywords == ["dystopia", "workplace"]


def test_request_state_mapping_from_seerr_fixture():
    state = seerr_request_state_from_api(load_fixture("seerr", "request_detail.json"))
    assert state.request_id == 123
    assert state.status == "pending"
    assert state.requested_by == "alex"
    assert state.requested_seasons == [1]
    assert state.profile_id == 6


def test_live_source_combines_seerr_and_sonarr():
    seerr = FakeSeerrClient(
        tv=load_fixture("seerr", "tv_details_severance.json"),
        request=load_fixture("seerr", "request_detail.json"),
    )
    sonarr = FakeSonarrClient(series=load_fixture("sonarr", "series_severance.json"))
    source = LiveEvidenceSource(seerr, sonarr)
    bundle = source.collect(DecisionRequest(seerr_request_id=123, tmdb_id=95396))
    assert bundle.facts.canonical_title == "Severance"
    assert bundle.seerr_request.request_id == 123
    assert bundle.sonarr.exists
    assert bundle.sonarr.quality_profile_name == "HD-1080p"
    assert not bundle.gaps


def test_live_source_records_gaps_on_failure():
    source = LiveEvidenceSource(FakeSeerrClient(), FakeSonarrClient())
    bundle = source.collect(DecisionRequest(seerr_request_id=123, tmdb_id=95396))
    assert "seerr_request" in bundle.gaps
    assert "show_facts" in bundle.gaps


def test_live_source_backfills_tmdb_id_from_request():
    seerr = FakeSeerrClient(
        tv=load_fixture("seerr", "tv_details_severance.json"),
        request=load_fixture("seerr", "request_detail.json"),
    )
    source = LiveEvidenceSource(seerr, None)
    bundle = source.collect(DecisionRequest(seerr_request_id=123))
    assert bundle.facts.tmdb_id == 95396  # discovered via the Seerr request
