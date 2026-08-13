# Architecture

## Pipeline (v2 — ADR-0003, LLM-primary)

Every trigger (webhook, API, CLI, scheduled review) is normalized into one
canonical `DecisionRequest`, then flows through a single engine:

```text
Seerr webhook ─┐
manual CLI ────┼─> DecisionRequest
manual API ────┤        │
scheduled ─────┘        ▼
                 EvidenceSource.collect()      (Seerr TV details, Seerr request
                        │                       state, Sonarr state, diskspace)
                        ▼
                 metadata floor                (title AND genres absent ->
                        │                       hold; no model call)
                        ▼
                 model verdict                 (evidence + household prose ->
                        │                       strict ModelVerdict, one retry)
                        ▼
                 hard rails                    (holds, conservative fallback)
                        ▼
                 build_action_plan()           (Seerr-first; Sonarr fallback)
                        ▼
                 Decision  ──>  Store (SQLite)  ──>  Executor (mode-gated writes)
```

The model is the decision-maker (ADR-0003). There is no weighted pre-score,
no ambiguity band, and no policy vocabulary: household preference is a prose
file the model reads verbatim, and live facts (measured free space, request
state) replace hand-maintained proxy knobs. Deterministic code is only the
safety envelope.

## Modules

| Module | Responsibility |
| --- | --- |
| `schemas/` | Pydantic contracts: request, evidence, decision, action plan, feedback, model verdicts. Everything is `extra="forbid"`. |
| `config.py` | Runtime settings (env `RESOLUTE_*` / YAML) and the household-preference prose (sensitive runtime config, Secret-mounted). |
| `metadata/source.py` | EvidenceSource protocol: live (Seerr + Sonarr, incl. `/diskspace`) and fixture implementations. |
| `engine/rails.py` | The ADR-0003 safety envelope: metadata floor, conservative fallback, hold handling. |
| `engine/engine.py` | Orchestrator: evidence → floor → model → rails → plan → Decision. |
| `judge/` | Provider abstraction, versioned prompts, strict validation with one retry. Two invocation contracts: request-path and objective-only. |
| `seerr/` | API client, canonical webhook template + normalizer, action planner. |
| `sonarr/` | API client, post-hoc profile audit, fallback correction. |
| `sonarr/downgrade.py` | ADR-0002 reclaim-to-1080p executor: report-only default, admin-confirm gated, write-ahead audited, exactly-once per Costanza decision. |
| `store/db.py` | SQLite (WAL) decisions/feedback/audits/webhook events/executions + JSONL export. |
| `executor.py` | The only write path; enforces the mode/write matrix. |
| `api/app.py`, `cli.py` | Thin adapters over the same engine. |

## The hard rails (ADR-0003)

Five rails, deliberately small — failure fallbacks, not a second decision
engine:

1. **Closed choice set.** The `ModelVerdict` schema enums are the only
   resolutions and actions the model can express; a hallucinated profile or
   verb fails validation.
2. **Strict validation, one retry.** An invalid response is retried once
   with a sanitized error summary (field locations and error types — never
   the offending values, which are untrusted model output). A second invalid
   result, or an unavailable/malformed provider, yields the conservative
   fallback: **recommend 1080p and hold for review; no write**.
3. **Pending-state protection.** Seerr writes are planned only for pending
   requests, and pending state is re-read at execution time (ADR-0001).
4. **Executor authority.** `allow_writes`, the mode matrix, approval gates,
   and plan ordering govern regardless of what the model says.
5. **Bounded blast radius.** 1+3+4 together bound untrusted evidence: a
   hostile show description can at worst pick a profile the operator already
   trusts, or cause a hold — never invent a profile, widen permissions, or
   perform acquisition.

Plus one evidence precondition: the **metadata floor** — when both title and
genre evidence are absent there is nothing trustworthy to judge, so the case
holds without spending a model call.

With the model disabled or failing, resolute runs **degraded**: every normal
decision takes the conservative fallback. That is the accepted design, not an
oversight — restoring a deterministic duplicate would restore the tuning
burden v2 removed.

## Prompt-injection posture

