"""Turn a guardrailed decision into a Seerr-first action plan.

The plan is a statement of intent, not execution. The executor decides what
actually runs based on the automation mode and write switches.
"""

from __future__ import annotations

from ..engine.guardrails import GuardrailResult
from ..schemas import Action, ActionType, EvidenceBundle, Resolution


def _profile_action(resolution: Resolution) -> ActionType:
    return (
        ActionType.SET_SEERR_REQUEST_PROFILE_2160P
        if resolution is Resolution.P2160
        else ActionType.SET_SEERR_REQUEST_PROFILE_1080P
    )


def _fallback_action(resolution: Resolution) -> ActionType:
    return (
        ActionType.FALLBACK_SET_SONARR_PROFILE_2160P
        if resolution is Resolution.P2160
        else ActionType.FALLBACK_SET_SONARR_PROFILE_1080P
    )


def build_action_plan(
    result: GuardrailResult,
    evidence: EvidenceBundle,
    *,
    profile_name_1080p: str,
    profile_name_2160p: str,
    auto_profile_allowed: bool = False,
    auto_approve_allowed: bool = False,
) -> list[Action]:
    actions: list[Action] = []
    request_id = evidence.seerr_request.request_id
    profile_name = (
        profile_name_2160p if result.resolution is Resolution.P2160 else profile_name_1080p
    )

    if result.insufficient_metadata:
        actions.append(
            Action(
                type=ActionType.INSUFFICIENT_METADATA,
                requires_approval=False,
                note="not enough metadata to decide safely",
            )
        )

    if result.hold_for_review:
        hold_type = (
            ActionType.HOLD_SEERR_REQUEST_FOR_MANUAL_REVIEW
            if request_id is not None
            else ActionType.HOLD_FOR_MANUAL_REVIEW
        )
        actions.append(
            Action(
                type=hold_type,
                params={"seerr_request_id": request_id} if request_id is not None else {},
                requires_approval=False,
                note="; ".join(result.notes) or "decision requires manual review",
            )
        )
        return actions

    if request_id is not None:
        actions.append(
            Action(
                type=_profile_action(result.resolution),
                params={"seerr_request_id": request_id, "profile_name": profile_name},
                requires_approval=not auto_profile_allowed,
                note=f"set Seerr request {request_id} to profile '{profile_name}'",
            )
        )
        actions.append(
            Action(
                type=ActionType.APPROVE_SEERR_REQUEST,
                params={"seerr_request_id": request_id},
                requires_approval=not auto_approve_allowed,
                note="approve the Seerr request so it routes to Sonarr",
            )
        )
    elif evidence.sonarr.exists:
        current = (evidence.sonarr.quality_profile_name or "").strip().lower()
        if current and current != profile_name.strip().lower():
            actions.append(
                Action(
                    type=_fallback_action(result.resolution),
                    params={
                        "sonarr_series_id": evidence.sonarr.series_id,
                        "profile_name": profile_name,
                    },
                    requires_approval=True,  # fallback writes are never automatic in v1
                    note=(
                        f"series already in Sonarr with profile "
                        f"'{evidence.sonarr.quality_profile_name}'; correct to '{profile_name}'"
                    ),
                )
            )

    if evidence.facts.tvdb_id or evidence.facts.tmdb_id or evidence.sonarr.exists:
        actions.append(
            Action(
                type=ActionType.AUDIT_SONARR_SERIES_PROFILE,
                params={"expected_profile_name": profile_name},
                requires_approval=False,
                note="after Seerr routes the request, verify Sonarr ended up on the expected profile",
            )
        )
    return actions


def shadow_delta(
    result: GuardrailResult,
    evidence: EvidenceBundle,
    *,
    profile_name_1080p: str,
    profile_name_2160p: str,
) -> str | None:
    """Human-readable comparison of the recommendation vs observed current state."""
    expected = (
        profile_name_2160p if result.resolution is Resolution.P2160 else profile_name_1080p
    )
    if evidence.sonarr.exists and evidence.sonarr.quality_profile_name:
        current = evidence.sonarr.quality_profile_name
        if current.strip().lower() == expected.strip().lower():
            return f"match: Sonarr already on '{current}'"
        return f"mismatch: recommend '{expected}', Sonarr currently '{current}'"
    if evidence.seerr_request.request_id is not None:
        state = "4k" if evidence.seerr_request.is4k else "standard"
        return (
            f"no Sonarr series yet; Seerr request {evidence.seerr_request.request_id} "
            f"({state} lane, profile_id={evidence.seerr_request.profile_id}) "
            f"would get '{expected}'"
        )
    return None
