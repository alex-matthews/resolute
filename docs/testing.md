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

## 3. Local model evals (`resolute eval` / `mise run eval` — optional dev tool)

A workstation harness, not a deployment gate: it needs the repo checkout
(corpus, git identity) and deliberately does not ship in the image. Its job
is pre-flight sanity on prompt/model changes — schema conformance, gross
taste regressions, injection resistance — before a Git commit enables the
model in shadow; **shadow (layer 4) is the quality evidence** (ADR-0003:
shadow is the calibration method, and real household requests are the real
corpus). Labeled cases (`fixtures/eval/cases.json`) run against a live
provider, spending real money, scored against

- acceptable resolution *sets* (never exact prose),
- hold expectations (with held-alternative acceptance),
- repeat-run stability (`--repeat`, default 3),
- schema-failure rate (any failure fails the case),
- **cross-case invariants** — counterfactual pairs are scored relationally,
  not independently, over complete (resolution, held) outcomes with stable
  operands: `different_outcomes` proves the varied input (requester, free
  space, prose) actually changed the outcome (a judge that answers an unheld
  1080p to everything fails the invariant even when every individual case
  passes), and `same_outcomes` proves an isolated input (episode burden on
  the objective read) did NOT leak in,
- injection resistance: hostile overviews on both the request and objective
  paths.

Household prose loading is **required** (same as production serve): an eval
against accidentally-empty prose would validate the wrong policy.

Every run writes a durable JSON report — default under the writable data
volume (`<db_path dir>/eval-reports/`; in-cluster that is `/data`, since the
rootfs is read-only), or `--report` — identifying the commit and worktree
dirtiness, corpus hash, configured and provider-reported model, prompt
version, per-case household hash, and per-run resolutions, holds,
confidences, reasons, latency, tokens, and the full per-attempt audit
(including rejected paid calls and their usage). "Reviewing the Layer 3
report" means this artifact, not scrollback.

Run it before leaving shadow mode, after changing the model or prompt version,
and when editing household prose changes expected outcomes. The harness's own
scoring and invariant logic is unit-tested in CI (`tests/test_evaluation.py`)
with canned judges — including a regression for the answers-1080p-to-everything
judge.

**Corpus honesty**: `fixtures/eval/cases.json` was authored by the model that
wrote the implementation. Its acceptable-sets encode assumptions about the
household's taste; review and expand it before treating a green eval as a
gate (this is an operator to-do in the acceptance checklist).

## 4. Live shadow evidence (`docs/rollout.md` phase 1)

Provider drift, real metadata quality, and actual household agreement cannot
be established offline. Shadow mode records every decision with full audit
(per-attempt raw output, latency, tokens, evidence and prose hashes);
`resolute calibrate` and `review-overrides` summarize agreement; the metrics
listener exposes `model_inferences_total{model}`, `model_calls_total{model}`
(billable granularity: one per provider attempt, so a retried inference
meters as two), `model_fallback_total{model}`, `model_latency_ms_sum/count`,
and `model_tokens_total{direction}`.

## What gates what

| Gate | Evidence required |
| --- | --- |
| Merging code | Layers 1–2 green in CI (the deploy template ships the model OFF and the CronJob suspended) |
| Enabling the model in shadow | A reviewed Git commit (rollout.md phase 0.5) with a LiteLLM budget in place; layer 3 is optional pre-flight |
| Leaving shadow (approve mode) | Layer 4: rollout.md phase-1 exit criteria |
| Auto modes | Layer 4 sustained (rollout.md phases 3–4) |
