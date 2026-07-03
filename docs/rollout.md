# Shadow-Mode Rollout Path

Writes are earned, not assumed. Each phase has an explicit exit criterion.

## Phase 0 — prerequisites

- Create a Seerr API key with `MANAGE_REQUESTS` (admin key works).
- Confirm the two Sonarr quality profile names as Seerr shows them
  (`GET /api/v1/service/sonarr/0`) and set
  `seerr.profile_name_1080p` / `seerr.profile_name_2160p` accordingly.
- Disable Seerr auto-approval for TV requests (or for the users in scope), so
  requests land pending. Without this, tv-decider can only audit after the
  fact.

## Phase 1 — shadow (no writes, weeks 1–2)

- Deploy with `mode: shadow`, `allow_writes: false`, judge disabled.
- Configure the Seerr webhook (see README) for "Request Pending Approval".
- Humans keep approving requests in Seerr exactly as before.
- tv-decider records a decision per request and a `shadow_delta` comparing its
  recommendation to what actually happened.
- Record feedback: `tv-decider feedback last agree` /
  `prefer_1080p --reason-tag ...` after each real approval.

**Exit criterion:** `tv-decider calibrate` shows ≥ 80% agreement over at least
15 decisions, and `review-overrides` shows no systematic cluster (if it does,
edit `policy.yaml` weights/pins and keep shadowing).

## Phase 2 — judge calibration (optional, parallel)

- Enable the judge (`judge.enabled: true`) pointing at LiteLLM; still shadow.
- Ambiguous-band decisions now carry model verdicts; verify `model_error`
  rate is near zero and spot-check reasons.

## Phase 3 — approve (first writes)

- Set `mode: approve`, `allow_writes: true`.
- Nothing changes automatically. When a decision looks right, execute it
  explicitly: `POST /api/decisions/{id}/execute {"operator": "alex"}`.
- The executor sets the request profile, then approves — while the request is
  still pending, so no Sonarr race exists.
- After a few requests, run `tv-decider audit-sonarr --decision-id ...` to
  verify the profile landed.

**Exit criterion:** ≥ 10 operator-executed decisions with zero incorrect
profiles at the Sonarr end.

## Phase 4 — auto_profile

- Set `mode: auto_profile`. Pending requests get their profile set
  automatically when guardrails pass (never low-confidence, never held).
  Approval remains human, in Seerr, where it always was.

## Phase 5 — auto_approve (optional, opt-in)

- Requires both `mode: auto_approve` **and** `auto_approve_enabled: true`.
- Only worth it once override rate is negligible, because approval starts
  real downloads. Consider keeping specific requesters out via Seerr
  permissions instead of going fully automatic.

## Rollback

Any phase rolls back by setting `mode: shadow` (or flipping
`allow_writes: false`, which neuters every mode instantly). Decisions and
feedback history are unaffected.
