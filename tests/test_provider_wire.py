"""Wire-level tests for the shipped OpenAI-compatible provider via
httpx.MockTransport: exact outgoing payload and the malformed-response matrix
(adversarial review round 3)."""

import json

import httpx
import pytest

from resolute.judge.provider import OpenAICompatProvider, ProviderError


def _provider(handler) -> OpenAICompatProvider:
    client = httpx.Client(
        base_url="http://litellm.test/v1", transport=httpx.MockTransport(handler)
    )
    return OpenAICompatProvider(
        base_url="http://litellm.test/v1", api_key="k", model="test-model", client=client
    )


def _ok(content, usage=None):
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def test_outgoing_payload_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return _ok('{"x": 1}')

    provider = _provider(handler)
    out = provider.complete_json("SYS", "USER")
    assert out == '{"x": 1}'
    assert seen["path"] == "/v1/chat/completions"
    body = seen["body"]
    assert body["model"] == "test-model"
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert body["response_format"] == {"type": "json_object"}


def test_null_content_is_provider_error():
    provider = _provider(lambda req: _ok(None))
    with pytest.raises(ProviderError, match="non-string content"):
        provider.complete_json("s", "u")


def test_missing_choices_is_provider_error():
    provider = _provider(lambda req: httpx.Response(200, json={"object": "error"}))
    with pytest.raises(ProviderError, match="model call failed"):
        provider.complete_json("s", "u")


def test_http_error_is_provider_error():
    provider = _provider(lambda req: httpx.Response(503, text="down"))
    with pytest.raises(ProviderError):
        provider.complete_json("s", "u")


def test_oversized_content_is_provider_error():
    provider = _provider(lambda req: _ok("x" * 200_000))
    with pytest.raises(ProviderError, match="exceeds"):
        provider.complete_json("s", "u")


def test_usage_is_captured_and_cleared():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok('{"x": 1}', usage={"prompt_tokens": 321, "completion_tokens": 45})
        return httpx.Response(503, text="down")

    provider = _provider(handler)
    provider.complete_json("s", "u")
    assert provider.last_usage == {"prompt_tokens": 321, "completion_tokens": 45}
    with pytest.raises(ProviderError):
        provider.complete_json("s", "u")
    assert provider.last_usage is None  # cleared: stale usage never misattributed


def test_default_client_sends_auth_header_and_timeout():
    """Production client construction, not an injected one: auth header,
    base_url, and timeout must come from the constructor args."""
    provider = OpenAICompatProvider(
        base_url="http://litellm.test/v1", api_key="sekrit-key", model="m",
        timeout_seconds=7.5,
    )
    client = provider._client
    assert client.headers["Authorization"] == "Bearer sekrit-key"
    assert str(client.base_url).rstrip("/") == "http://litellm.test/v1"
    assert client.timeout.read == 7.5

    anon = OpenAICompatProvider(base_url="http://x", api_key="", model="m")
    assert "Authorization" not in anon._client.headers


def test_litellm_extra_fields_tolerated():
    def handler(req):
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": "claude-haiku-4-5",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"ok": true}'},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    assert _provider(handler).complete_json("s", "u") == '{"ok": true}'


def test_rejected_paid_response_keeps_usage_in_audit():
    """Round-5/6 review: content:null with real usage is a PAID call — the
    tokens and provider-reported model must reach the inference audit even
    though the response is rejected."""
    from resolute.judge.judge import Judge
    from resolute.schemas import ShowFacts

    def handler(req):
        return httpx.Response(
            200,
            json={
                "model": "claude-haiku-4-5-actual",
                "choices": [{"message": {"content": None}}],
                "usage": {"prompt_tokens": 321, "completion_tokens": 45},
            },
        )

    provider = _provider(handler)
    verdict, involvement = Judge(provider).judge_objective(
        ShowFacts(canonical_title="X", genres=["Drama"])
    )
    assert verdict is None
    # provider-level failures do not retry (only schema failures do), so this
    # is exactly one paid attempt — and its spend is fully accounted
    assert len(involvement.attempts) == 1
    assert involvement.tokens_in == 321
    assert involvement.tokens_out == 45
    assert involvement.attempts[0].reported_model == "claude-haiku-4-5-actual"
    assert "non-string content" in involvement.attempts[0].error
