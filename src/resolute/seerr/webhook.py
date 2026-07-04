"""Canonical Seerr webhook payload template and normalizer.

Configure the Seerr webhook (Settings -> Notifications -> Webhook) with the JSON
payload in CANONICAL_PAYLOAD_TEMPLATE. Seerr expands `{{media}}`, `{{request}}`,
and `{{extra}}` template keys into objects/arrays. Enable at least the
"Request Pending Approval" notification type.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..schemas import DecisionRequest, TriggerSource

# Paste this into the Seerr webhook JSON payload field verbatim.
CANONICAL_PAYLOAD_TEMPLATE = """\
{
    "notification_type": "{{notification_type}}",
    "event": "{{event}}",
    "subject": "{{subject}}",
    "message": "{{message}}",
    "{{media}}": "media",
    "{{request}}": "request",
    "{{extra}}": []
}
"""


class WebhookMedia(BaseModel):
    model_config = ConfigDict(extra="ignore")

    media_type: str | None = None
    tmdbId: int | None = None
    tvdbId: int | None = None
    status: str | None = None
    status4k: str | None = None


class WebhookRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id: int | None = None
    requestedBy_username: str | None = None
    requestedBy_email: str | None = None


class WebhookExtra(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    value: str | None = None


class SeerrWebhookPayload(BaseModel):
    """Shape produced by CANONICAL_PAYLOAD_TEMPLATE after Seerr template expansion."""

    model_config = ConfigDict(extra="ignore")

    notification_type: str
    event: str | None = None
    subject: str | None = None
    message: str | None = None
    media: WebhookMedia | None = None
    request: WebhookRequest | None = None
    extra: list[WebhookExtra] = Field(default_factory=list)


class WebhookRejection(Exception):
    """Payload is valid but not something resolute should decide on."""


def _parse_seasons(extra: list[WebhookExtra]) -> list[int]:
    for item in extra:
        if (item.name or "").strip().lower() == "requested seasons":
            seasons: list[int] = []
            for part in (item.value or "").split(","):
                part = part.strip()
                if part.isdigit():
                    seasons.append(int(part))
            return seasons
    return []


def normalize_webhook(
    payload: dict, trigger_notification_types: list[str]
) -> DecisionRequest:
    """Validate and convert a Seerr webhook payload into the canonical request.

    Raises WebhookRejection for payloads that should be acknowledged and skipped
    (wrong media type, non-trigger notification type, test notifications), and
    pydantic.ValidationError for structurally invalid payloads.
    """
    parsed = SeerrWebhookPayload.model_validate(payload)

    if parsed.notification_type == "TEST_NOTIFICATION":
        raise WebhookRejection("test notification")
    if parsed.notification_type not in trigger_notification_types:
        raise WebhookRejection(
            f"notification_type '{parsed.notification_type}' is not a decision trigger"
        )
    if parsed.media is None or (parsed.media.media_type or "").lower() != "tv":
        raise WebhookRejection("not a TV request")
    if parsed.media.tmdbId is None and parsed.media.tvdbId is None:
        raise WebhookRejection("webhook carries no tmdbId/tvdbId")

    title = None
    if parsed.subject:
        # Seerr subjects look like "Title (2022)"; keep the title portion.
        title = parsed.subject.rsplit("(", 1)[0].strip() or None
    year = None
    if parsed.subject and parsed.subject.rstrip().endswith(")"):
        tail = parsed.subject.rstrip()[-5:-1]
        if tail.isdigit():
            year = int(tail)

    return DecisionRequest(
        title=title,
        year=year,
        seasons=_parse_seasons(parsed.extra),
        trigger=TriggerSource.SEERR_WEBHOOK,
        requester=parsed.request.requestedBy_username if parsed.request else None,
        tmdb_id=parsed.media.tmdbId,
        tvdb_id=parsed.media.tvdbId,
        seerr_request_id=parsed.request.request_id if parsed.request else None,
    )
