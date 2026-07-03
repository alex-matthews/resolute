import json

from tv_decider.engine.features import FeatureSet
from tv_decider.engine.policy import prescore
from tv_decider.judge.judge import Judge
from tv_decider.judge.prompts import PROMPT_VERSION
from tv_decider.judge.provider import StaticProvider
from tv_decider.schemas import EvidenceBundle

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


def _judge_inputs(policy):
    evidence = EvidenceBundle()
    features = FeatureSet(title="X", genres=["drama"])
    return evidence, prescore(features, policy)


def test_valid_output_is_parsed_and_audited(policy):
    provider = StaticProvider([VALID])
    evidence, pre = _judge_inputs(policy)
    verdict, involvement = Judge(provider).judge(evidence, pre, policy)
    assert verdict is not None
    assert verdict.automation.resolution == "2160p"
    assert involvement.used
    assert involvement.provider == "static"
    assert involvement.prompt_version == PROMPT_VERSION
    assert involvement.evidence_hash == evidence.bundle_hash()
    assert involvement.raw_output == VALID


def test_markdown_fenced_json_is_tolerated(policy):
    provider = StaticProvider([f"```json\n{VALID}\n```"])
    evidence, pre = _judge_inputs(policy)
    verdict, _ = Judge(provider).judge(evidence, pre, policy)
    assert verdict is not None


def test_invalid_then_valid_retries_once(policy):
    provider = StaticProvider(['{"not": "the schema"}', VALID])
    evidence, pre = _judge_inputs(policy)
    verdict, involvement = Judge(provider).judge(evidence, pre, policy)
    assert verdict is not None
    assert len(provider.calls) == 2
    assert "invalid" in provider.calls[1][1]  # retry prompt carries the error


def test_two_invalid_responses_fail_closed(policy):
    provider = StaticProvider(['{"bad": 1}', '[1,2,3]'])
    evidence, pre = _judge_inputs(policy)
    verdict, involvement = Judge(provider).judge(evidence, pre, policy)
    assert verdict is None
    assert involvement.error is not None
    assert "schema validation failed" in involvement.error


def test_hallucinated_extra_fields_are_rejected(policy):
    tampered = json.loads(VALID)
    tampered["automation"]["execute_now"] = True  # extra field must be rejected
    provider = StaticProvider([json.dumps(tampered), json.dumps(tampered)])
    evidence, pre = _judge_inputs(policy)
    verdict, _ = Judge(provider).judge(evidence, pre, policy)
    assert verdict is None


def test_provider_failure_fails_closed(policy):
    provider = StaticProvider([])  # raises ProviderError immediately
    evidence, pre = _judge_inputs(policy)
    verdict, involvement = Judge(provider).judge(evidence, pre, policy)
    assert verdict is None
    assert "model call failed" in (involvement.error or "") or "exhausted" in (
        involvement.error or ""
    )
