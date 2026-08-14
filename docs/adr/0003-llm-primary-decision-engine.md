# ADR 0003: LLM-primary decision engine

Status: accepted
Date: 2026-08-13

Retains ADR-0001 in full; amends ADR-0002 in one place — the _mechanism
premise_ of the objective-worth endpoint; the downgrade executor, its gates,
and its scope fence are untouched. See "Relationship to prior ADRs".

## Context

v1 decides between the two trusted Sonarr quality profiles with a
deterministic pipeline: feature extraction against a policy vocabulary
(`engine/features.py`), a weighted two-lane pre-score with an ambiguity band
(`engine/policy.py`), an optional LLM judge that may only resolve the
ambiguous band (`judge/`), and a guardrail layer of pins, caps, clamps, and
holds (`engine/guardrails.py`). The LLM is a bounded tiebreaker; policy
weights, thresholds, genre lists, a requester-bias table, and a hand-set
`storage_pressure` knob pre-decide most cases.

This works, but its cost is a standing operator ritual: disagreeing with a
recurring decision means recalibrating floats, thresholds, score bands, and
reason tags in `config/policy.yaml` — a growing second vocabulary that
restates household taste as tuning parameters. The knobs are also proxies for
facts the stack can report natively (e.g. `storage_pressure: medium`
hand-maintained while Sonarr's `/api/v3/diskspace` knows the actual free
space). Meanwhile the judgment the LLM is good at — "is this title, for this
household, worth a 2160p commitment?" — is confined to a narrow score band.

Resolute's identity does not change: it is a Seerr-first, evidence-backed
profile judgment service. It is not an acquisition engine, release ranker,
library scanner, continuous quality controller, or search scheduler.

## Decision

**In v2 the LLM becomes the primary decision-maker for choosing among the
small set of trusted quality profiles. Deterministic policy is reduced to a
small envelope of hard safety rails; it no longer pre-decides cases through
weights, thresholds, score bands, or a parallel policy vocabulary.**

The success criterion is not a line count: it is a material reduction in
mandatory operator ritual and tuning surface. Household preference lives
chiefly as ordinary prose, not as an accumulating collection of floats and
reason taxonomies.

### The v2 judgment path

For every canonical `DecisionRequest`:

1. collect the trusted evidence bundle (adapters may normalize facts for the
   model, but must not grow back into a disguised deterministic decision
   engine);
2. combine it with the versioned household-preference prose and relevant live
   operational facts;
3. ask the model for a strict, schema-validated verdict;
4. apply only the hard rails (below);
5. build an independently gated action plan.

Two supporting decisions:

- **Live evidence over proxy knobs.** Where a native source exists, the
  evidence bundle carries the measured fact — actual free space from Sonarr's
  `/api/v3/diskspace` rather than a hand-set `storage_pressure` level. The
  final evidence schema is not enumerated here.
- **Household policy is versioned prose.** Preferences for showcase genres,
  children's content, franchises, space sensitivity, and other taste
  judgments are written as prose, versioned, and may be supplied as sensitive
  runtime configuration rather than published structured configuration (it
  names household members). No requester table and no parallel rules
  language are rebuilt around it.

The existing strict `ModelVerdict` is the starting contract, not a
field-by-field commitment. v2 preserves what audit and planning need:
structured recommendations, reasons, confidence, risk flags/questions, and a
constrained automation result. It also preserves the _meaning_ of the
objective vs household lanes — objective media-quality merit distinct from
household-context judgment — because ADR-0002's worth seam depends on that
distinction (see below). What is removed is the deterministic scoring
machinery beneath those lanes and its calibration ritual.

### Hard rails, not deterministic lanes

The deterministic layer shrinks to a deliberately small safety envelope:

- **Closed choice set.** The model chooses only among configured, trusted
  profiles and constrained action types. It never receives a direct write
  capability; its output is a recommendation the planner and executor
  independently gate.
- **Strict validation with one retry.** A verdict that fails schema
  validation is retried once with the validation error echoed back. A second
  invalid result, or an unavailable model, yields the conservative TV
  fallback: **recommend 1080p and hold for review; do not write.**
- **Pending-state protection.** A Seerr request that is no longer pending is
  never mutated or approved; pending state is re-read at execution time, as
  ADR-0001 requires.
- **Executor authority.** The master write switch (`allow_writes`), the
  automation-mode matrix, approval gates, and plan ordering remain
  authoritative regardless of model output.
- **Bounded injection blast radius.** Untrusted evidence (TMDB overviews,
  keywords) can at worst influence a choice among profiles the operator
  already trusts, or cause a hold. It cannot invent a profile, widen
  permissions, bypass approval, or perform acquisition.

One deterministic check is retained on its individual merits — as an
evidence precondition, not another policy rail: the **metadata floor**. When
both the title and genre evidence are absent, hold for review rather than
invoking the model — there is nothing trustworthy to judge. Missing genres
alone do not block judgment when the title and other evidence are usable.
No other v1 pin, clamp, cap, threshold, or reason-taxonomy check is
grandfathered in under the label "rail"; any future deterministic rule must
justify itself individually, or it silently rebuilds the v1 tuning burden.

