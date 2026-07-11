# API Examples

Base URL in-cluster: `http://resolute.default.svc.cluster.local:8080`

If `api_token` is configured, every `/api/*` call below (except the webhook,
which uses its own shared secret) additionally needs
`-H 'X-Resolute-Api-Token: <api_token>'`.

## Manual decision

```bash
curl -s -X POST localhost:8080/api/decisions \
  -H 'Content-Type: application/json' \
  -d '{"title": "Severance", "year": 2022, "tmdb_id": 95396, "requester": "alex"}'
```

```json
{
  "decision_id": "01KWMDY8125QKBT392Y06ZNSPN",
  "title": "Severance",
  "year": 2022,
  "final_resolution": "2160p",
  "confidence": "high",
  "objective": {"resolution": "2160p", "confidence": "high",
                "reasons": ["genre/keywords suggest strong visual payoff",
                             "premium network/platform production values"]},
  "household": {"resolution": "2160p", "confidence": "high", "reasons": ["..."]},
  "score": 4.0,
  "top_reasons": [
    "genre/keywords suggest strong visual payoff",
    "premium network/platform production values",
    "widely acclaimed title"
  ],
  "risk_flags": [],
  "metadata_gaps": [],
  "mode": "shadow",
  "action_plan": [
    {"type": "audit_sonarr_series_profile",
     "params": {"expected_profile_name": "Ultra-HD"},
     "requires_approval": false,
     "note": "after Seerr routes the request, verify Sonarr ended up on the expected profile"}
  ],
  "shadow_delta": null,
  "feedback_options": ["agree", "prefer_1080p", "prefer_2160p", "manual_review"],
  "model_involvement": {"used": false}
}
```

(Response truncated: the full body also carries `request`, `evidence`,
`score_components`, and `verdict` for auditability.)

## Seerr webhook (what Seerr sends)

```bash
curl -s -X POST localhost:8080/api/webhooks/seerr \
  -H 'Content-Type: application/json' \
  -H 'X-Resolute-Token: s3cret' \
  -d @fixtures/seerr/webhook_media_pending.json
```

```json
{
  "status": "decided",
  "decision_id": "01KWME0M3H8Y1RZ0Q2W7C9XKPT",
  "final_resolution": "2160p",
  "confidence": "high",
  "mode": "shadow",
  "action_plan": [
    {"type": "set_seerr_request_profile_2160p",
     "params": {"seerr_request_id": 123, "profile_name": "Ultra-HD"},
     "requires_approval": true,
     "note": "set Seerr request 123 to profile 'Ultra-HD'"},
    {"type": "approve_seerr_request",
     "params": {"seerr_request_id": 123},
     "requires_approval": true,
     "note": "approve the Seerr request so it routes to Sonarr"},
    {"type": "audit_sonarr_series_profile",
     "params": {"expected_profile_name": "Ultra-HD"},
     "requires_approval": false,
     "note": "..."}
  ],
  "executed_actions": [],
  "shadow_delta": "no Sonarr series yet; Seerr request 123 (standard lane, profile_id=6) would get 'Ultra-HD'"
}
```

## Execute a decision (approve mode)

Requires `execute_token` to be configured; while it is empty, HTTP-mediated
execution is disabled entirely (403) and only the CLI path works.

```bash
curl -s -X POST localhost:8080/api/decisions/01KWME0M3H8Y1RZ0Q2W7C9XKPT/execute \
  -H 'Content-Type: application/json' \
  -H 'X-Resolute-Operator-Token: <execute_token>' \
  -d '{"operator": "alex"}'
# -> {"decision_id": "01KWME0M...", "executed_actions":
#     ["set_seerr_request_profile_2160p", "approve_seerr_request"]}
# 403 on missing/invalid token; 409 if the decision is held, low-confidence,
# no longer pending in Seerr, or mode/allow_writes forbids it.
# 502 if a write fails mid-plan; actions that already ran are recorded in the
# executions table with operator "alex (partial)" before the error surfaces.
```

