# Acceptance Checklist

From the cleansheet design + handoff. Verification commands assume
`uv sync --locked`.

## Functional

- [x] Given a Seerr TV request (webhook), resolute returns a structured
      1080p/2160p decision and action plan — `tests/test_api.py::test_webhook_decides_pending_tv_request`
- [x] Canonical Seerr webhook payload template + normalizer —
      `src/resolute/seerr/webhook.py`, `tests/test_webhook_normalizer.py`
- [x] Manual decisions (CLI `decide`, API `POST /api/decisions`)
- [x] Seerr request/profile planning (`plan-seerr`, `POST /api/seerr/plan`)
- [x] Sonarr audit + fallback planning (`audit-sonarr`, `POST /api/sonarr/audit`,
      `fallback_set_sonarr_profile_*` actions)
- [x] Scheduled library review (`review-pending`, `audit-library`, CronJob manifest)
- [x] Shadow mode compares recommendation vs current Sonarr/Seerr state without
      writing (`shadow_delta`, `tests/test_planner.py::test_shadow_delta_*`)
- [x] Feedback ingestion via CLI and API, recorded durably and used in
      shadow-review summaries
- [x] Result is presentable to any human-facing adapter (title, resolution,
      confidence, top reasons, risk flags, feedback options)
- [x] ADR-0002 Costanza seam as amended by ADR-0003: objective-worth read is
      model-derived under an objective-only invocation contract, appends an
      inference-audit row, mutates no domain state, and degrades to
      `unavailable` on metadata OR model failure; reclaim-to-1080p executor
      (report-only default, admin-confirm gated, write-ahead audited,
      exactly-once) — `tests/test_api.py`, `tests/test_downgrade.py`

## Safety

- [x] Default mode `shadow`; writes require `approve` / `auto_profile` /
      `auto_approve`; `auto_approve` disabled by default and double-gated —
      `tests/test_executor.py`
- [x] Auto write modes refuse to start without `seerr.webhook_shared_secret`
      (no unauthenticated write-capable webhook) — `tests/test_config.py`
- [x] `allow_writes` master switch independently blocks all writes
- [x] Model output strictly schema-validated (closed enums, length caps);
      invalid output retried once with a sanitized error summary, then the
      conservative fallback (1080p + hold, no write) — `tests/test_judge.py`,
      `tests/test_rails.py`
- [x] Model unavailability (disabled, unreachable, malformed provider output,
      unexpected provider exception) always degrades, never crashes or writes
      — `tests/test_engine.py`, `tests/test_provider_wire.py`
- [x] Objective-worth invocation is input-isolated: whitelisted ObjectiveFacts
      projection, no household/requester/storage/episode-cost terms —
      `tests/test_judge.py::test_objective_invocation_excludes_cost_shaped_fields`
- [x] Every model inference is audited per attempt (raw output, error,
      latency, tokens); worth inferences audit even on provider explosions —
      `tests/test_judge.py`, `tests/test_api.py`
- [x] Low-confidence / held / insufficient-metadata decisions can never execute
- [x] Race avoidance: decide while pending, profile-before-approve ordering;
      the only resolute-initiated Sonarr search is the ADR-0002 downgrade
      executor's, under its own gates — `docs/adr/0001`, `docs/adr/0002`
- [x] Pending-status enforcement: the planner emits Seerr writes only for
      pending requests, and the executor/client re-verify status at write time
      — `tests/test_planner.py`, `tests/test_seerr_client.py`, `tests/test_executor.py`
- [x] Preserving `PUT /request/{id}` body: routing fields and seasons echoed
      back, only `profileId` changed, no explicit nulls — `tests/test_seerr_client.py`
- [x] Webhook shared-secret support; execute endpoint requires a configured
      operator token (`execute_token`) and is disabled without one; optional
      `api_token` gates all other decision-producing endpoints
- [x] Partial executions are durably recorded before mid-plan failures surface
      (`ExecutionFailed.executed` → executions table with `(partial)` marker)
      — `tests/test_executor.py`, `tests/test_api.py`
- [x] CLI `execute` command provides the non-HTTP write path; CLI `preflight`
      verifies the live Seerr contract before write modes are enabled

## Scope boundaries

- [x] No Costanza/Discord/presentation-layer dependency anywhere
- [x] No release-level AI picking (documented as unsupported upstream, ADR)
- [x] No TRaSH/profile definition ownership — selects between two existing
      profiles, resolved by name via Seerr service discovery
- [x] Chaski optional-only: direct webhook is the baseline; no hard dependency
      (`docs/deployment.md`)

## Engineering

- [x] Real package with clear modules (`src/resolute/...`), CLI + API over
      one engine
- [x] No-network tests: fixtures, provider wire (MockTransport), rails,
      planner, audit, engine, store, CLI, API, webhook, wire-level Seerr
      client, eval-harness mechanics, canned-verdict pipeline cases —
      `pytest` (162 tests)
- [x] Validation layering is explicit (docs/testing.md): CI proves safety and
      integration; model quality is gated by `resolute eval` (live model,
      opt-in) and shadow evidence — never by green unit tests
- [x] Durable decision/feedback/audit history: SQLite on PVC + JSONL export
- [x] Dockerfile, local run commands, config examples
      (`config/*.example.yaml`), home-ops manifests (`deploy/kubernetes/`)
- [x] Shadow-mode rollout path with exit criteria (`docs/rollout.md`)
- [x] Integration-strategy ADR with verified Seerr API basis (`docs/adr/0001`)

## Deploy-time verification (operator to-do)

- [ ] Confirm actual Sonarr profile names and set
      `RESOLUTE_SEERR__PROFILE_NAME_{1080P,2160P}`
- [ ] Disable Seerr TV auto-approval for in-scope users (rollout phase 0)
- [ ] Run `resolute preflight` in-cluster: connectivity, profile resolution,
      pending-request visibility all green
- [ ] Live contract test with a throwaway pending request before enabling
      writes: `resolute execute` it and verify profile/seasons/root folder
      survive and the request routes (rollout phase 2)
- [ ] Point the image at a published registry path and pin by digest
- [ ] Keep one replica / one uvicorn worker (SQLite single-writer)
- [ ] Create the `RESOLUTE_HOUSEHOLD_PROSE` field on the `resolute` 1Password
      item and write the real household prose (config/household.example.md is
      a skeleton) — the pod fails fast without the mount
- [ ] Review and expand `fixtures/eval/cases.json` (authored by the
      implementing model — its acceptable-sets encode taste assumptions that
      only the household can confirm)
- [ ] Run `mise run eval` against the configured model; review the durable
      report it writes (`data/eval-reports/`) before enabling the model in
      shadow, and re-run on model/prompt/prose changes