The fallback is intentionally _degraded operation_, not a second decision
engine. With the model unavailable, Resolute recommends 1080p and holds; it
no longer makes normal decisions. Accepting this is part of the decision.

### Seerr-first action semantics

ADR-0001's race-avoidance design remains the common path: decide while the
request is pending; set the selected profile through Seerr; approve only when
separately authorized; let Seerr and the native *arr flow perform
add-and-search. There is no explicit `search_now` action on this path.

Across all current and future entry points, **changing the desired profile
and triggering an immediate search are distinct plan verbs with independent
authorization**. A profile change must never acquire a hidden search side
effect. This is a planner-shape decision recorded now, before any future
executor exists to get it wrong.

ADR-0002's Costanza downgrade executor is the deliberate exception in
_behavior_, not in plan semantics: materializing an already-consented
replacement is its purpose, so its profile change and monitored search remain
separate audited steps executed together under its existing gates.

### The ADR-0002 objective-worth seam

ADR-0002 specifies `GET /api/titles/{tvdb_id}/objective-worth` as a pure,
deterministic objective score ("deterministic from metadata") computed by the
weighted objective lane. Removing the deterministic scoring engine removes
that premise, and this ADR supersedes it explicitly rather than silently.

**Retained contract:** a read-only, gracefully degrading evidence seam that
returns the _objective lane only_ — never household terms (Costanza
ADR-0011's rationale stands) — keyed by `tvdb_id` with the existing confirmed
tvdb→tmdb mapping, degrading to `worth: unavailable` instead of erroring so a
Costanza case assembles without it rather than blocking.

**Objective-lane isolation is an invocation contract, not an output label.**
A model can leak household context between output fields, so returning "the
objective lane" of a household-context call is not sufficient. An
objective-worth invocation receives no household policy prose, requester
preferences, storage state, title pins, episode-cost terms, votes, or
feedback: an objective-only evidence and prompt contract, enforceable in
code, sharing the v2 model infrastructure but not its household inputs. This
is what actually preserves Costanza ADR-0011's anti-double-counting boundary
(and, incidentally, keeps per-person household data out of the prompt).

**Changed mechanism:** the judgment is derived from the v2 model path's
objective lane, not from a weighted score retained solely for this endpoint.
Keeping `engine/policy.py` alive only to serve Costanza would preserve the
full calibration surface v2 exists to remove.

**Compatibility consequences, stated rather than hidden:**

- `worth` (resolution), `confidence`, `reasons`, and the `unavailable`
  degradation survive unchanged. The numeric `objective_score` field loses
  its defining semantics (a deterministic weighted sum) and is **removed at
  the v2 cutover**. No implemented Costanza consumer exists (verified
  against the Costanza repository), so no Costanza runtime migration is
  required — but removal remains an intentional breaking change to
  Resolute's documented API contract. Costanza ADR-0011 and Resolute's API
  documentation must be amended before the v2 cutover, describing
  categorical, model-derived objective evidence instead of a numeric score.
- The endpoint is no longer deterministic or bit-for-bit reproducible, and
  it now performs metered, potentially externally logged model calls — so
  "side-effect-free" is no longer strictly accurate. The precise retained
  property is: the endpoint **does not mutate request or media state, create
  a normal profile decision, or trigger an external write**. It does append
  an inference-audit record (model, prompt version, evidence hash, latency,
  validation result, raw output) — operational auditing, distinct from
  media-domain mutation. Caching remains an implementation choice; auditing
  does not, because this ADR relies on auditability to compensate for lost
  reproducibility. Model unavailability
  must map to `worth: unavailable` — the request-path 1080p-hold fallback is
  not appropriate for an evidence read.

This ADR does not redesign Costanza's council or Resolute's downgrade
executor.

### Sequencing: desired state before reconciliation

> Establish desired profile state first; measure reconciliation work second;
> choose a reconciler last.

v2 first runs in shadow on the existing request path. A later Radarr advisory
audit can then estimate proposed profile changes — its flip count is an
upper-bound sizing signal, not a search backlog. Only after trusted
corrections are applied and Recyclarr-managed profile state has settled is
there evidence to decide whether manual searches, a temporary drain, or a
permanent sweeper are warranted. **This ADR selects no sweeper (DAPS,
Houndarr, bespoke, or otherwise) and designs none inside Resolute.**

### Future Radarr / library-audit extension (constraint only)

The v2 judgment seam is intentionally shaped to later support:

- choosing between the trusted SQP-1 and SQP-4 movie strategies — these are
  _strategies_, not rungs; direction is not labeled "upgrade"/"downgrade",
  and merit is not inferred from resolution, file size, or lossless audio
  alone;
- a secondary, externally triggered existing-library audit for an individual
  title or bounded batch;
- recommending or applying a profile correction _without_ automatically
  searching (the distinct-verbs rule above).

Constraints on that future work: it must deliberately amend ADR-0002's
TV-only scope fence rather than inheriting movie writes silently. An audit
caller may optionally supply a bounded availability summary (e.g. the
presence of a specifically valued release family) as evidence, but Resolute
must not fetch, parse, score, or rank candidate releases itself, and
request-time decisions do not require availability evidence. Radarr API
verbs, availability scanning, batch scheduling, consent taxonomy, and release
handling are all out of scope here.

## Superseded v1 behavior

- The weighted two-lane pre-score, score thresholds, ambiguity band, and
  `ScoreComponent` machinery (`engine/policy.py`).
- The judge-as-bounded-tiebreaker role and the guardrail clamps that existed
  to referee it: judge confidence capping, ambiguous-band-only resolution,
  the episode-burden cap and storage-pressure block as deterministic
  overrides of the model.
- The structured policy vocabulary as decision input: weights, thresholds,
  genre/network lists, the requester-bias table, the `storage_pressure` knob,
  and the feedback reason-tag calibration loop.
- The premise (only) of ADR-0002's worth endpoint: "deterministic from
  metadata" becomes "objective-lane judgment from the v2 model path, with the
  same read-only degrading contract".

## Retained contracts and boundaries

- The pipeline shape and ownership: trigger → canonical request → evidence
  collection → decision → action plan → audited store → mode-gated executor.
- Seerr pending requests as the primary automation entry point (ADR-0001 in
  full, including PUT body semantics and race avoidance).
- Profile definitions and release preferences owned by Recyclarr/TRaSH and
  the *arrs; Sonarr/Radarr own release evaluation, ranking, grabbing,
  importing, and replacement. Resolute chooses profiles; the *arrs choose
  releases.
- The executor as the only write path, with its existing gates, audit
  records, idempotency/race protections, and shadow-first posture.
- The persistent store as the decision and execution audit trail — the v2
  pivot is not a stateless rewrite.
- The Costanza seams (worth read, downgrade executor) as explicit surfaces
  off the request-time path, not absorbed into it.

## Consequences

- **Tuning simplifies.** Disagreeing with recurring judgments normally means
  editing policy prose, not calibrating weights. Shadow mode becomes the
  calibration method: inspect decisions and reasons, adjust the prose where
  recurring disagreements emerge, then climb the existing trust ladder.
- **Bit-for-bit reproducibility is lost.** Mitigated by audit, not by a
  deterministic duplicate: stored prompt version, evidence hash/snapshot,
  provider/model, raw output, latency, validation failures, and the final
  action plan.
- **Model dependency.** Unavailability degrades to conservative 1080p-hold
  (request path) or `worth: unavailable` (evidence read), not to normal
  operation.
- **Prompt injection remains an exposure**, bounded by strict schemas, the
  trusted-profile enum, the planner, and the executor gates — the worst-case
  _action class_ is v1's: a wrong trusted profile and its downstream
  acquisition cost, or a hold. The
  exposed surface is nonetheless larger: v1's judge adjudicated only
  ambiguous-band cases, while v2's model decides every normal case, so
  untrusted evidence now reaches the decision-maker on every request even
  though the executor-bounded blast radius is unchanged.
- **Cost and latency move onto the normal decision path.** Acceptable at
  request volume (a few decisions per day), but must be observable.
- **Migration principle for stored history:** v1 `Decision` records (scores,
  components, lanes) remain readable and intelligible after the schema
  changes — versioned schemas or tolerant readers, not destructive
  rewrites. This ADR states the principle; it does not prescribe migrations.

## Open questions (deliberately not settled here)

- **External research.** Allowing the model to consult external AV forums was
  proposed in earlier discussion but is _not_ accepted as a required v2
  capability: it raises freshness, provenance, prompt-injection, latency,
  cost, and availability questions. It is deferred from the first v2 slice
  as a follow-on decision; the evidence path assumes no unrestricted web
  search. The first shadow rollout should establish whether metadata plus
  model knowledge suffices; if not, the preferred follow-on shape is
  bounded, attributable research evidence fetched and summarized outside the
  model, not unrestricted browsing.
- **Model/provider selection.** Not chosen here; the contract, failure
  behavior, and audit requirements bind regardless of the current model
  name.
- **Worth-endpoint caching.** Whether the model-derived worth read caches
  its model calls is an implementation decision to settle with Costanza's
  consumer in view (auditing them is required; see above).
- **Prose policy delivery.** The household prose is versioned and sensitive;
  the exact runtime delivery mechanism (secret mount vs. other sensitive
  configuration) is a deployment decision, not part of this record.

## Relationship to prior ADRs

- **ADR-0001** (Seerr integration strategy): retained in full; v2 changes
  who decides, not how decisions reach Seerr.
- **ADR-0002** (downgrade executor and worth endpoint): executor, gates,
  quality-list invariant, and TV-only scope fence retained; the worth
  endpoint's deterministic-scoring premise is superseded as described above.
- **Costanza ADR-0005** (LLM discipline: optional garnish, never
  load-bearing — a convention that document credits Resolute with
  establishing): not silently repealed. The boundaries are stakes-calibrated:
  Costanza's more consequential action surface remains constrained, while
  Resolute accepts model dependence for a narrow, gated choice among trusted
  profiles.
