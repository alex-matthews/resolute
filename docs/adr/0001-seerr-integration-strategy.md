# ADR 0001: Seerr Integration Strategy

Status: accepted
Date: 2026-07-04

## Context

tv-decider must decide whether a Seerr TV request should use the existing
1080p or 2160p Sonarr quality profile, and apply that decision somewhere.
Three candidate strategies were identified in the preflight review:

- **A. Seerr pending-request decision**: decide while the request is pending,
  update the request's quality profile via Seerr, optionally approve it.
- **B. Seerr recommend-only**: decide and publish, never write.
- **C. Post-add Sonarr corrector**: let Seerr route with a default profile,
  then rewrite the Sonarr series profile afterwards.

## Verification

Checked against the Seerr v3 OpenAPI spec (`seerr-api.yml`, seerr-team/seerr,
deployed version v3.3.0):

- `PUT /api/v1/request/{requestId}` accepts `profileId` (with required
  `mediaType`, optional `seasons`, `serverId`, `rootFolder`); requires the
  `MANAGE_REQUESTS` permission. **TV request profiles can be changed while
  pending.**
- `POST /api/v1/request/{requestId}/{approve|decline}` transitions status;
  Seerr sends the request to Sonarr only on approval (`sendToSonarr` fires on
  the APPROVED transition).
- `GET /api/v1/service/sonarr/{id}` exposes the Sonarr server's quality
  profiles, so the two existing profile names can be resolved to IDs without
  talking to Sonarr.
- Webhook notifications include `MEDIA_PENDING` ("Request Pending Approval")
  with `{{media}}`, `{{request}}`, and `{{extra}}` template objects carrying
  `media_tmdbid`, `media_tvdbid`, `request_id`, requester fields, and
  requested seasons.
- `GET /api/v1/request?filter=pending` supports polling for scheduled review.

## Decision

**Strategy A is the write path; strategy B is the default posture.**

tv-decider listens for `MEDIA_PENDING` webhooks, decides while the request is
pending, and produces an action plan of
`set_seerr_request_profile_*` -> `approve_seerr_request`. Whether that plan
executes is governed by the automation mode (`shadow` by default; see
docs/rollout.md). Strategy C is implemented only as an explicitly-approved
fallback (`fallback_set_sonarr_profile_*`) and as the read-only audit path.

## Race avoidance

Deciding **before approval** is the race-avoidance strategy: no Sonarr series
exists until Seerr approves and routes the request, so there is no window in
which Sonarr can start searching with the wrong profile. Consequences:

1. Seerr TV auto-approval must be disabled (or scoped away from) users whose
   requests should be decided. `MEDIA_AUTO_APPROVED` events are still accepted
   as triggers, but they can only produce audit/fallback plans since the
   request has already routed.
2. The executor sets the profile *before* approving, in plan order.
3. The Sonarr fallback path never triggers a search and requires operator
   approval, because a Seerr-initiated search may already be in flight there.

## Assumptions

- The Seerr API key used has `MANAGE_REQUESTS`/admin rights.
- The two Sonarr profiles are externally managed (TRaSH/Recyclarr); tv-decider
  only references their names (config) and resolves IDs at runtime.
- A single non-4K Sonarr server holds one copy of each series and both
  profiles ("one server, per-request profile" Seerr topology).

## Consequences

- No Sonarr write in the common path; Seerr remains the source of truth.
- If a deployed Seerr version ever rejects `profileId` on pending TV requests,
  the fallback corrector and recommend-only mode still function; the ADR
  should then be revisited.
- Release-level selection (picking a specific release) is out of scope for v1:
  Sonarr exposes no supported external release-selection hook (tracked
  upstream in Sonarr/Sonarr#8396).
