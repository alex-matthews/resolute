# Build notes

## Ops reset (2026-07-05, external review "Prompt A")

Shared container-identity flaws with Costanza, fixed here in the same
pass. Authority for all ops patterns: home-ops live manifests (e.g.
bazarr/atuin helmreleases; volsync component) and
home-operations/containers (apps/tautulli) — not this repo's own
precedent.

- **Image user model (H1):** dropped `useradd -u 1032` + `chown` +
  `VOLUME`. The uid happened to match the cluster, but baking storage
  identity into the image was still the wrong pattern — identity belongs
  to Kubernetes (`runAsUser/runAsGroup/fsGroup`, already correct in
  `deploy/kubernetes/app/helmrelease.yaml`). `USER nobody:nogroup` is
  the bare-run default only; the image runs as any arbitrary uid:gid.
- **Base image (H3):** `python:3.14-slim` → `python:3.14-alpine3.24`
  (SHA-pinned), uv multi-stage kept. No musl wheel issues; the existing
  uv.lock resolved unchanged.
- **No baked config (H4):** `COPY config/policy.example.yaml
  /config/policy.yaml` removed. `load_policy(..., required=True)` on the
  production serve path fails fast with a clear error when the ConfigMap
  mount is missing (silently scoring with a default policy was the worse
  failure mode); ad-hoc CLI/fixture runs keep the tolerant default.
- **K8s-constraint smoke (H6):** `scripts/k8s-smoke.sh` + mise
  `k8s-smoke` + CI container-job step: `--user 1032:100 --read-only
  --cap-drop ALL`, no HOME, only /data writable, policy mounted
  read-only; asserts probes, DB creation, clean logs.
- **volsync posture (H7):** documented in docs/deployment.md — Snapshot
  copyMethod crash consistency, WAL sidecar expectations, concrete
  restore drill (scratch PVC → integrity_check → recency query).
- **Workflow parity audit (H9):** the action pins shared with home-ops
  (checkout, mise-action, create-github-app-token, renovate action)
  match the live workflows exactly. Intentional divergences: pinned
  `RENOVATE_VERSION` + `RENOVATE_REPOSITORIES` + mise unsafe-execution
  env (code repos need `mise lock`; home-ops itself autodiscovers and
  runs latest); docker/release-please/trivy/codeql actions have no
  home-ops counterpart and stay as adopted, SHA-pinned. No cosmetic
  churn.
- **Secrets audit (H10):** SeerrClient exceptions embed the request URL
  (via httpx), but the API key travels in the `X-Api-Key` header and
  never in the URL or params, so nothing sensitive can leak through
  exception text; the judge client likewise keys via header. Checked and
  left as-is (flagged, not refactored). The webhook shared secret is
  compared, never logged.
