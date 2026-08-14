"""ADR-0003 hard-rails unit tests: the safety envelope, not a decision engine."""

import json

from conftest import make_verdict

from resolute.engine.rails import (
    RailsResult,
    apply_rails,
    conservative_fallback,
    metadata_floor,
    metadata_gaps,
)
from resolute.schemas import Confidence, EvidenceBundle, ModelVerdict, Resolution, ShowFacts


def _verdict(**kw) -> ModelVerdict:
    return ModelVerdict.model_validate_json(make_verdict(**kw))


def test_metadata_gaps_names_missing_evidence():
    gaps = metadata_gaps(EvidenceBundle())
    assert {"title", "genres", "networks", "ratings", "episode_count"} <= set(gaps)


def test_floor_requires_both_title_and_genres_absent():
    both_missing = metadata_gaps(EvidenceBundle())
    assert metadata_floor(both_missing) is not None

    only_genres_missing = metadata_gaps(
        EvidenceBundle(facts=ShowFacts(canonical_title="Known Show"))
    )
    assert metadata_floor(only_genres_missing) is None  # title present -> judge


def test_floor_result_is_hold_1080p_low():
    result = metadata_floor(["title", "genres"])
    assert isinstance(result, RailsResult)
    assert result.resolution is Resolution.P1080
    assert result.confidence is Confidence.LOW
    assert result.hold_for_review and result.insufficient_metadata


def test_conservative_fallback_is_1080p_hold():
    result = conservative_fallback("provider timeout")
    assert result.resolution is Resolution.P1080
    assert result.confidence is Confidence.LOW
    assert result.hold_for_review
    assert "model_unavailable" in result.risk_flags
    assert any("provider timeout" in n for n in result.notes)


def test_apply_rails_passes_through_a_confident_verdict():
    result = apply_rails(_verdict(resolution="2160p", confidence="high"), gaps=[])
    assert result.resolution is Resolution.P2160
    assert result.confidence is Confidence.HIGH
    assert not result.hold_for_review


def test_apply_rails_honors_model_hold_request():
    result = apply_rails(
        _verdict(resolution="1080p", confidence="medium", action="hold_for_manual_review"),
        gaps=[],
    )
    assert result.hold_for_review
    assert result.resolution is Resolution.P1080


def test_apply_rails_low_confidence_holds():
    result = apply_rails(_verdict(resolution="2160p", confidence="low"), gaps=[])
    assert result.hold_for_review
    assert "low_confidence" in result.risk_flags


def test_apply_rails_surfaces_metadata_gap_flag():
    result = apply_rails(_verdict(), gaps=["ratings"])
    assert "metadata_gap" in result.risk_flags


def test_choice_set_is_closed_by_schema():
    """Rail 1: the model cannot invent a profile or an action."""
    bad_resolution = json.loads(make_verdict())
    bad_resolution["automation"]["resolution"] = "4320p"
    bad_action = json.loads(make_verdict())
    bad_action["automation"]["action"] = "delete_series"
    for tampered in (bad_resolution, bad_action):
        try:
            ModelVerdict.model_validate(tampered)
        except ValueError:
            continue
        raise AssertionError(f"schema accepted out-of-enum value: {tampered['automation']}")
