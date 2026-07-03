import pytest

from tv_decider.engine.features import FeatureSet
from tv_decider.engine.guardrails import apply_guardrails
from tv_decider.engine.policy import PreScore
from tv_decider.schemas import (
    Confidence,
    ModelVerdict,
    Recommendation,
    Resolution,
)


def _pre(resolution=Resolution.P2160, confidence=Confidence.MEDIUM, ambiguous=False, score=2.5):
    rec = Recommendation(resolution=resolution, confidence=confidence, reasons=["r"])
    return PreScore(objective=rec, household=rec, score=score, components=[], ambiguous=ambiguous)


def _verdict(resolution="2160p", confidence="medium", action="set_seerr_request_profile_2160p"):
    return ModelVerdict.model_validate(
        {
            "objective": {"resolution": resolution, "confidence": confidence, "reasons": ["x"]},
            "household": {"resolution": resolution, "confidence": confidence, "reasons": ["x"]},
            "automation": {"resolution": resolution, "confidence": confidence, "action": action},
            "risk_flags": [],
            "questions": [],
        }
    )


def test_missing_critical_metadata_forces_safe_hold(policy):
    features = FeatureSet(metadata_gaps=["title", "genres", "networks"])
    result = apply_guardrails(features, _pre(), None, policy)
    assert result.insufficient_metadata
    assert result.hold_for_review
    assert result.resolution is Resolution.P1080
    assert "insufficient_metadata" in result.risk_flags


def test_policy_pin_beats_score_and_judge(policy):
    features = FeatureSet(title="Great British Bake Off", pinned_1080p_title="great british bake off")
    verdict = _verdict("2160p")  # judge tries to upgrade a pinned title
    result = apply_guardrails(features, _pre(Resolution.P2160, Confidence.HIGH), verdict, policy)
    assert result.resolution is Resolution.P1080
    assert result.confidence is Confidence.HIGH
    assert "judge_conflict_pinned" in result.risk_flags


def test_judge_resolves_ambiguous_band_capped_at_medium(policy):
    features = FeatureSet(title="The Bear", genres=["drama"])
    pre = _pre(Resolution.P1080, Confidence.LOW, ambiguous=True, score=1.4)
    verdict = _verdict("2160p", "high")
    result = apply_guardrails(features, pre, verdict, policy)
    assert result.resolution is Resolution.P2160
    assert result.confidence is Confidence.MEDIUM  # judge confidence is capped
    assert not result.hold_for_review


def test_judge_cannot_flip_unambiguous_decision(policy):
    features = FeatureSet(title="Friends", genres=["comedy"])
    pre = _pre(Resolution.P1080, Confidence.HIGH, ambiguous=False, score=-3.0)
    verdict = _verdict("2160p", "high")
    result = apply_guardrails(features, pre, verdict, policy)
    assert result.resolution is Resolution.P1080
    assert "judge_disagrees_deterministic" in result.risk_flags


def test_judge_hold_request_is_honored(policy):
    features = FeatureSet(title="X", genres=["drama"])
    pre = _pre(Resolution.P1080, Confidence.LOW, ambiguous=True, score=1.0)
    verdict = _verdict("1080p", "low", "hold_for_manual_review")
    result = apply_guardrails(features, pre, verdict, policy)
    assert result.hold_for_review


def test_episode_burden_cap_downgrades_2160p(policy):
    features = FeatureSet(title="Long Show", genres=["documentary"], requested_episodes=200)
    result = apply_guardrails(features, _pre(Resolution.P2160, Confidence.MEDIUM), None, policy)
    assert result.resolution is Resolution.P1080
    assert "episode_burden_cap" in result.risk_flags


def test_episode_burden_cap_spares_high_confidence(policy):
    features = FeatureSet(title="Long Show", genres=["documentary"], requested_episodes=200)
    result = apply_guardrails(features, _pre(Resolution.P2160, Confidence.HIGH), None, policy)
    assert result.resolution is Resolution.P2160


def test_high_storage_pressure_blocks_medium_confidence_2160p(policy):
    pressured = policy.model_copy(update={"storage_pressure": "high"})
    features = FeatureSet(title="Show", genres=["documentary"])
    result = apply_guardrails(features, _pre(Resolution.P2160, Confidence.MEDIUM), None, pressured)
    assert result.resolution is Resolution.P1080
    assert "storage_pressure_block" in result.risk_flags


def test_unresolved_ambiguity_holds_without_judge(policy):
    features = FeatureSet(title="The Bear", genres=["drama"])
    pre = _pre(Resolution.P1080, Confidence.LOW, ambiguous=True, score=1.4)
    result = apply_guardrails(features, pre, None, policy)
    assert result.hold_for_review
    assert "near_threshold" in result.risk_flags


@pytest.mark.parametrize("confidence", [Confidence.LOW])
def test_low_confidence_always_holds(policy, confidence):
    features = FeatureSet(title="Show", genres=["drama"])
    result = apply_guardrails(features, _pre(Resolution.P2160, confidence), None, policy)
    assert result.hold_for_review
