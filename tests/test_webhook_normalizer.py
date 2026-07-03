import pytest
from pydantic import ValidationError

from tv_decider.schemas import TriggerSource
from tv_decider.seerr.webhook import WebhookRejection, normalize_webhook

TRIGGERS = ["MEDIA_PENDING", "MEDIA_AUTO_APPROVED"]


def test_normalizes_pending_tv_request(webhook_payload):
    request = normalize_webhook(webhook_payload, TRIGGERS)
    assert request.title == "Severance"
    assert request.year == 2022
    assert request.tmdb_id == 95396
    assert request.tvdb_id == 371980
    assert request.seerr_request_id == 123
    assert request.requester == "alex"
    assert request.seasons == [1]
    assert request.trigger is TriggerSource.SEERR_WEBHOOK


def test_rejects_movie_requests(movie_webhook_payload):
    with pytest.raises(WebhookRejection, match="not a TV request"):
        normalize_webhook(movie_webhook_payload, TRIGGERS)


def test_rejects_test_notification(webhook_payload):
    webhook_payload["notification_type"] = "TEST_NOTIFICATION"
    with pytest.raises(WebhookRejection, match="test notification"):
        normalize_webhook(webhook_payload, TRIGGERS)


def test_rejects_non_trigger_types(webhook_payload):
    webhook_payload["notification_type"] = "MEDIA_AVAILABLE"
    with pytest.raises(WebhookRejection, match="not a decision trigger"):
        normalize_webhook(webhook_payload, TRIGGERS)


def test_rejects_payload_without_ids(webhook_payload):
    webhook_payload["media"]["tmdbId"] = None
    webhook_payload["media"]["tvdbId"] = None
    with pytest.raises(WebhookRejection, match="no tmdbId/tvdbId"):
        normalize_webhook(webhook_payload, TRIGGERS)


def test_invalid_payload_raises_validation_error():
    with pytest.raises(ValidationError):
        normalize_webhook({"garbage": True}, TRIGGERS)


def test_multi_season_parsing(webhook_payload):
    webhook_payload["extra"] = [{"name": "Requested Seasons", "value": "1, 2, 3"}]
    request = normalize_webhook(webhook_payload, TRIGGERS)
    assert request.seasons == [1, 2, 3]
