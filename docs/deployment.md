# Deployment Notes

## Kubernetes (home-ops)

`deploy/kubernetes/` follows the Flux + bjw-s app-template conventions used in
home-ops: copy the directory to `kubernetes/apps/default/tv-decider/` (with
`ks.yaml` one level above `app/`), adjust the image repository to wherever the
Docker image is published, and create a `tv-decider` item in 1Password with
`TV_DECIDER_MODEL_API_KEY` and `TV_DECIDER_WEBHOOK_SECRET`.

Components:

- **Deployment** `tv-decider`: the API, internal-only ClusterIP (no
  HTTPRoute — only Seerr and operators talk to it; add an internal route if
  you want the API reachable from the LAN).
- **CronJob** `review` (optional): nightly `POST /api/reviews/pending` sweep
  through the API. It deliberately does not mount the data PVC — the SQLite
  writer stays single (the API pod), and the endpoint only decides and
  records, never executes, so it is shadow-safe in every mode.
- **ConfigMap** `tv-decider-policy` from `app/policy.yaml`: the household
  policy. Editing it in git and letting Flux/reloader roll the pod is the
  intended calibration loop.
- **PVC** `tv-decider` (1Gi ceph-block): SQLite decision/feedback history,
  volsync-backed like other apps.

## Direct webhook (default shape)

```text
Seerr ──POST──> http://tv-decider.default.svc.cluster.local:8130/api/webhooks/seerr
```

In Seerr: Settings -> Notifications -> Webhook:

- Webhook URL: the address above.
- Custom header `X-TVD-Token: <TV_DECIDER_WEBHOOK_SECRET>` (matches
  `seerr.webhook_shared_secret`).
- JSON payload: `CANONICAL_PAYLOAD_TEMPLATE` from
  `src/tv_decider/seerr/webhook.py` (also in the README).
- Notification types: at minimum "Request Pending Approval"; optionally
  "Request Automatically Approved" (those can only be audited, not decided —
  the request has already routed).

tv-decider acknowledges and skips anything else (movies, media-available,
test notifications), storing every payload in `webhook_events` for fixture
harvesting.

## Optional Chaski relay (fanout shape)

[Chaski](https://github.com/home-operations/chaski) is a stateless webhook
relay: path routing, CEL gating, Go-template rendering, HMAC/token
verification, retries. It is useful here only when the same Seerr webhook
should reach multiple consumers:

```text
Seerr ──> Chaski ──> tv-decider /api/webhooks/seerr
                ├──> sample logger (fixture capture)
                └──> future consumer (e.g. a household concierge app)
```

Guidance:

- Point Seerr at a Chaski route; have Chaski relay the **unmodified** JSON
  body to tv-decider and inject the `X-TVD-Token` header at the relay if you
  prefer keeping the secret out of Seerr.
- Optionally gate with CEL (`payload.notification_type == "MEDIA_PENDING" &&
  payload.media.media_type == "tv"`) to cut noise; tv-decider performs the
  same filtering itself, so this is an optimization, not a requirement.
- Do **not** use Chaski as a queue, state store, or decision layer. tv-decider
  must keep working when Chaski is removed — the direct shape above is the
  supported baseline, and nothing in tv-decider knows Chaski exists.

Consult the Chaski repo for its own CR/config syntax before writing routes;
this repo deliberately ships no Chaski manifests to avoid guessing them.

## Docker (standalone)

```bash
docker build -t tv-decider .
docker run -p 8130:8130 \
  -v tvd-data:/data \
  -v $(pwd)/config/policy.yaml:/config/policy.yaml:ro \
  -e TVD_SEERR__URL=http://seerr.local \
  -e TVD_SEERR__API_KEY=... \
  -e TVD_SONARR__URL=http://sonarr.local \
  -e TVD_SONARR__API_KEY=... \
  tv-decider
```

## API authentication and network posture

Three independent credentials, all optional-but-recommended layers:

- `seerr.webhook_shared_secret` — gates `/api/webhooks/seerr` (`X-TVD-Token`).
- `execute_token` — gates `POST /api/decisions/{id}/execute`
  (`X-TVD-Operator-Token`); while unset, HTTP execution is disabled entirely
  and `tv-decider execute` (CLI, via `kubectl exec`) is the only write path.
- `api_token` — gates every other `/api/*` endpoint (`X-TVD-Api-Token`).
  Health/readiness/metrics stay open for probes and scrapers. Set this once
  the judge is enabled: decision endpoints can trigger paid model calls, and
  "internal-only ClusterIP" is a topology, not an authorization model.

For defense in depth, add a NetworkPolicy restricting ingress to Seerr, the
review CronJob, and your operator tooling, e.g.:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tv-decider
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: tv-decider
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: seerr
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: tv-decider   # review cronjob
      ports:
        - port: 8130
```

(Adjust labels/CNI specifics to your cluster; not shipped as a manifest to
avoid guessing them.)

## Single-writer constraint

SQLite is serialized in-process only. Keep exactly **one replica** and **one
uvicorn worker** (`tv-decider serve` runs one worker; the HelmRelease pins
`replicas: 1` with `strategy: Recreate`). Do not run ad hoc CLI commands that
write (`decide`, `feedback`, `execute`) against the mounted PVC while the API
pod is serving — use the HTTP API, or `kubectl exec` into the running pod so
both writers share the same process. If this service ever genuinely needs
concurrency, that is the Postgres trigger mentioned in the design docs.

## Observability

- `/healthz` (liveness), `/readyz` (readiness, proves DB access), `/metrics`
  (Prometheus text: decisions by resolution, webhook outcomes, feedback,
  executions, audits).
- Structured stdout logging; `TVD_LOG_LEVEL=DEBUG` for wire-level detail.
