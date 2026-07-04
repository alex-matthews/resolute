import json

from resolute.engine.engine import DecisionEngine
from resolute.judge.judge import Judge
from resolute.judge.provider import StaticProvider
from resolute.schemas import (
    ActionType,
    AutomationMode,
    Confidence,
    DecisionRequest,
    Resolution,
)

JUDGE_2160 = json.dumps(
    {
        "objective": {"resolution": "2160p", "confidence": "medium", "reasons": ["prestige visuals"]},
        "household": {"resolution": "2160p", "confidence": "medium", "reasons": ["household favorite"]},
        "automation": {
            "resolution": "2160p",
            "confidence": "medium",
            "action": "set_seerr_request_profile_2160p",
        },
        "risk_flags": [],
        "questions": [],
    }
)


def test_end_to_end_showcase_decision(engine):
    decision = engine.decide(DecisionRequest(title="Severance", tmdb_id=95396))
    assert decision.final_resolution is Resolution.P2160
    assert decision.confidence is Confidence.HIGH
    assert decision.mode is AutomationMode.SHADOW
    assert decision.title == "Severance"
    assert not decision.model_involvement.used
    assert decision.decision_id
    # no Seerr request in evidence -> audit action only, nothing writable
    assert all(not a.is_write for a in decision.action_plan)


def test_ambiguous_without_judge_holds(engine):
    decision = engine.decide(DecisionRequest(title="The Bear", tmdb_id=136315))
    assert decision.final_resolution is Resolution.P1080
    assert any(a.type is ActionType.HOLD_FOR_MANUAL_REVIEW for a in decision.action_plan)
    assert "near_threshold" in decision.risk_flags


def test_judge_consulted_only_for_ambiguous_band(settings, policy, evidence_source):
    provider = StaticProvider([JUDGE_2160, JUDGE_2160])
    engine = DecisionEngine(settings, policy, evidence_source, judge=Judge(provider))

    # unambiguous case: judge not called
    engine.decide(DecisionRequest(title="Severance", tmdb_id=95396))
    assert len(provider.calls) == 0

    # ambiguous case: judge called and resolves the band
    decision = engine.decide(DecisionRequest(title="The Bear", tmdb_id=136315))
    assert len(provider.calls) == 1
    assert decision.final_resolution is Resolution.P2160
    assert decision.confidence is Confidence.MEDIUM
    assert decision.model_involvement.used
    assert decision.verdict is not None
    assert not any("hold" in a.type for a in decision.action_plan)


def test_force_judge_overrides_ambiguity_gate(settings, policy, evidence_source):
    provider = StaticProvider([JUDGE_2160])
    engine = DecisionEngine(settings, policy, evidence_source, judge=Judge(provider))
    engine.decide(DecisionRequest(title="Severance", tmdb_id=95396, force_judge=True))
    assert len(provider.calls) == 1


def test_judge_failure_falls_back_to_deterministic(settings, policy, evidence_source):
    provider = StaticProvider(["not json", "still not json"])
    engine = DecisionEngine(settings, policy, evidence_source, judge=Judge(provider))
    decision = engine.decide(DecisionRequest(title="The Bear", tmdb_id=136315))
    assert decision.final_resolution is Resolution.P1080  # deterministic lean
    assert "model_error" in decision.risk_flags
    assert decision.verdict is None
    assert any("hold" in a.type for a in decision.action_plan)  # still held for a human


def test_unknown_title_is_insufficient_metadata(engine):
    decision = engine.decide(DecisionRequest(tmdb_id=999999))
    assert decision.final_resolution is Resolution.P1080
    assert "insufficient_metadata" in decision.risk_flags
    assert any(a.type is ActionType.INSUFFICIENT_METADATA for a in decision.action_plan)
