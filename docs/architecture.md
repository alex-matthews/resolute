# Architecture

## Pipeline

Every trigger (webhook, API, CLI, scheduled review) is normalized into one
canonical `DecisionRequest`, then flows through a single engine:

```text
Seerr webhook ─┐
manual CLI ────┼─> DecisionRequest
manual API ────┤        │
scheduled ─────┘        ▼
                 EvidenceSource.collect()          (Seerr TV details, Seerr
                        │                           request state, Sonarr state)
                        ▼
                 extract_features()                (deterministic, policy vocab)
                        ▼
                 prescore()                        (objective + household lanes,
                        │                           weighted score, ambiguity band)
                        ▼
              ┌─ ambiguous or forced? ─┐
              │ yes                    │ no
              ▼                        │
        Judge (LLM, strict JSON) ──────┤           (optional; failure = fallback)
                        ▼
                 apply_guardrails()                (pins, caps, holds, clamps)
                        ▼
                 build_action_plan()               (Seerr-first; Sonarr fallback)
                        ▼
                 Decision  ──>  Store (SQLite)  ──>  Executor (mode-gated writes)
```

## Modules

| Module | Responsibility |
| --- | --- |
| `schemas/` | Pydantic contracts: request, evidence, decision, action plan, feedback, model verdict. Everything is `extra="forbid"`. |
| `config.py` | Runtime settings (env `RESOLUTE_*` / YAML) and the editable household policy file. |
| `metadata/source.py` | EvidenceSource protocol: live (Seerr + Sonarr) and fixture implementations. |
| `engine/features.py` | Evidence -> flat FeatureSet, metadata-gap detection. |
| `engine/policy.py` | Deterministic weighted pre-score, two lanes, ambiguity band. |
| `engine/guardrails.py` | Hard pins, episode/storage caps, judge clamping, hold rules. |
| `judge/` | Provider abstraction (OpenAI-compatible / static), versioned prompt, strict `ModelVerdict` validation with one retry. |
| `seerr/` | API client, canonical webhook template + normalizer, action planner. |
| `sonarr/` | API client, post-hoc profile audit, fallback correction. |
| `sonarr/downgrade.py` | ADR-0002 reclaim-to-1080p executor: report-only default, admin-confirm gated, write-ahead audited, exactly-once per Costanza decision. |
| `store/db.py` | SQLite (WAL) decisions/feedback/audits/webhook events/executions + JSONL export. |
| `executor.py` | The only write path; enforces the mode/write matrix. |
| `api/app.py`, `cli.py` | Thin adapters over the same engine. |

## Decision lanes

- **objective**: media-quality merit independent of the household (genre
  visual payoff, network tier, era, acclaim).
- **household**: objective + requester bias, franchise pins, episode burden,
  storage pressure.
- **final/automation**: household lane after guardrails — what the stack
  should actually do, expressed as an action plan.

## The judge is bounded

The LLM judge only runs inside the ambiguous score band (or when forced), and
its output is:

1. schema-validated (`ModelVerdict`, `extra="forbid"`, one retry with the
   validation error echoed back);
2. clamped by guardrails — it can resolve the ambiguous band (capped at
   medium confidence) and request holds, but it can never override policy
   pins, flip an unambiguous deterministic result, or unlock writes;
3. fully audited — provider, model, prompt version, evidence hash, raw
   output, and latency are stored with the decision.

A judge failure (network, bad JSON twice) falls back to the deterministic
result with a `model_error` risk flag; ambiguous cases then hold for a human.

Note that the evidence bundle contains **untrusted text** (TMDB overviews and
keywords are publicly editable), and it is interpolated into the judge
prompt. The clamps above are what bound a prompt-injection attempt: the worst
a malicious show description can achieve is flipping an ambiguous-band
decision between two profiles you already trust, or forcing a manual-review
hold — never a write the deterministic layer wouldn't allow.

## The Costanza seam (ADR-0002)

Two surfaces exist for the retention council, both off the request-time path:

- **`GET /api/titles/{tvdb_id}/objective-worth`** — the objective lane only
  (never household terms), computed on demand, records no decision, degrades
  to `worth: unavailable` instead of erroring. tvdb→tmdb mapping is Seerr
  search plus confirmation against `/tv/{tmdbId}` externalIds, so a wrong
  search hit can never be scored.
- **`POST /api/downgrades/plan` / `.../execute`** — the reclaim executor.
  Plan is read-only and always available; execute requires the operator
  token **and** `allow_writes` **and** `downgrade.admin_confirm_enabled`
  (both ship off), and is exactly-once per Costanza decision id via a
  write-ahead audit row that records each Sonarr step as it completes —
  an interrupted attempt resumes its remaining idempotent steps on retry,
  and `GET /api/downgrades/{id}` reconciles the actual outcome against
  live Sonarr state. The reclaim itself is Sonarr's own import-then-delete
  upgrade flow; resolute still deletes nothing.

## Write safety

Three independent gates must all open before any write:

1. `allow_writes: true` (master switch, default false);
2. a mode that permits the specific action (see `executor.py` matrix);
3. per-decision checks: not held, not low-confidence.

`auto_approve` additionally requires `auto_approve_enabled: true`.

## State

SQLite on a PVC (WAL mode). Tables: `decisions` (full Decision JSON plus
indexed columns), `feedback`, `audits`, `webhook_events` (raw payload +
outcome, which doubles as a fixture farm), `executions` (including partial
executions recorded before a mid-plan failure surfaces). `export-jsonl`
provides append-only export.

Access is serialized with an in-process lock, which makes the service a
**strict single-writer**: one replica, one uvicorn worker, no concurrent CLI
writers against the same file (see docs/deployment.md). Scaling beyond that
is the explicit trigger for the Postgres migration path. Redis/Dragonfly is intentionally absent from v1:
decision volume is a few per day, so a cache layer is not yet earning its
operational cost; idempotency is handled by the fact that re-deciding a
pending request is harmless and re-approving is a no-op.
