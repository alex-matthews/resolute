"""The primary decision model (ADR-0003): builds the prompt, calls the
provider, and strictly validates the response. One retry with the validation
error echoed back; a second invalid result or an unavailable provider returns
None and the engine applies the conservative fallback (1080p + hold).

Two entry points, two invocation contracts:

- `judge_request`: the request-path decision — evidence, live operational
  facts, and the household prose.
- `judge_objective`: the ADR-0002 worth read — show facts ONLY. The signature
  accepts ShowFacts and nothing else so household context cannot reach this
  prompt (Costanza ADR-0011's anti-double-counting boundary, enforced in code).
"""

from __future__ import annotations

import json
import logging
import time

from pydantic import BaseModel, ValidationError

from ..config import HouseholdPolicy
from ..schemas import EvidenceBundle, ModelInvolvement, ModelVerdict, ObjectiveVerdict, ShowFacts
from .prompts import (
    OBJECTIVE_SYSTEM_PROMPT,
    OBJECTIVE_USER_TEMPLATE,
    PROMPT_VERSION,
    REQUEST_SYSTEM_PROMPT,
    REQUEST_USER_TEMPLATE,
)
from .provider import JudgeProvider, ProviderError

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    """Tolerate accidental markdown fences around the JSON object."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json")
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def build_request_prompt(evidence: EvidenceBundle, household: HouseholdPolicy) -> str:
    operational = {
        "seerr_request": evidence.seerr_request.model_dump(mode="json"),
        "sonarr": evidence.sonarr.model_dump(mode="json"),
        "diskspace": [d.model_dump(mode="json") for d in evidence.diskspace],
        "evidence_gaps": evidence.gaps,
    }
    return REQUEST_USER_TEMPLATE.format(
        facts_json=json.dumps(evidence.facts.model_dump(mode="json"), indent=2),
        operational_json=json.dumps(operational, indent=2),
        household_prose=household.prose.strip() or "(no household preferences supplied)",
    )


def build_objective_prompt(facts: ShowFacts) -> str:
    """Objective-only invocation: takes ShowFacts and nothing else, so the
    isolation contract is structural rather than a convention."""
    return OBJECTIVE_USER_TEMPLATE.format(
        facts_json=json.dumps(facts.model_dump(mode="json"), indent=2)
    )


class Judge:
    def __init__(self, provider: JudgeProvider) -> None:
        self.provider = provider

    def _complete_validated[V: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[V],
        involvement: ModelInvolvement,
    ) -> V | None:
        started = time.monotonic()
        last_error = ""
        for attempt in range(2):
            prompt = (
                user_prompt
                if attempt == 0
                else (
                    user_prompt
                    + "\n\nYour previous response was invalid: "
                    + last_error
                    + "\nRespond again with only the corrected JSON object."
                )
            )
            try:
                raw = self.provider.complete_json(system_prompt, prompt)
            except ProviderError as exc:
                involvement.error = str(exc)
                break
            involvement.raw_output = raw
            try:
                verdict = schema.model_validate_json(_extract_json(raw))
                involvement.latency_ms = int((time.monotonic() - started) * 1000)
                return verdict
            except ValidationError as exc:
                last_error = str(exc)[:500]
                involvement.error = f"schema validation failed: {last_error}"
                logger.warning("model output failed validation (attempt %d)", attempt + 1)
        involvement.latency_ms = int((time.monotonic() - started) * 1000)
        return None

    def judge_request(
        self, evidence: EvidenceBundle, household: HouseholdPolicy
    ) -> tuple[ModelVerdict | None, ModelInvolvement]:
        involvement = ModelInvolvement(
            used=True,
            provider=self.provider.name,
            model=self.provider.model,
            prompt_version=PROMPT_VERSION,
            evidence_hash=evidence.bundle_hash(),
            household_hash=household.content_hash,
        )
        verdict = self._complete_validated(
            REQUEST_SYSTEM_PROMPT,
            build_request_prompt(evidence, household),
            ModelVerdict,
            involvement,
        )
        return verdict, involvement

    def judge_objective(
        self, facts: ShowFacts
    ) -> tuple[ObjectiveVerdict | None, ModelInvolvement]:
        involvement = ModelInvolvement(
            used=True,
            provider=self.provider.name,
            model=self.provider.model,
            prompt_version=PROMPT_VERSION,
            evidence_hash=EvidenceBundle(facts=facts).bundle_hash(),
        )
        verdict = self._complete_validated(
            OBJECTIVE_SYSTEM_PROMPT,
            build_objective_prompt(facts),
            ObjectiveVerdict,
            involvement,
        )
        return verdict, involvement
