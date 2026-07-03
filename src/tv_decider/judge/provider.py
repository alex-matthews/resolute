"""Model provider abstraction. Providers return raw text; the judge validates it."""

from __future__ import annotations

from typing import Protocol

import httpx


class ProviderError(Exception):
    pass


class JudgeProvider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str) -> str:
        """Return the model's raw response text for a JSON-only completion."""
        ...


class OpenAICompatProvider:
    """OpenAI-compatible chat completions. Works with LiteLLM and most gateways."""

    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )

    def complete_json(self, system: str, user: str) -> str:
        try:
            response = self._client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"model call failed: {exc}") from exc


class StaticProvider:
    """Test/fixture provider returning canned responses in order."""

    name = "static"

    def __init__(self, responses: list[str], model: str = "static-test") -> None:
        self.model = model
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._responses:
            raise ProviderError("static provider exhausted")
        return self._responses.pop(0)