The same execution is available from the CLI (e.g. via `kubectl exec` into
the pod), which shares the write gates and partial-recording behavior:

```bash
resolute execute 01KWME0M3H8Y1RZ0Q2W7C9XKPT --operator alex
resolute execute last --operator alex --yes   # skip confirmation
```

## Scheduled review sweep

```bash
curl -s -X POST localhost:8080/api/reviews/pending
# -> {"reviewed": 2, "decisions": [
#      {"seerr_request_id": 123, "decision_id": "01KW...", "title": "Severance",
#       "final_resolution": "2160p", "confidence": "high"}, ...]}
# Decides and records every pending Seerr TV request; never executes writes.
```

## Plan from an existing Seerr request

```bash
curl -s -X POST localhost:8080/api/seerr/plan \
  -H 'Content-Type: application/json' \
  -d '{"seerr_request_id": 123}'
```

## Feedback

```bash
curl -s -X POST localhost:8080/api/feedback \
  -H 'Content-Type: application/json' \
  -d '{"decision_id": "01KWME0M3H8Y1RZ0Q2W7C9XKPT",
       "verdict": "prefer_1080p", "reason_tag": "background_watch",
       "comment": "nobody will watch this on the good TV"}'
```

## Sonarr audit

```bash
curl -s -X POST localhost:8080/api/sonarr/audit \
  -H 'Content-Type: application/json' \
  -d '{"decision_id": "01KWME0M3H8Y1RZ0Q2W7C9XKPT"}'
# -> {"series_found": true, "expected_profile": "Ultra-HD",
#     "actual_profile": "Ultra-HD", "matches": true, "note": "profile matches decision"}
```

## Calibration summary

```bash
curl -s localhost:8080/api/calibration/summary
# -> {"decisions": 42, "decisions_by_resolution": {"1080p": 30, "2160p": 12},
#     "feedback": 20, "feedback_by_verdict": {"agree": 17, "prefer_1080p": 3},
#     "override_reason_tags": {"background_watch": 2, "storage": 1},
#     "agreement_rate": 0.85}
```

## Objective worth (ADR-0002, Costanza evidence read)

```bash
curl -s localhost:8080/api/titles/371980/objective-worth
# -> {"tvdb_id": 371980, "tmdb_id": 95396, "title": "Severance",
#     "worth": "2160p", "objective_score": 3.1, "confidence": "medium",
#     "reasons": ["genre/keywords suggest strong visual payoff", ...],
#     "metadata_gaps": []}
# unresolvable ids degrade instead of erroring:
# -> {"tvdb_id": 999, "worth": "unavailable", "reason": "..."}
```

## Downgrade (ADR-0002, report-only by default)

```bash
# dry-run report: preconditions, resident 2160p, estimated GB reclaimed
curl -s -X POST localhost:8080/api/downgrades/plan \
  -H 'Content-Type: application/json' \
  -d '{"costanza_decision_id": "cz-001", "tvdb_id": 404171}'

# execution additionally requires the operator token AND allow_writes AND
# downgrade.admin_confirm_enabled (both ship off); exactly-once per decision id
curl -s -X POST localhost:8080/api/downgrades/execute \
  -H 'Content-Type: application/json' \
  -H 'X-Resolute-Operator-Token: <execute_token>' \
  -d '{"operator": "alex",
       "handoff": {"costanza_decision_id": "cz-001", "tvdb_id": 404171}}'

# the write-ahead audit record (per-step state) plus a live reconciliation of
# the reclaim's actual outcome against current Sonarr state
curl -s localhost:8080/api/downgrades/cz-001
# -> {"executed": true, "steps": ["profile_set", "search_triggered"], ...,
#     "reconciliation": {"outcome": "complete", "gb_freed_so_far": 30.0,
#                        "uhd_files_remaining": 0, "queue_items": 0, ...}}
```
