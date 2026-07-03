from tv_decider.engine.guardrails import GuardrailResult
from tv_decider.schemas import (
    ActionType,
    Confidence,
    EvidenceBundle,
    Resolution,
    SeerrRequestState,
    SonarrState,
)
from tv_decider.seerr.planner import build_action_plan, shadow_delta

PROFILES = {"profile_name_1080p": "HD-1080p", "profile_name_2160p": "Ultra-HD"}


def _result(resolution=Resolution.P2160, confidence=Confidence.HIGH, **kwargs):
    return GuardrailResult(resolution=resolution, confidence=confidence, **kwargs)


def _evidence_with_request(**kwargs) -> EvidenceBundle:
    bundle = EvidenceBundle()
    bundle.seerr_request = SeerrRequestState(request_id=123, status="pending", **kwargs)
    bundle.facts.tvdb_id = 371980
    return bundle


def test_pending_request_gets_profile_then_approval_plan():
    actions = build_action_plan(_result(), _evidence_with_request(), **PROFILES)
    types = [a.type for a in actions]
    assert types == [
        ActionType.SET_SEERR_REQUEST_PROFILE_2160P,
        ActionType.APPROVE_SEERR_REQUEST,
        ActionType.AUDIT_SONARR_SERIES_PROFILE,
    ]
    # nothing is auto-executable by default
    assert all(a.requires_approval for a in actions if a.is_write)
    assert actions[0].params == {"seerr_request_id": 123, "profile_name": "Ultra-HD"}


def test_1080p_decision_uses_1080p_profile_action():
    actions = build_action_plan(
        _result(Resolution.P1080), _evidence_with_request(), **PROFILES
    )
    assert actions[0].type is ActionType.SET_SEERR_REQUEST_PROFILE_1080P
    assert actions[0].params["profile_name"] == "HD-1080p"


def test_auto_profile_mode_unlocks_profile_but_not_approval():
    actions = build_action_plan(
        _result(),
        _evidence_with_request(),
        **PROFILES,
        auto_profile_allowed=True,
        auto_approve_allowed=False,
    )
    by_type = {a.type: a for a in actions}
    assert not by_type[ActionType.SET_SEERR_REQUEST_PROFILE_2160P].requires_approval
    assert by_type[ActionType.APPROVE_SEERR_REQUEST].requires_approval


def test_hold_produces_only_hold_action_for_seerr_request():
    result = _result(confidence=Confidence.LOW, hold_for_review=True)
    actions = build_action_plan(result, _evidence_with_request(), **PROFILES)
    assert [a.type for a in actions] == [ActionType.HOLD_SEERR_REQUEST_FOR_MANUAL_REVIEW]
    assert not any(a.is_write for a in actions)


def test_insufficient_metadata_plan():
    result = _result(
        Resolution.P1080,
        Confidence.LOW,
        hold_for_review=True,
        insufficient_metadata=True,
    )
    actions = build_action_plan(result, EvidenceBundle(), **PROFILES)
    assert [a.type for a in actions] == [
        ActionType.INSUFFICIENT_METADATA,
        ActionType.HOLD_FOR_MANUAL_REVIEW,
    ]


def test_sonarr_fallback_only_without_seerr_request_and_on_mismatch():
    bundle = EvidenceBundle()
    bundle.sonarr = SonarrState(
        exists=True, series_id=42, quality_profile_id=6, quality_profile_name="HD-1080p"
    )
    actions = build_action_plan(_result(Resolution.P2160), bundle, **PROFILES)
    assert actions[0].type is ActionType.FALLBACK_SET_SONARR_PROFILE_2160P
    assert actions[0].requires_approval  # fallback writes are never automatic

    # matching profile -> no fallback write
    bundle.sonarr.quality_profile_name = "Ultra-HD"
    actions = build_action_plan(_result(Resolution.P2160), bundle, **PROFILES)
    assert all(not a.is_write for a in actions)


def test_shadow_delta_reports_mismatch_and_match():
    bundle = EvidenceBundle()
    bundle.sonarr = SonarrState(exists=True, quality_profile_name="HD-1080p")
    delta = shadow_delta(_result(Resolution.P2160), bundle, **PROFILES)
    assert delta is not None and delta.startswith("mismatch")

    delta = shadow_delta(_result(Resolution.P1080), bundle, **PROFILES)
    assert delta is not None and delta.startswith("match")


def test_shadow_delta_for_pending_request_without_series():
    delta = shadow_delta(_result(), _evidence_with_request(is4k=False), **PROFILES)
    assert delta is not None and "no Sonarr series yet" in delta
