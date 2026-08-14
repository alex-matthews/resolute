# Shadow-Mode Rollout Path

Writes are earned, not assumed. Each phase has an explicit exit criterion.

## Phase 0 — prerequisites

- Create a Seerr API key with `MANAGE_REQUESTS` (admin key works).
- Confirm the two Sonarr quality profile names as Seerr shows them
  (`GET /api/v1/service/sonarr/0`) and set
  `seerr.profile_name_1080p` / `seerr.profile_name_2160p` accordingly.
- Disable Seerr auto-approval for TV requests (or for the users in scope), so
  requests land pending. Without this, resolute can only audit after the
  fact.
- Run `resolute preflight`: verifies Seerr connectivity, resolves both
  profile names to IDs, confirms pending TV requests are visible, and lists
  Sonarr profiles. All checks must pass before moving on.

## Phase 0.5 — enabling the model is a Git commit (GitOps-native gate)

The manifests deliberately ship `RESOLUTE_JUDGE__ENABLED: "false"` **and the
review CronJob suspended**: deployed resolute runs degraded (every decision a
conservative 1080p hold), spends nothing, and writes no meaningless sweep
records. There is no imperative pre-shadow procedure — per ADR-0003, shadow
itself is the calibration method, and real household requests are the
evaluation corpus. The gate is a single reviewed commit:

- Before the commit: write the real household prose (1Password field), pin
  the image by digest, and set a conservative budget/rate limit for
  resolute's key in LiteLLM — that, not hope, is the spend bound.
- Optionally, run the local eval harness from a workstation checkout
  (`mise run eval` with `RESOLUTE_JUDGE__*` pointed at LiteLLM, e.g. via
  port-forward) as a smoke check of schema conformance and prompt sanity.
  It is a dev tool, not a gate — see docs/testing.md.
- The commit itself: flip `RESOLUTE_JUDGE__ENABLED` to `"true"` and
  unsuspend the review CronJob, with `allow_writes` still `false`. Flux
  reconciles it; the change, its author, and its rollback are all Git.

**Exit criterion:** the commit is merged and reconciled; model-backed shadow
decisions (with reasons) start accumulating on real requests.

## Phase 1 — shadow (no writes, weeks 1–2)

Shadow mode **is** the calibration method in v2 (ADR-0003): there are no
weights to tune, only prose to edit.

- Flip `RESOLUTE_JUDGE__ENABLED: "true"` (with the phase 0.5 report in hand).
  Mount the household prose (see `config/household.example.md`).
- Configure the Seerr webhook (see README) for "Request Pending Approval".
- Humans keep approving requests in Seerr exactly as before.
- resolute records a decision per request — with the model's stated reasons —
  and a `shadow_delta` comparing its recommendation to what actually
  happened.
- Record feedback: `resolute feedback last agree` / `prefer_1080p` after real
  approvals. Read the model's reasons when you disagree.
- Watch `model_fallback_total{model}`, `model_calls_total{model}` (billable
  attempts), `model_latency_ms_sum/count`, and `model_tokens_total{direction}`
  on the metrics listener, plus
  `model_unavailable` in risk flags: cost and degradation are on the normal
  path now.

**Exit criterion:** `resolute calibrate` shows ≥ 80% agreement over at least
15 decisions, and `review-overrides` shows no systematic cluster. When a
cluster exists, **edit the household prose** and keep shadowing — that is the
whole tuning loop.

## Phase 2 — approve (first writes)

- Set `mode: approve`, `allow_writes: true`, and a strong `execute_token`
  (HTTP execution stays disabled until the token exists).
- **Live contract test first**: with a throwaway pending TV request, run
  `resolute execute <decision-id> --operator alex` and verify in Seerr that
  the profile changed, seasons/root folder/server survived intact, and the
  request approved and routed. This is the one check fixtures cannot give you.
- Nothing changes automatically. When a decision looks right, execute it
  explicitly via `resolute execute` (CLI/kubectl) or
  `POST /api/decisions/{id}/execute {"operator": "alex"}` with the
  `X-Resolute-Operator-Token` header.
- If an execution fails partway (profile set but approval failed), the
  completed actions are still recorded in the `executions` table with a
  `(partial)` operator suffix — check `audit-sonarr` and re-execute or finish
  by hand in Seerr.
- The executor sets the request profile, then approves — while the request is
  still pending, so no Sonarr race exists.
- After a few requests, run `resolute audit-sonarr --decision-id ...` to
  verify the profile landed.

**Exit criterion:** ≥ 10 operator-executed decisions with zero incorrect
profiles at the Sonarr end.

## Phase 3 — auto_profile

- Set `mode: auto_profile`. Requires `seerr.webhook_shared_secret` — the
  service refuses to start in an auto write mode with an unauthenticated
  webhook, since that path executes writes.
- Pending requests get their profile set automatically when the rails pass
  (never low-confidence, never held, never on the model-unavailable
  fallback). Approval remains human, in Seerr, where it always was.

## Phase 4 — auto_approve (optional, opt-in)

- Requires both `mode: auto_approve` **and** `auto_approve_enabled: true`.
- Only worth it once override rate is negligible, because approval starts
  real downloads. Consider keeping specific requesters out via Seerr
  permissions instead of going fully automatic.

## Rollback

Any phase rolls back by setting `mode: shadow` (or flipping
`allow_writes: false`, which neuters every mode instantly). Decisions and
feedback history are unaffected.
