# Acceptance Checklist

From the cleansheet design + handoff. Verification commands assume
`uv pip install -e '.[dev]'` in a venv.

## Functional

- [x] Given a Seerr TV request (webhook), tv-decider returns a structured
      1080p/2160p decision and action plan — `tests/test_api.py::test_webhook_decides_pending_tv_request`
- [x] Canonical Seerr webhook payload template + normalizer —
      `src/tv_decider/seerr/webhook.py`, `tests/test_webhook_normalizer.py`
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

## Safety

- [x] Default mode `shadow`; writes require `approve` / `auto_profile` /
      `auto_approve`; `auto_approve` disabled by default and double-gated —
      `tests/test_executor.py`
- [x] `allow_writes` master switch independently blocks all writes
- [x] Model output strictly schema-validated; invalid output fails closed —
      `tests/test_judge.py`
- [x] Judge cannot override policy pins or unambiguous deterministic results —
      `tests/test_guardrails.py`
- [x] Low-confidence / held / insufficient-metadata decisions can never execute
- [x] Race avoidance: decide while pending, profile-before-approve ordering,
      no tv-decider-initiated Sonarr searches — `docs/adr/0001`
- [x] Webhook shared-secret support

## Scope boundaries

- [x] No Costanza/Discord/presentation-layer dependency anywhere
- [x] No release-level AI picking (documented as unsupported upstream, ADR)
- [x] No TRaSH/profile definition ownership — selects between two existing
      profiles, resolved by name via Seerr service discovery
- [x] Chaski optional-only: direct webhook is the baseline; no hard dependency
      (`docs/deployment.md`)

## Engineering

- [x] Real package with clear modules (`src/tv_decider/...`), CLI + API over
      one engine
- [x] No-network tests: fixtures, provider abstraction, guardrails, planner,
      audit, engine, store, CLI, API, webhook, golden cases — `pytest` (83 tests)
- [x] Durable decision/feedback/audit history: SQLite on PVC + JSONL export
- [x] Dockerfile, local run commands, config examples
      (`config/*.example.yaml`), home-ops manifests (`deploy/kubernetes/`)
- [x] Shadow-mode rollout path with exit criteria (`docs/rollout.md`)
- [x] Integration-strategy ADR with verified Seerr API basis (`docs/adr/0001`)

## Deploy-time verification (operator to-do)

- [ ] Confirm actual Sonarr profile names and set
      `TVD_SEERR__PROFILE_NAME_{1080P,2160P}`
- [ ] Disable Seerr TV auto-approval for in-scope users (rollout phase 0)
- [ ] Verify `PUT /api/v1/request/{id}` accepts `profileId` on the deployed
      Seerr version with a real pending request (rollout phase 3 first write)
- [ ] Point the image at a published registry path and pin by digest
