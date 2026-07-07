# ADR 0002: Downgrade executor and objective-worth endpoint

Status: accepted
Date: 2026-07-07

Companion to Costanza **ADR-0011** (the cross-system authority: the council
decides a `downgrade`, Resolute executes it). This ADR specifies Resolute's
two sides of that seam: the read endpoint Costanza calls for evidence, and
the executor that applies a downgrade verdict to Sonarr.

## Context

Costanza's retention council decides `downgrade`; Resolute executes, because
it owns quality profiles and the Sonarr write path and already computes an
objective UHD-worthiness score (`engine/policy.py` emits separate `objective`
and `household` recommendations). Two seams land in Resolute:

1. an **evidence read** — Costanza pulls Resolute's objective score during
   case assembly; and
2. an **executor** — Resolute applies the verdict to Sonarr.

Today Resolute's Sonarr surface is deliberately minimal: `sonarr/client.py`
reads profiles/series and has exactly one write, `update_series_profile`,
under a standing discipline that Resolute **never triggers a Sonarr search
and never deletes files** (the client's race note). The executor below is the
one gated place that discipline is lifted — mirroring how Costanza's ADR-0009
lifts its no-external-writes rule for exactly one module.

## Verification (Sonarr behaviour that drives the design)

Sonarr does **not** downgrade in place. Re-pointing a series at a
1080p-cutoff profile leaves an existing 2160p file *above* cutoff, so Sonarr
keeps it — a profile change alone reclaims nothing. Reclaiming UHD space
therefore requires **deleting the UHD episode files** so the episodes read as
missing, after which the new profile grabs 1080p. And Sonarr will not grab
1080p while the 2160p file exists (cutoff already met), so grab-then-delete is
unavailable in vanilla Sonarr — the delete must precede the re-grab. This
shapes the verb and its guards.

## Decision

### 1. Objective-worth endpoint (read, non-destructive)

`GET /api/titles/{tvdb_id}/objective-worth` (bearer auth, like all `/api/*`)
returns Resolute's **`objective`** recommendation only — `objective_score`,
resolution, confidence, and reasons — computed on demand from current TMDB
metadata via the existing metadata path. Never `household_score` (ADR-0011's
rationale: it folds in household-subjective and storage-context terms the
council itself owns).

- Pure and side-effect-free; deterministic from metadata; records no decision.
- Takes the Sonarr-native `tvdb_id` (what Costanza's retention candidates
  carry) and resolves TMDB facts through Resolute's existing id mapping;
  returns `worth: unavailable` when metadata can't be resolved, so Costanza's
  evidence degrades gracefully rather than blocking a case (ADR-0011).
- Small and low-risk: reuses scoring, metadata, and API auth already in place.

### 2. Downgrade executor (`executors/sonarr_downgrade`, destructive, gated)

One module, one verb: **reclaim a TV series to 1080p**. Triggered by a
Costanza `downgrade` handoff carrying the decision id + `tvdb_id`
(+ target profile). Sequence:

1. **Preconditions** (any failure ⇒ `ExecutionBlocked`, reported, no writes):
   the title carries a Costanza protection; no obtainable 1080p release
   (search, don't grab — **never delete without a confirmed replacement
   path**); the series is airing or has episodes queued/downloading; the
   decision is stale; `RESOLUTE_ALLOW_WRITES=false`.
2. **Write-ahead audit** row (files to delete, estimated reclaim, Costanza
   decision id), UNIQUE per decision, *before* any destructive call — a crash
   leaves evidence and recovery never double-deletes.
3. **Reclaim:** set the 1080p-target profile → delete the UHD episode files →
   trigger a search → monitor the 1080p re-grab through to import; alert on
   failure. The brief no-file window (delete → 1080p import) is inherent to
   Sonarr's no-downgrade behaviour; it is accepted, bounded by the
   pre-verified availability and the re-grab monitoring.

Staging mirrors ADR-0009 / ADR-0011 but **starts more conservative because
this executor deletes files** (the Seerr executor only creates requests):

- **Report-only (default):** dry-run emits the exact plan — which files,
  which profile, estimated GB reclaimed — as a report; no writes. Ships here
  and stays here until proven.
- **Admin-confirm one-click (flag, ships OFF):** executes on an admin,
  server-side-identity-checked press; exactly once.
- **Capped-auto (flag, ships OFF, far future):** only after admin-confirm
  history proves the thresholds; hard-capped, cap-fallback to confirm.
- `RESOLUTE_ALLOW_WRITES=false` forces dry-run at every phase (the existing
  master switch, reused).

Scope fence: this executor reclaims-to-1080p for **Sonarr TV series only**. It
never deletes a series (Maintainerr, ADR-0003), never touches Radarr/movies
(Resolute is TV-only), and is separate from the request-time profile-set path.

## Consequences

- Resolute becomes a **bidirectional quality brain** — request-time
  up-selection and retention down-selection — under one policy vocabulary and
  one audit trail. The objective score gains a second consumer (Costanza's
  evidence) beside Resolute's own decisions.
- Resolute's Sonarr write surface grows from "profile-set only, no search, no
  delete" to "+ file-delete + search," confined to this one gated module; the
  client race-note discipline stands everywhere else.
- This is Resolute's **first destructive verb.** It ships report-only,
  guarded by the 1080p-availability precondition and re-grab monitoring, with
  a higher bar to enable than the non-destructive Seerr executor — the trust
  ladder is real, not ceremonial.
- New inbound coupling: Costanza calls the worth endpoint (soft dependency)
  and hands off downgrade decisions (hard, only once admin-confirm+ is
  enabled). Resolute owns the Sonarr risk; Costanza owns the decision and its
  reasons.

## Alternatives rejected

- **Profile change only (no delete).** Reclaims nothing — Sonarr keeps the
  above-cutoff UHD file (see Verification).
- **Grab-then-delete.** Unavailable in vanilla Sonarr (won't grab 1080p while
  2160p meets cutoff); would need advanced custom-format / downgrade-allowed
  profiles. Noted as a possible later refinement, not the baseline.
- **Delete without pre-verifying 1080p availability.** Risks destroying the
  only copy with no replacement — a non-negotiable precondition.
- **Execute from Costanza.** ADR-0011 / ADR-0003: Costanza never touches
  quality profiles; the destructive Sonarr write is Resolute's.
- **Return `household_score` as evidence.** ADR-0011: double-counts the
  household's live voice and imports storage-pressure circularity. Objective
  lane only.
