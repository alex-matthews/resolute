"""Wire-level Seerr client tests via httpx.MockTransport (no network).

The PUT body must preserve every routing field Seerr's route handler assigns
directly from the request body (serverId, rootFolder, languageProfileId,
tags), and must always carry seasons — the TV branch throws without them.
"""

import json

import httpx
import pytest

from resolute.seerr.client import RequestNotPendingError, SeerrClient, SeerrError

from conftest import load_fixture


def _client(handler) -> SeerrClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(base_url="http://seerr.test", transport=transport)
    return SeerrClient("http://seerr.test", "key", client=http)


def _pending_request(**overrides) -> dict:
    request = dict(load_fixture("seerr", "request_detail.json"))
    request.update(overrides)
    return request


def test_update_preserves_routing_fields_and_seasons():
    captured = {}
    current = _pending_request(languageProfileId=4, tags=[7])

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json=current)
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json=current)

    client = _client(handler)
    client.update_request_profile(123, profile_id=5)
    body = captured["body"]
    assert body["mediaType"] == "tv"
    assert body["profileId"] == 5
    assert body["seasons"] == [1]  # preserved from the current request
    assert body["serverId"] == 0
    assert body["rootFolder"] == "/data/tv"
    assert body["languageProfileId"] == 4
    assert body["tags"] == [7]
    assert body["userId"] == 2
    assert None not in body.values()  # never explicitly null a field


def test_update_uses_supplied_seasons_when_given():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json=_pending_request())
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={})

    _client(handler).update_request_profile(123, profile_id=5, seasons=[1, 2])
    assert captured["body"]["seasons"] == [1, 2]


def test_update_refuses_non_pending_request():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"  # must never reach PUT
        return httpx.Response(200, json=_pending_request(status=2))

    with pytest.raises(RequestNotPendingError):
        _client(handler).update_request_profile(123, profile_id=5)


def test_update_refuses_seasonless_request():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        return httpx.Response(200, json=_pending_request(seasons=[]))

    with pytest.raises(SeerrError, match="no seasons"):
        _client(handler).update_request_profile(123, profile_id=5)


def test_assert_pending_passes_through_pending():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_pending_request())

    assert _client(handler).assert_pending(123)["id"] == 123


def test_resolve_profile_id_via_service_discovery():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/service/sonarr":
            return httpx.Response(200, json=[{"id": 0, "name": "Sonarr", "isDefault": True}])
        return httpx.Response(200, json=load_fixture("seerr", "service_sonarr_detail.json"))

    client = _client(handler)
    assert client.resolve_profile_id("Ultra-HD") == 5
    assert client.resolve_profile_id("hd-1080p") == 6  # case-insensitive
    with pytest.raises(SeerrError, match="not found"):
        client.resolve_profile_id("SD-480p")
