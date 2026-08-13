# Testing strategy (v2, ADR-0003)

A nondeterministic model cannot be validated by exact-output unit tests, and
green CI must never be read as "the decisions are good." Validation is four
distinct layers, each answering a different question.

## 1. Deterministic contract tests (`pytest`, CI)

Schemas, rails, planner, executor, store, API, CLI, webhook normalizer, and
the fallback matrix — everything with an exact right answer. Model behavior is
supplied as canned verdicts, so these tests prove: *whatever the model says,
the system stays inside the safety envelope*. They prove nothing about
decision quality.

The `fixtures-test` cases (`fixtures/golden/expectations.json`) belong to this
layer: they are **canned-verdict pipeline cases** — each supplies the verdict
and asserts the rails/planner outcome, including the floor and fallback paths.
They are not a taste oracle.

## 2. Provider wire tests (`tests/test_provider_wire.py`, CI)

The shipped OpenAI-compatible adapter against `httpx.MockTransport`: exact
outgoing payload shape, and the malformed-response matrix (null content,
missing choices, HTTP errors, oversized output, LiteLLM response shape,
usage capture). Proves the adapter converts every provider misbehavior into
`ProviderError` so layer-1's fallback guarantees hold.

## 3. Model evals (`resolute eval` / `mise run eval` — live model, opt-in)

The layer CI cannot be: labeled cases (`fixtures/eval/cases.json`) run against
the **configured production model**, spending real money, scored against

- acceptable resolution *sets* (never exact prose),
- hold expectations (with held-alternative acceptance),
- repeat-run stability (`--repeat`, default 3),
- schema-failure rate (any failure fails the case),
- counterfactual pairs: same show under ample vs tight space, requester A vs
  B, changed household prose,
- injection resistance: hostile overviews on both the request and objective
  paths,
- objective/household separation: episode-burden invariance of the worth
  judgment.

Run it before leaving shadow mode, after changing the model or prompt version,
and when editing household prose changes expected outcomes. The harness's own
scoring logic is unit-tested in CI (`tests/test_evaluation.py`) with canned
judges.

## 4. Live shadow evidence (`docs/rollout.md` phase 1)

Provider drift, real metadata quality, and actual household agreement cannot
be established offline. Shadow mode records every decision with full audit
(per-attempt raw output, latency, tokens, evidence and prose hashes);
`resolute calibrate` and `review-overrides` summarize agreement; the metrics
listener exposes `model_calls_total`, `model_fallback_total`,
`model_latency_ms_sum/count`, and `model_tokens_total{direction}`.

## What gates what

| Gate | Evidence required |
| --- | --- |
| Merging code | Layers 1–2 green in CI |
| Enabling shadow with the model | Layer 3 report reviewed |
| Leaving shadow (approve mode) | Layer 4: rollout.md phase-1 exit criteria |
| Auto modes | Layer 4 sustained (rollout.md phases 3–4) |
