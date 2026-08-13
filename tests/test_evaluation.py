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


def test_invariant_catches_ineffective_requester_context(settings):
    """Round-4 review: two independently-passing cases prove nothing. A judge
    answering an unheld 1080p to everything must FAIL the pair invariant."""
    from resolute.evaluation import check_invariants

    judge = Judge(CannedProvider(make_verdict("1080p", "high")))
    cases = [
        _case(name="A", accept={"resolutions": ["1080p"], "hold": None}),
        _case(name="B", accept={"resolutions": ["1080p", "2160p"], "hold": None}),
    ]
    results = evaluate_cases(cases, judge, settings, HouseholdPolicy(), repeat=2)
    assert all(r.passed for r in results)  # individually fine...
    invs = check_invariants(
        results, [{"type": "different_outcomes", "a": "A", "b": "B"}]
    )
    assert not invs[0].passed  # ...but the pair proves no influence
    assert "no observable effect" in invs[0].detail


def test_invariant_hold_state_is_a_distinct_outcome(settings):
    """Round-5 review: an unheld 2160p vs a HELD 2160p is a real difference —
    resolution-set comparison wrongly failed this pair."""
    from resolute.evaluation import check_invariants

    judge_a = Judge(CannedProvider(make_verdict("2160p", "high")))
    judge_b = Judge(
        CannedProvider(make_verdict("2160p", "medium", action="hold_for_manual_review"))
    )
    ra = evaluate_cases(
        [_case(name="A", accept={"resolutions": ["2160p"], "hold": False})],
        judge_a, settings, HouseholdPolicy(), repeat=2,
    )
    rb = evaluate_cases(
        [_case(name="B", accept={"resolutions": [], "hold": None,
                                 "also_acceptable_if_held": ["2160p"]})],
        judge_b, settings, HouseholdPolicy(), repeat=2,
    )
    invs = check_invariants(
        ra + rb, [{"type": "different_outcomes", "a": "A", "b": "B"}]
    )
    assert invs[0].passed


def test_invariant_rejects_unstable_operands(settings):
    """Round-5 review: an alternating operand must fail the invariant — a
    wandering baseline cannot attribute effects to the varied input."""
    from resolute.evaluation import check_invariants

    class Alternating:
        name = "alt"
        model = "alt"

        def __init__(self):
            self.n = 0

        def complete_json(self, s, u):
            self.n += 1
            return make_verdict("2160p" if self.n % 2 else "1080p", "high")

    ra = evaluate_cases(
        [_case(name="A", accept={"resolutions": ["1080p", "2160p"], "hold": False})],
        Judge(Alternating()), settings, HouseholdPolicy(), repeat=2,
    )
    rb = evaluate_cases(
        [_case(name="B", accept={"resolutions": ["1080p"], "hold": False})],
        Judge(CannedProvider(make_verdict("1080p", "high"))), settings,
        HouseholdPolicy(), repeat=2,
    )
    invs = check_invariants(
        ra + rb, [{"type": "different_outcomes", "a": "A", "b": "B"}]
    )
    assert not invs[0].passed
    assert "unstable operand" in invs[0].detail


def test_invariant_same_resolutions_detects_leak(settings):
    from resolute.evaluation import check_invariants

    class ByTitle:
        name = "bytitle"
        model = "bytitle"

        def complete_json(self, s, u):
            return make_verdict("2160p" if "Big" not in u else "1080p", "high")

    cases = [
        {"name": "base", "kind": "objective",
         "facts": {"canonical_title": "Doc", "genres": ["Documentary"]},
         "accept": {"resolutions": ["1080p", "2160p"]}},
        {"name": "burdened", "kind": "objective",
         "facts": {"canonical_title": "Doc Big", "genres": ["Documentary"]},
         "accept": {"resolutions": ["1080p", "2160p"]}},
    ]
    # objective verdicts need the objective schema
    objective = lambda res: json.dumps(
        {"objective": {"resolution": res, "confidence": "high", "reasons": ["r"]},
         "risk_flags": []}
    )

    class ByTitleObjective:
        name = "bt"
        model = "bt"

        def complete_json(self, s, u):
            return objective("1080p" if "Big" in u else "2160p")

    results = evaluate_cases(cases, Judge(ByTitleObjective()), settings, HouseholdPolicy(), repeat=1)
    invs = check_invariants(
        results, [{"type": "same_outcomes", "a": "base", "b": "burdened"}]
    )
    assert not invs[0].passed
    assert "leaked" in invs[0].detail


def test_report_carries_full_identity(settings, tmp_path):
    from resolute.evaluation import build_report, check_invariants

    judge = Judge(CannedProvider(make_verdict("2160p", "high")))
    household = HouseholdPolicy(prose="some prose")
    results = evaluate_cases([_case()], judge, settings, household, repeat=1)
    report = build_report(
        corpus_path="fixtures/eval/cases.json",
        corpus_raw='{"cases": []}',
        settings=settings,
        household=household,
        repeat=1,
        results=results,
        invariant_results=check_invariants(results, []),
    )
    assert report["model"]["prompt_version"] == "judge_v2"
    assert report["household_hash"] == household.content_hash
    assert len(report["corpus"]["sha256"]) == 16
    assert report["commit"] is None or len(report["commit"]) == 40
    assert report["cases"][0]["runs"][0]["resolution"] == "2160p"
    assert report["cases"][0]["runs"][0]["confidence"] == "high"
    assert report["summary"]["cases_passed"] == 1
