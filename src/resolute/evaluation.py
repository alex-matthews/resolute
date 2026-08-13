"""Model-eval harness (ADR-0003 validation layer 3).

Ordinary CI proves the safety plumbing with canned verdicts; it cannot prove
that the configured model makes acceptable decisions. This harness runs
labeled cases against a REAL judge (live provider — costs money; invoked via
`resolute eval`, never in CI) and scores outcomes against acceptable *sets*,
hold expectations, and repeat-run stability, rather than exact prose.

Case shape (fixtures/eval/cases.json):
  kind: "request" (full engine path) | "objective" (worth invocation)
  request/evidence or facts; optional household_prose override
  accept: {resolutions: [...], hold: true|false|null,
           also_acceptable_if_held: [...]}
  require_stable: every repeat must land the same (resolution, held) pair
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import HouseholdPolicy, Settings
from .engine.engine import DecisionEngine
from .judge.judge import Judge
from .schemas import AutomationMode, DecisionRequest, EvidenceBundle, ShowFacts


@dataclass
class CaseResult:
    name: str
    outcomes: list[tuple[str, bool]] = field(default_factory=list)  # (resolution, held)
    schema_failures: int = 0
    passed: bool = False
    notes: list[str] = field(default_factory=list)
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


class _OneShotEvidence:
    def __init__(self, bundle: EvidenceBundle) -> None:
        self._bundle = bundle

    def collect(self, request: DecisionRequest) -> EvidenceBundle:
        return self._bundle.model_copy(deep=True)


def _run_once(
    case: dict, judge: Judge, settings: Settings, household: HouseholdPolicy
) -> tuple[str | None, bool, Any]:
    """Returns (resolution, held, involvement); resolution None = schema failure."""
    if case.get("kind") == "objective":
        facts = ShowFacts(**case["facts"])
        verdict, involvement = judge.judge_objective(facts)
        if verdict is None:
            return None, False, involvement
        return verdict.objective.resolution.value, False, involvement

    evidence = EvidenceBundle.model_validate(case["evidence"])
    engine = DecisionEngine(settings, household, _OneShotEvidence(evidence), judge=judge)
    decision = engine.decide(DecisionRequest(**case["request"]), AutomationMode.SHADOW)
    if decision.verdict is None:
        return None, True, decision.model_involvement
    held = any("hold" in a.type for a in decision.action_plan)
    return decision.final_resolution.value, held, decision.model_involvement


def _acceptable(case: dict, resolution: str, held: bool) -> bool:
    accept = case.get("accept", {})
    want_hold = accept.get("hold")
    if resolution in accept.get("resolutions", []):
        return want_hold is None or held == want_hold
    return held and resolution in accept.get("also_acceptable_if_held", [])


def evaluate_cases(
    cases: list[dict],
    judge: Judge,
    settings: Settings,
    default_household: HouseholdPolicy,
    repeat: int = 3,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in cases:
        result = CaseResult(name=case.get("name", "unnamed"))
        household = (
            HouseholdPolicy(prose=case["household_prose"])
            if case.get("household_prose")
            else default_household
        )
        for _ in range(max(1, repeat)):
            resolution, held, involvement = _run_once(case, judge, settings, household)
            if involvement is not None:
                result.latency_ms += involvement.latency_ms or 0
                result.tokens_in += involvement.tokens_in or 0
                result.tokens_out += involvement.tokens_out or 0
            if resolution is None:
                result.schema_failures += 1
                result.outcomes.append(("schema_failure", held))
            else:
                result.outcomes.append((resolution, held))

        ok_runs = [
            _acceptable(case, res, held)
            for res, held in result.outcomes
            if res != "schema_failure"
        ]
        result.passed = (
            result.schema_failures == 0 and bool(ok_runs) and all(ok_runs)
        )
        if case.get("require_stable") and len(set(result.outcomes)) > 1:
            result.passed = False
            result.notes.append(f"unstable across repeats: {sorted(set(result.outcomes))}")
        if result.schema_failures:
            result.notes.append(f"{result.schema_failures} schema failure(s)")
        results.append(result)
    return results
