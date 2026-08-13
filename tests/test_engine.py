from conftest import CannedProvider, make_verdict

from resolute.engine.engine import DecisionEngine
from resolute.judge.judge import Judge
from resolute.schemas import (
    ActionType,
    AutomationMode,
    Confidence,
    DecisionRequest,
    Resolution,
)


def test_end_to_end_showcase_decision(engine):
    """LLM-primary (ADR-0003): the model decides; the pipeline plans."""
    decision = engine.decide(DecisionRequest(title="Severance", tmdb_id=95396))
    assert decision.final_resolution is Resolution.P2160
    assert decision.confidence is Confidence.HIGH
    assert decision.mode is AutomationMode.SHADOW
    assert decision.title == "Severance"
    assert decision.model_involvement.used
    assert decision.model_involvement.household_hash is not None
    assert decision.verdict is not None
    assert decision.decision_id
    # no Seerr request in evidence -> audit action only, nothing writable
    assert all(not a.is_write for a in decision.action_plan)


def test_model_hold_request_is_honored(settings, policy, evidence_source):
    judge = Judge(
        CannedProvider(make_verdict("1080p", "medium", action="hold_for_manual_review"))
    )
    engine = DecisionEngine(settings, policy, evidence_source, judge=judge)
    decision = engine.decide(DecisionRequest(title="The Bear", tmdb_id=136315))
    assert decision.final_resolution is Resolution.P1080
    assert any(a.type is ActionType.HOLD_FOR_MANUAL_REVIEW for a in decision.action_plan)


def test_model_disabled_degrades_to_conservative_fallback(engine_no_model):
    """No model, no normal decisions: 1080p + hold, never a write (ADR-0003)."""
    decision = engine_no_model.decide(DecisionRequest(title="Severance", tmdb_id=95396))
    assert decision.final_resolution is Resolution.P1080
    assert decision.confidence is Confidence.LOW
    assert "model_unavailable" in decision.risk_flags
    assert not decision.model_involvement.used
    assert any("hold" in a.type for a in decision.action_plan)
    assert all(not a.is_write for a in decision.action_plan)


def test_twice_invalid_output_degrades_to_conservative_fallback(
    settings, policy, evidence_source
):
    judge = Judge(CannedProvider("not json at all"))
    engine = DecisionEngine(settings, policy, evidence_source, judge=judge)
    decision = engine.decide(DecisionRequest(title="The Bear", tmdb_id=136315))
    assert decision.final_resolution is Resolution.P1080
    assert "model_unavailable" in decision.risk_flags
    assert decision.verdict is None
    assert decision.model_involvement.used
    assert decision.model_involvement.error is not None
    assert any("hold" in a.type for a in decision.action_plan)
    assert len(judge.provider.calls) == 2  # one retry, then fail closed


def test_low_confidence_verdict_holds(settings, policy, evidence_source):
    judge = Judge(CannedProvider(make_verdict("2160p", "low")))
    engine = DecisionEngine(settings, policy, evidence_source, judge=judge)
    decision = engine.decide(DecisionRequest(title="Severance", tmdb_id=95396))
    assert decision.confidence is Confidence.LOW
    assert "low_confidence" in decision.risk_flags
    assert any("hold" in a.type for a in decision.action_plan)


def test_unknown_title_is_insufficient_metadata_without_model_call(engine):
    """Metadata floor: both title and genres absent -> hold, no model spend."""
    decision = engine.decide(DecisionRequest(tmdb_id=999999))
    assert decision.final_resolution is Resolution.P1080
    assert "insufficient_metadata" in decision.risk_flags
    assert any(a.type is ActionType.INSUFFICIENT_METADATA for a in decision.action_plan)
    assert not decision.model_involvement.used
    assert engine.judge.provider.calls == []
