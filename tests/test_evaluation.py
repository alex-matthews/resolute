"""Eval-harness mechanics with canned judges. The harness's purpose is live
models (mise run eval); these tests prove its scoring logic is trustworthy."""

import json

from conftest import CannedProvider, make_verdict

from resolute.config import HouseholdPolicy
from resolute.evaluation import evaluate_cases
from resolute.judge.judge import Judge

_EVIDENCE = {
    "facts": {"canonical_title": "Show", "genres": ["Drama"], "vote_average": 8.0}
}


def _case(**over):
    base = {
        "name": "case",
        "kind": "request",
        "request": {"title": "Show", "tmdb_id": 1},
        "evidence": _EVIDENCE,
        "accept": {"resolutions": ["2160p"], "hold": False},
    }
    base.update(over)
    return base


def test_acceptable_outcome_passes(settings):
    judge = Judge(CannedProvider(make_verdict("2160p", "high")))
    results = evaluate_cases([_case()], judge, settings, HouseholdPolicy(), repeat=2)
    assert results[0].passed
    assert results[0].outcomes == [("2160p", False), ("2160p", False)]


def test_unacceptable_resolution_fails(settings):
    judge = Judge(CannedProvider(make_verdict("1080p", "high")))
    results = evaluate_cases([_case()], judge, settings, HouseholdPolicy(), repeat=1)
    assert not results[0].passed


def test_held_alternative_acceptance(settings):
    judge = Judge(
        CannedProvider(make_verdict("2160p", "medium", action="hold_for_manual_review"))
    )
    case = _case(
        accept={"resolutions": ["1080p"], "hold": None, "also_acceptable_if_held": ["2160p"]}
    )
    results = evaluate_cases([case], judge, settings, HouseholdPolicy(), repeat=1)
    assert results[0].passed


def test_schema_failure_fails_case(settings):
    judge = Judge(CannedProvider("not json"))
    results = evaluate_cases([_case()], judge, settings, HouseholdPolicy(), repeat=1)
    assert not results[0].passed
    assert results[0].schema_failures == 1


def test_instability_fails_when_required(settings):
    class Alternating:
        name = "alt"
        model = "alt"

        def __init__(self):
            self.n = 0

        def complete_json(self, s, u):
            self.n += 1
            return make_verdict("2160p" if self.n % 2 else "1080p", "high")

    case = _case(accept={"resolutions": ["1080p", "2160p"], "hold": False}, require_stable=True)
    results = evaluate_cases([case], Judge(Alternating()), settings, HouseholdPolicy(), repeat=2)
    assert not results[0].passed
    assert any("unstable" in n for n in results[0].notes)


def test_objective_kind_uses_objective_contract(settings):
    objective = json.dumps(
        {"objective": {"resolution": "2160p", "confidence": "high", "reasons": ["merit"]},
         "risk_flags": []}
    )
    provider = CannedProvider(objective)
    case = {
        "name": "obj",
        "kind": "objective",
        "facts": {"canonical_title": "Doc", "genres": ["Documentary"]},
        "accept": {"resolutions": ["2160p"]},
    }
    results = evaluate_cases([case], Judge(provider), settings, HouseholdPolicy(), repeat=1)
    assert results[0].passed
    # the objective system prompt was used, not the request one
    assert "objective media-quality judge" in provider.calls[0][0]


def test_household_prose_override_reaches_prompt(settings):
    judge = Judge(CannedProvider(make_verdict("2160p", "high")))
    case = _case(household_prose="ALWAYS_UNIQUE_MARKER prefers 4K")
    evaluate_cases([case], judge, settings, HouseholdPolicy(prose="default"), repeat=1)
    assert "ALWAYS_UNIQUE_MARKER" in judge.provider.calls[0][1]