The evidence bundle contains **untrusted text** (TMDB overviews and keywords
are publicly editable), and in v2 it reaches the decision-maker on every
request, not only in an ambiguous band. The bound is structural: strict
schemas (rail 1), the planner, and the executor gates mean the worst case is
a wrong-but-trusted profile choice (and its downstream acquisition cost) or a
hold. The prompts also mark evidence sections as data-never-instructions,
but the guarantee lives in the rails, not the wording.

Reproducibility is handled by audit, not determinism: every model-backed
decision stores provider, model, prompt version, evidence hash, household
prose hash, and per-attempt raw output, error, latency, and token counts.
The metrics listener exposes `model_inferences_total{model}`,
`model_calls_total{model}` (one per billable provider attempt),
`model_fallback_total{model}`, `model_latency_ms_sum/count`, and
`model_tokens_total{direction}` so cost and degradation stay observable (see docs/testing.md for the validation
layering).

## The Costanza seam (ADR-0002, amended by ADR-0003)

Two surfaces exist for the retention council, both off the request-time path:

- **`GET /api/titles/{tvdb_id}/objective-worth`** — the objective lane only
  (never household terms), **model-derived** via a separate objective-only
  invocation contract: `judge_objective()` takes show facts and nothing
  else, so household prose, requester, and storage context cannot reach that
  prompt (Costanza ADR-0011's anti-double-counting boundary, enforced by
  code and tests). Returns `worth`/`confidence`/`reasons`; the v1 numeric
  `objective_score` is gone. Does not mutate request or media state and
  records no decision, but every invocation appends an inference-audit row.
  Degrades to `worth: unavailable` on unresolvable metadata **or** model
  failure. tvdb→tmdb mapping is Seerr search plus confirmation against
  `/tv/{tmdbId}` externalIds, so a wrong search hit can never be judged.
- **`POST /api/downgrades/plan` / `.../execute`** — the reclaim executor,
  untouched by the v2 pivot. Plan is read-only and always available; execute
  requires the operator token **and** `allow_writes` **and**
  `downgrade.admin_confirm_enabled` (both ship off), and is exactly-once per
  Costanza decision id via a write-ahead audit row that records each Sonarr
  step as it completes — an interrupted attempt resumes its remaining
  idempotent steps on retry, and `GET /api/downgrades/{id}` reconciles the
  actual outcome against live Sonarr state. The reclaim itself is Sonarr's
  own import-then-delete upgrade flow; resolute still deletes nothing.

## Write safety

Three independent gates must all open before any write:

1. `allow_writes: true` (master switch, default false);
2. a mode that permits the specific action (see `executor.py` matrix);
3. per-decision checks: not held, not low-confidence.

`auto_approve` additionally requires `auto_approve_enabled: true`.

Changing a desired profile and triggering a search remain **distinct plan
verbs with independent authorization** (ADR-0003): the request path has no
search verb at all (Seerr's native flow searches after approval), and the
only search resolute ever triggers is the downgrade executor's, under its own
gates.

## State

SQLite on a PVC (WAL mode). Tables: `decisions` (full Decision JSON plus
indexed columns), `feedback`, `audits` (Sonarr profile audits and worth
inference audits), `webhook_events` (raw payload + outcome, which doubles as
a fixture farm), `executions` (including partial executions recorded before a
mid-plan failure surfaces). `export-jsonl` provides append-only export.

Stored v1 decisions remain readable: the v1 scoring fields (`score`,
`score_components`) are retained as compatibility relics — new decisions
write a synthetic `score` of `0.0` (the column is NOT NULL) and an empty
component list; the CLI only displays a score when it is a real v1 record.

Access is serialized with an in-process lock, which makes the service a
**strict single-writer**: one replica, one uvicorn worker, no concurrent CLI
writers against the same file (see docs/deployment.md). Scaling beyond that
is the explicit trigger for the Postgres migration path. Redis/Dragonfly is
intentionally absent: decision volume is a few per day, so a cache layer is
not yet earning its operational cost; idempotency is handled by the fact that
re-deciding a pending request is harmless and re-approving is a no-op.
