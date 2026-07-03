from tv_decider.engine.features import extract_features
from tv_decider.engine.policy import prescore
from tv_decider.metadata.source import FixtureEvidenceSource
from tv_decider.schemas import Confidence, DecisionRequest, Resolution


def _score(evidence_source: FixtureEvidenceSource, policy, **request_kwargs):
    request = DecisionRequest(**request_kwargs)
    evidence = evidence_source.collect(request)
    features = extract_features(request, evidence, policy)
    return features, prescore(features, policy)


def test_premium_visual_show_scores_2160p_high(evidence_source, policy):
    _, pre = _score(evidence_source, policy, title="Severance", tmdb_id=95396)
    assert pre.household.resolution is Resolution.P2160
    assert pre.household.confidence is Confidence.HIGH
    assert not pre.ambiguous


def test_archival_sitcom_scores_1080p(evidence_source, policy):
    _, pre = _score(evidence_source, policy, title="Friends", tmdb_id=1668)
    assert pre.household.resolution is Resolution.P1080
    assert not pre.ambiguous
    # episode burden should appear as a negative household component
    burden = [c for c in pre.components if c.name == "episode_burden"]
    assert burden and burden[0].contribution < 0


def test_story_led_show_lands_in_ambiguous_band(evidence_source, policy):
    _, pre = _score(evidence_source, policy, title="The Bear", tmdb_id=136315)
    assert pre.ambiguous
    assert pre.household.confidence is Confidence.LOW


def test_requester_bias_shifts_household_score(evidence_source, policy):
    _, neutral = _score(evidence_source, policy, title="The Bear", tmdb_id=136315)
    _, biased = _score(
        evidence_source, policy, title="The Bear", tmdb_id=136315, requester="alex"
    )
    assert biased.score > neutral.score
    assert neutral.objective.resolution == biased.objective.resolution  # objective lane unmoved


def test_objective_lane_ignores_household_signals(evidence_source, policy):
    _, pre = _score(
        evidence_source, policy, title="Star Wars: Visions", tmdb_id=114478
    )
    franchise = [c for c in pre.components if c.name == "franchise_priority"]
    assert franchise, "franchise pin should contribute to household lane"
    objective_only = sum(
        c.contribution
        for c in pre.components
        if c.name in ("visual_genre", "network_tier", "era", "acclaim", "low_payoff_genre")
    )
    assert pre.score > objective_only  # household adds franchise on top
