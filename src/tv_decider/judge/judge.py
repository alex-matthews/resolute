"""LLM judge: builds the evidence prompt, calls the provider, and strictly
validates the response. A failed judge never blocks a decision — the engine
falls back to the deterministic result with a risk flag."""

from __future__ import annotations

import json
import logging
import time

from pydantic import ValidationError

from ..config import Policy
from ..schemas import EvidenceBundle, ModelInvolvement, ModelVerdict
from ..engine.policy import PreScore
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, USER_TEMPLATE
from .provider import JudgeProvider, ProviderError

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    """Tolerate accidental markdown fences around the JSON object."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def build_user_prompt(evidence: EvidenceBundle, pre: PreScore, policy: Policy) -> str:
    components = "\n".join(
        f"- {c.name}: {c.contribution:+.2f} ({c.note})" for c in pre.components
    ) or "- none"
    return USER_TEMPLATE.format(
        evidence_json=json.dumps(evidence.facts.model_dump(mode="json"), indent=2),
        score=pre.score,
        uhd_threshold=policy.thresholds.uhd_score,
        hd_threshold=policy.thresholds.hd_score,
        objective_resolution=pre.objective.resolution,
        objective_confidence=pre.objective.confidence,
        household_resolution=pre.household.resolution,
        household_confidence=pre.household.confidence,
        components=components,
        storage_pressure=policy.storage_pressure,
        max_episodes=policy.max_episodes_2160p,
    )


class Judge:
    def __init__(self, provider: JudgeProvider) -> None:
        self.provider = provider

    def judge(
        self, evidence: EvidenceBundle, pre: PreScore, policy: Policy
    ) -> tuple[ModelVerdict | None, ModelInvolvement]:
        involvement = ModelInvolvement(
            used=True,
            provider=self.provider.name,
            model=self.provider.model,
            prompt_version=PROMPT_VERSION,
            evidence_hash=evidence.bundle_hash(),
        )
        user_prompt = build_user_prompt(evidence, pre, policy)
        started = time.monotonic()
        last_error = ""

        for attempt in range(2):
            prompt = user_prompt if attempt == 0 else (
                user_prompt
                + "\n\nYour previous response was invalid: "
                + last_error
                + "\nRespond again with only the corrected JSON object."
            )
            try:
                raw = self.provider.complete_json(SYSTEM_PROMPT, prompt)
            except ProviderError as exc:
                involvement.error = str(exc)
                break
            involvement.raw_output = raw
            try:
                verdict = ModelVerdict.model_validate_json(_extract_json(raw))
                involvement.latency_ms = int((time.monotonic() - started) * 1000)
                return verdict, involvement
            except ValidationError as exc:
                last_error = str(exc)[:500]
                involvement.error = f"schema validation failed: {last_error}"
                logger.warning("judge output failed validation (attempt %d)", attempt + 1)

        involvement.latency_ms = int((time.monotonic() - started) * 1000)
        return None, involvement
