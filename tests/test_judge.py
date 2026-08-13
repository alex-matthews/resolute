import json

from resolute.judge.judge import Judge, build_objective_prompt, build_request_prompt
from resolute.judge.prompts import PROMPT_VERSION
from resolute.judge.provider import StaticProvider
from resolute.schemas import EvidenceBundle, ShowFacts

VALID = json.dumps(
    {
        "objective": {"resolution": "2160p", "confidence": "medium", "reasons": ["visual showcase"]},
        "household": {"resolution": "2160p", "confidence": "medium", "reasons": ["likely favorite"]},
        "automation": {
            "resolution": "2160p",
            "confidence": "medium",
            "action": "set_seerr_request_profile_2160p",
        },
        "risk_flags": ["near_threshold"],
        "questions": [],
    }
)

VALID_OBJECTIVE = json.dumps(
    {
        "objective": {"resolution": "2160p", "confidence": "high", "reasons": ["true UHD master"]},
        "risk_flags": [],
    }
)


def _evidence() -> EvidenceBundle:
    return EvidenceBundle(facts=ShowFacts(canonical_title="X", genres=["Drama"]))


def test_valid_output_is_parsed_and_audited(policy):
    provider = StaticProvider([VALID])
    evidence = _evidence()
    verdict, involvement = Judge(provider).judge_request(evidence, policy)
    assert verdict is not None
    assert verdict.automation.resolution == "2160p"
    assert involvement.used
    assert involvement.provider == "static"
    assert involvement.prompt_version == PROMPT_VERSION
    assert involvement.evidence_hash == evidence.bundle_hash()
    assert involvement.household_hash == policy.content_hash
    assert involvement.raw_output == VALID


def test_markdown_fenced_json_is_tolerated(policy):
    provider = StaticProvider([f"```json\n{VALID}\n```"])
    verdict, _ = Judge(provider).judge_request(_evidence(), policy)
    assert verdict is not None


def test_invalid_then_valid_retries_once(policy):
    provider = StaticProvider(['{"not": "the schema"}', VALID])
    verdict, _involvement = Judge(provider).judge_request(_evidence(), policy)
    assert verdict is not None
    assert len(provider.calls) == 2
    assert "invalid" in provider.calls[1][1]  # retry prompt carries the error


def test_two_invalid_responses_fail_closed(policy):
    provider = StaticProvider(['{"bad": 1}', "[1,2,3]"])
    verdict, involvement = Judge(provider).judge_request(_evidence(), policy)
    assert verdict is None
    assert involvement.error is not None
    assert "schema validation failed" in involvement.error


def test_hallucinated_extra_fields_are_rejected(policy):
    tampered = json.loads(VALID)
    tampered["automation"]["execute_now"] = True  # extra field must be rejected
    provider = StaticProvider([json.dumps(tampered), json.dumps(tampered)])
    verdict, _ = Judge(provider).judge_request(_evidence(), policy)
    assert verdict is None


def test_provider_failure_fails_closed(policy):
    provider = StaticProvider([])  # raises ProviderError immediately
    verdict, involvement = Judge(provider).judge_request(_evidence(), policy)
    assert verdict is None
    assert "model call failed" in (involvement.error or "") or "exhausted" in (
        involvement.error or ""
    )


def test_request_prompt_carries_household_prose_and_evidence(policy):
    evidence = _evidence()
    prompt = build_request_prompt(evidence, policy)
    assert "Star Wars" in prompt  # household prose present
    assert '"canonical_title": "X"' in prompt
    assert "Household preferences" in prompt


def test_objective_invocation_contract_excludes_household_context(policy):
    """ADR-0003: the objective-worth invocation receives no household context.
    The prompt builder takes ShowFacts only; the rendered prompt must not
    contain the household section or any of its prose."""
    facts = ShowFacts(canonical_title="X", genres=["Drama"])
    prompt = build_objective_prompt(facts)
    assert "Household" not in prompt
    for household_word in ("Star Wars", "Bake Off", "space is tight"):
        assert household_word not in prompt


def test_judge_objective_returns_objective_verdict_only():
    provider = StaticProvider([VALID_OBJECTIVE])
    facts = ShowFacts(canonical_title="Planet Earth II", genres=["Documentary"])
    verdict, involvement = Judge(provider).judge_objective(facts)
    assert verdict is not None
    assert verdict.objective.resolution == "2160p"
    assert involvement.household_hash is None  # no household context at all
    # A full ModelVerdict (household/automation lanes) must be rejected here.
    provider2 = StaticProvider([VALID, VALID])
    verdict2, _ = Judge(provider2).judge_objective(facts)
    assert verdict2 is None
