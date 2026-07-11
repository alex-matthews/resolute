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
      calibration summaries
- [x] Result is presentable to any human-facing adapter (title, resolution,
      confidence, top reasons, risk flags, feedback options)
- [x] ADR-0002 Costanza seam: objective-worth read endpoint (pure, degrades to
      `unavailable`) and reclaim-to-1080p executor (report-only default,
      admin-confirm gated, write-ahead audited, exactly-once) —
      `tests/test_downgrade.py`, `tests/test_api.py`

## Safety

- [x] Default mode `shadow`; writes require `approve` / `auto_profile` /
      `auto_approve`; `auto_approve` disabled by default and double-gated —
      `tests/test_executor.py`
- [x] Auto write modes refuse to start without `seerr.webhook_shared_secret`
      (no unauthenticated write-capable webhook) — `tests/test_config.py`
- [x] `allow_writes` master switch independently blocks all writes
- [x] Model output strictly schema-validated; invalid output fails closed —
      `tests/test_judge.py`
- [x] Judge cannot override policy pins or unambiguous deterministic results —
      `tests/test_guardrails.py`
- [x] Low-confidence / held / insufficient-metadata decisions can never execute
- [x] Race avoidance: decide while pending, profile-before-approve ordering,
      no resolute-initiated Sonarr searches — `docs/adr/0001`
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
- [x] No-network tests: fixtures, provider abstraction, guardrails, planner,
      audit, engine, store, CLI, API, webhook, wire-level Seerr client, golden
      cases — `pytest` (113 tests)
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
      survive and the request routes (rollout phase 3)
- [ ] Point the image at a published registry path and pin by digest
- [ ] Keep one replica / one uvicorn worker (SQLite single-writer)
