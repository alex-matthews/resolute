# resolute

Seerr-first TV resolution policy engine for a home media stack. When a TV
request lands in Seerr, resolute decides whether it should use the existing
**1080p** or **2160p** Sonarr quality profile — an LLM judges the trusted
evidence against the household's written preferences, inside a strict schema
and a small set of hard safety rails (ADR-0003) — and produces a Seerr
request/profile action plan. It ships in **shadow mode**: it
recommends and records, and writes nothing until you explicitly enable it.

```text
Seerr (request pending) ──webhook──> resolute ──decides──> action plan
                                                    │  set_seerr_request_profile_2160p
                                                    │  approve_seerr_request
                                                    ▼
                                     executor (mode-gated) ──> Seerr API ──> Sonarr
```

Key properties:

- **Seerr is the control point.** Decisions happen while the request is
  pending, before Sonarr ever sees it — which is also the race-avoidance
  strategy (see [docs/adr/0001](docs/adr/0001-seerr-integration-strategy.md)).
- **It selects between the two existing profiles** (externally managed by
  Recyclarr/TRaSH); it never creates or edits profile definitions.
- **The LLM is the decision-maker** (ADR-0003): strictly schema-validated,
  bounded by a small set of hard rails, fully audited. With the model
  disabled or unreachable, resolute runs degraded — every normal decision
  becomes a conservative 1080p hold; there is no second decision engine.
- **Every decision, feedback event, webhook, and execution is durable**
  (SQLite on PVC); shadow mode plus feedback is the calibration method —
  disagree with a pattern, edit the household prose.
- Standalone CLI/API/service. No Discord, no Costanza, no presentation-layer
  dependency; those can consume the API later.

## Quick start (local, no network)

```bash
uv sync --locked

# run the test suite
.venv/bin/pytest

# decide against bundled fixture evidence
.venv/bin/resolute decide "Severance" --year 2022 --tmdb-id 95396 \
  --fixtures fixtures/evidence

# run the golden decision suite
.venv/bin/resolute fixtures-test
```

## Quick start (against your stack)

```bash
cp config/config.example.yaml config/config.yaml   # edit URLs/profile names
export RESOLUTE_CONFIG_FILE=config/config.yaml
export RESOLUTE_SEERR__API_KEY=...
export RESOLUTE_SONARR__API_KEY=...   # optional: enables shadow deltas + audits

resolute serve                 # API on :8080
resolute decide "Severance" --year 2022        # live metadata via Seerr
resolute plan-seerr --seerr-request-id 123     # plan for a pending request
resolute review-pending                        # sweep all pending TV requests
resolute audit-library --limit 50              # shadow-audit Sonarr drift
```

## Seerr webhook setup

Settings -> Notifications -> Webhook in Seerr:

- **URL**: `http://resolute.default.svc.cluster.local/api/webhooks/seerr`
- **Custom header**: `X-Resolute-Token: <your shared secret>` (matches
  `seerr.webhook_shared_secret`)
- **Notification types**: enable "Request Pending Approval"
- **JSON payload** (the canonical template; Seerr expands the `{{...}}` keys):

```json
{
    "notification_type": "{{notification_type}}",
    "event": "{{event}}",
    "subject": "{{subject}}",
    "message": "{{message}}",
    "{{media}}": "media",
    "{{request}}": "request",
    "{{extra}}": []
}
```

Movies, non-trigger events, and test notifications are acknowledged and
skipped; every payload is stored for fixture harvesting.

**Prerequisite**: TV requests must land *pending* (disable Seerr TV
auto-approval for in-scope users), otherwise resolute can only audit after
the fact.

## Automation modes

| Mode | Writes | Behavior |
| --- | --- | --- |
| `shadow` (default) | none | decide, log, compare against current state |
| `recommend` | none | decide and return/publish the action plan |
| `approve` | on explicit command | `POST /api/decisions/{id}/execute` runs the plan |
| `auto_profile` | automatic | sets the pending request's profile; approval stays human |
| `auto_approve` | automatic | also approves; requires `auto_approve_enabled: true` |

All writes additionally require the `allow_writes: true` master switch, and
held/low-confidence decisions never execute regardless of mode. The auto
modes also refuse to start unless the webhook shared secret is configured
(no unauthenticated write-capable endpoint). Follow
[docs/rollout.md](docs/rollout.md) — shadow first, always.

## How decisions work

1. **Evidence**: show facts via Seerr's TMDB proxy, Seerr request state,
   Sonarr series state (if it exists), and measured free space from Sonarr's
   `/diskspace`. Gaps are tracked, not guessed away; both title and genres
   missing means hold without a model call (the metadata floor).
2. **The model decides** ([docs/adr/0003](docs/adr/0003-llm-primary-decision-engine.md)):
   evidence plus the household-preference prose
   ([config/household.example.md](config/household.example.md)) go to the
   model, which returns a strict, schema-validated verdict (objective lane,
   household lane, and a constrained automation action) — one retry, then
   the conservative fallback (1080p + hold, no write).
3. **Hard rails** honor model-requested holds, hold low-confidence verdicts,
   and bound the blast radius: the model can only ever pick among trusted
   profiles or hold.
4. **Planner** emits a Seerr-first action plan; Sonarr mutation exists only as
   an operator-approved fallback plus a read-only audit. A separate,
   independently-gated retention seam
   ([docs/adr/0002](docs/adr/0002-downgrade-executor-and-worth-endpoint.md))
   serves Costanza: a model-derived objective-worth read (objective-only
   invocation contract) and a reclaim-to-1080p executor that ships
   report-only.
5. **Feedback** (`agree` / `prefer_1080p` / `prefer_2160p` / `manual_review`)
   accumulates as the shadow-mode signal for editing the prose
   ([docs/rollout.md](docs/rollout.md)).

## Repository map

```text
src/resolute/     engine, schemas, seerr/sonarr adapters, judge, store, api, cli
tests/              137 no-network tests
fixtures/           seerr/sonarr payloads, evidence bundles, golden expectations
config/             config + household prose examples
deploy/kubernetes/  Flux/app-template manifests (home-ops style)
docs/               architecture, ADRs, rollout, deployment,
                    API examples, acceptance checklist
```

## Development

Tooling is managed by [mise](https://mise.jdx.dev) (`.mise/config.toml` is the
source of truth for tool versions and tasks); dependencies are locked with
`uv.lock`.

```bash
mise install          # toolchain: python 3.14, uv, helm, kubeconform, linters
mise run sync         # uv sync --locked (incl. dev group)
mise run test         # pytest
mise run lint         # ruff
mise run golden       # golden decision fixtures
mise run kubeconform  # render app-template + validate all manifests
mise run build        # docker build (needs a Docker daemon)
mise run ci           # core CI checks (everything except the container build)
```

Runtime is Python 3.14 on a digest-pinned `python:3.14-alpine3.24` base; Renovate
(extending `home-operations/renovate-config`) maintains the base-image digest,
the uv lockfile, GitHub Action SHAs, mise tool versions, and the app-template
chart reference. Releases are cut by release-please; images are multi-arch
(amd64/arm64), SBOM-attached, provenance-attested, and cosign keyless-signed
via the shared `docker/github-builder` workflow.

See [docs/architecture.md](docs/architecture.md) for module-level detail and
[docs/api-examples.md](docs/api-examples.md) for request/response examples.
