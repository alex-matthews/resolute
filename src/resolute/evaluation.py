"""Model-eval harness (ADR-0003 validation layer 3).

Ordinary CI proves the safety plumbing with canned verdicts; it cannot prove
that the configured model makes acceptable decisions. This harness runs
labeled cases against a REAL judge (live provider — costs money; invoked via
`resolute eval`, never in CI), scores outcomes against acceptable *sets*,
hold expectations, repeat-run stability, and **relational invariants across
cases** — and emits a durable JSON report identifying exactly what was
evaluated (model, prompt version, household hash, corpus hash, commit).

Corpus shape (fixtures/eval/cases.json):

    {"cases": [...], "invariants": [...]}

Case:
  kind: "request" (full engine path) | "objective" (worth invocation)
  request/evidence or facts; optional household_prose override
  accept: {resolutions: [...], hold: true|false|null,
           also_acceptable_if_held: [...]}
  require_stable: every repeat must land the same (resolution, held) pair

Invariant (the piece that makes counterfactual PAIRS mean something — two
independently-passing cases prove nothing about influence):
  {"type": "different_outcomes" | "same_outcomes",
   "a": "<case name>", "b": "<case name>"}
Invariants compare complete (resolution, held) outcome sets — a hold is a
different outcome from an unheld decision. Both operand cases must be stable
across repeats (an unstable operand fails the invariant: effects cannot be
attributed to the varied input if the baseline itself wanders), and corpus
pairs must vary exactly one input. `different_outcomes` fails when the varied
input had no observable effect (e.g. a judge answering an unheld 1080p to
everything); `same_outcomes` fails when an isolated input leaked in (e.g.
episode burden shifting the objective judgment).
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .config import HouseholdPolicy, Settings
from .engine.engine import DecisionEngine
from .judge.judge import Judge
from .judge.prompts import PROMPT_VERSION
from .schemas import AutomationMode, DecisionRequest, EvidenceBundle, ShowFacts


@dataclass
class Run:
    resolution: str  # "1080p" | "2160p" | "schema_failure"
    held: bool
    confidence: str | None = None
    reasons: list[str] = field(default_factory=list)
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    # Per-attempt audit (error, raw output, tokens, provider-reported model):
    # the report is the durable record of what each paid call actually did.
    attempts: list[dict] = field(default_factory=list)


@dataclass
class CaseResult:
    name: str
    runs: list[Run] = field(default_factory=list)
    schema_failures: int = 0
    passed: bool = False
    notes: list[str] = field(default_factory=list)
    household_hash: str | None = None

    @property
    def resolution_set(self) -> frozenset[str]:
        return frozenset(r.resolution for r in self.runs)

    @property
    def outcome_set(self) -> frozenset[tuple[str, bool]]:
        return frozenset((r.resolution, r.held) for r in self.runs)

    @property
    def outcomes(self) -> list[tuple[str, bool]]:
        return [(r.resolution, r.held) for r in self.runs]


@dataclass
class InvariantResult:
    description: str
    passed: bool
    detail: str


class _OneShotEvidence:
    def __init__(self, bundle: EvidenceBundle) -> None:
        self._bundle = bundle

    def collect(self, request: DecisionRequest) -> EvidenceBundle:
        return self._bundle.model_copy(deep=True)


def _attempts_dump(involvement) -> list[dict]:
    return [a.model_dump(mode="json") for a in involvement.attempts]


def _run_once(case: dict, judge: Judge, settings: Settings, household: HouseholdPolicy) -> Run:
    if case.get("kind") == "objective":
        facts = ShowFacts(**case["facts"])
        verdict, involvement = judge.judge_objective(facts)
        if verdict is None:
            return Run(
                "schema_failure",
                False,
                latency_ms=involvement.latency_ms or 0,
                tokens_in=involvement.tokens_in or 0,
                tokens_out=involvement.tokens_out or 0,
                attempts=_attempts_dump(involvement),
            )
        return Run(
            verdict.objective.resolution.value,
            False,
            confidence=verdict.objective.confidence.value,
            reasons=list(verdict.objective.reasons),
            latency_ms=involvement.latency_ms or 0,
            tokens_in=involvement.tokens_in or 0,
            tokens_out=involvement.tokens_out or 0,
            attempts=_attempts_dump(involvement),
        )

    evidence = EvidenceBundle.model_validate(case["evidence"])
    engine = DecisionEngine(settings, household, _OneShotEvidence(evidence), judge=judge)
    decision = engine.decide(DecisionRequest(**case["request"]), AutomationMode.SHADOW)
    involvement = decision.model_involvement
    if decision.verdict is None:
        return Run(
            "schema_failure",
            True,
            latency_ms=involvement.latency_ms or 0,
            tokens_in=involvement.tokens_in or 0,
            tokens_out=involvement.tokens_out or 0,
            attempts=_attempts_dump(involvement),
        )
    return Run(
        decision.final_resolution.value,
        any("hold" in a.type for a in decision.action_plan),
        confidence=decision.confidence.value,
        reasons=list(decision.top_reasons),
        latency_ms=involvement.latency_ms or 0,
        tokens_in=involvement.tokens_in or 0,
        tokens_out=involvement.tokens_out or 0,
        attempts=_attempts_dump(involvement),
    )


def _acceptable(case: dict, run: Run) -> bool:
    accept = case.get("accept", {})
    want_hold = accept.get("hold")
    if run.resolution in accept.get("resolutions", []):
        return want_hold is None or run.held == want_hold
    return run.held and run.resolution in accept.get("also_acceptable_if_held", [])


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
        result.household_hash = household.content_hash
        for _ in range(max(1, repeat)):
            run = _run_once(case, judge, settings, household)
            if run.resolution == "schema_failure":
                result.schema_failures += 1
            result.runs.append(run)

        ok_runs = [_acceptable(case, r) for r in result.runs if r.resolution != "schema_failure"]
        result.passed = result.schema_failures == 0 and bool(ok_runs) and all(ok_runs)
        if case.get("require_stable") and len(set(result.outcomes)) > 1:
            result.passed = False
            result.notes.append(f"unstable across repeats: {sorted(set(result.outcomes))}")
        if result.schema_failures:
            result.notes.append(f"{result.schema_failures} schema failure(s)")
        results.append(result)
    return results


def check_invariants(results: list[CaseResult], invariants: list[dict]) -> list[InvariantResult]:
    by_name = {r.name: r for r in results}
    out: list[InvariantResult] = []
    for inv in invariants:
        kind, a_name, b_name = inv.get("type"), inv.get("a"), inv.get("b")
        description = f"{kind}: {a_name!r} vs {b_name!r}"
        a, b = by_name.get(a_name), by_name.get(b_name)
        if a is None or b is None:
            out.append(InvariantResult(description, False, "referenced case not found"))
            continue
        detail = f"{sorted(a.outcome_set)} vs {sorted(b.outcome_set)}"
        unstable = [r.name for r in (a, b) if len(r.outcome_set) > 1]
        if unstable:
            # Effects cannot be attributed to the varied input when an
            # operand's own outcome wanders across repeats.
            out.append(
                InvariantResult(description, False, f"unstable operand(s) {unstable}: {detail}")
            )
            continue
        if kind == "different_outcomes":
            passed = a.outcome_set != b.outcome_set
            if not passed:
                detail += " — identical: the varied input had no observable effect"
        elif kind == "same_outcomes":
            passed = a.outcome_set == b.outcome_set
            if not passed:
                detail += " — diverged: the isolated input leaked into the judgment"
        else:
            passed, detail = False, f"unknown invariant type {kind!r}"
        out.append(InvariantResult(description, passed, detail))
    return out


def _git_state() -> tuple[str | None, bool | None]:
    try:
        commit = (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()
            or None
        )
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()
        )
    except OSError:
        return None, None
    return commit, dirty


def build_report(
    *,
    corpus_path: str,
    corpus_raw: str,
    settings: Settings,
    household: HouseholdPolicy,
    repeat: int,
    results: list[CaseResult],
    invariant_results: list[InvariantResult],
) -> dict[str, Any]:
    """Durable identity of what was evaluated, against what, with what result.
    testing.md's 'Layer 3 report' is this document, not a scrollback."""
    commit, dirty = _git_state()
    reported_models = sorted(
        {
            a.get("reported_model")
            for r in results
            for run in r.runs
            for a in run.attempts
            if a.get("reported_model")
        }
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "worktree_dirty": dirty,
        "provider_reported_models": reported_models,
        "corpus": {
            "path": corpus_path,
            "sha256": hashlib.sha256(corpus_raw.encode()).hexdigest()[:16],
        },
        "model": {
            "provider": settings.judge.provider,
            "model": settings.judge.model,
            "base_url": settings.judge.base_url,
            "prompt_version": PROMPT_VERSION,
        },
        "household_hash": household.content_hash,
        "repeat": repeat,
        "cases": [
            {
                "name": r.name,
                "passed": r.passed,
                "notes": r.notes,
                "household_hash": r.household_hash,
                "runs": [
                    {
                        "resolution": run.resolution,
                        "held": run.held,
                        "confidence": run.confidence,
                        "reasons": run.reasons,
                        "latency_ms": run.latency_ms,
                        "tokens_in": run.tokens_in,
                        "tokens_out": run.tokens_out,
                        "attempts": run.attempts,
                    }
                    for run in r.runs
                ],
            }
            for r in results
        ],
        "invariants": [
            {"description": i.description, "passed": i.passed, "detail": i.detail}
            for i in invariant_results
        ],
        "summary": {
            "cases_passed": sum(1 for r in results if r.passed),
            "cases_total": len(results),
            "invariants_passed": sum(1 for i in invariant_results if i.passed),
            "invariants_total": len(invariant_results),
            "schema_failures": sum(r.schema_failures for r in results),
            "total_latency_ms": sum(run.latency_ms for r in results for run in r.runs),
            "total_tokens": sum(run.tokens_in + run.tokens_out for r in results for run in r.runs),
        },
    }
