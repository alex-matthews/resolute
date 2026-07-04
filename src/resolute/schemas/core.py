"""Core enums shared across the decision pipeline."""

from __future__ import annotations

from enum import StrEnum


class Resolution(StrEnum):
    P1080 = "1080p"
    P2160 = "2160p"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AutomationMode(StrEnum):
    """Write boundaries, from safest to most autonomous.

    shadow       - no writes; compare recommendation to current state and log the delta.
    recommend    - no writes; return/publish the recommendation and action plan.
    approve      - writes happen only via an explicit operator execute command.
    auto_profile - update the pending Seerr request profile when guardrails pass.
    auto_approve - additionally approve the Seerr request; disabled by default in config.
    """

    SHADOW = "shadow"
    RECOMMEND = "recommend"
    APPROVE = "approve"
    AUTO_PROFILE = "auto_profile"
    AUTO_APPROVE = "auto_approve"


class TriggerSource(StrEnum):
    SEERR_WEBHOOK = "seerr_webhook"
    MANUAL_CLI = "manual_cli"
    MANUAL_API = "manual_api"
    SCHEDULED_REVIEW = "scheduled_review"


class ActionType(StrEnum):
    SET_SEERR_REQUEST_PROFILE_1080P = "set_seerr_request_profile_1080p"
    SET_SEERR_REQUEST_PROFILE_2160P = "set_seerr_request_profile_2160p"
    APPROVE_SEERR_REQUEST = "approve_seerr_request"
    HOLD_SEERR_REQUEST_FOR_MANUAL_REVIEW = "hold_seerr_request_for_manual_review"
    AUDIT_SONARR_SERIES_PROFILE = "audit_sonarr_series_profile"
    FALLBACK_SET_SONARR_PROFILE_1080P = "fallback_set_sonarr_profile_1080p"
    FALLBACK_SET_SONARR_PROFILE_2160P = "fallback_set_sonarr_profile_2160p"
    HOLD_FOR_MANUAL_REVIEW = "hold_for_manual_review"
    INSUFFICIENT_METADATA = "insufficient_metadata"


# Actions that mutate Seerr or Sonarr when executed.
WRITE_ACTIONS = frozenset(
    {
        ActionType.SET_SEERR_REQUEST_PROFILE_1080P,
        ActionType.SET_SEERR_REQUEST_PROFILE_2160P,
        ActionType.APPROVE_SEERR_REQUEST,
        ActionType.FALLBACK_SET_SONARR_PROFILE_1080P,
        ActionType.FALLBACK_SET_SONARR_PROFILE_2160P,
    }
)


class FeedbackVerdict(StrEnum):
    AGREE = "agree"
    PREFER_1080P = "prefer_1080p"
    PREFER_2160P = "prefer_2160p"
    MANUAL_REVIEW = "manual_review"
