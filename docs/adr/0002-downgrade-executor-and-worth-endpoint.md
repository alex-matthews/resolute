# ADR 0002: Downgrade executor and objective-worth endpoint

Status: accepted — implemented 2026-07-11 (worth endpoint; executor at
report-only with the admin-confirm flag shipping off; capped-auto not
built). Two implementation choices to note against the text below: the
executor records the reclaim outcome by **reconciliation on read**
(`GET /api/downgrades/{id}` compares live Sonarr state to the plan
baseline) rather than a blocking grab→import monitor, and exactly-once
means one *successful* execution — an interrupted attempt leaves a
truthful step-state audit row and a retry **resumes** the remaining
idempotent steps instead of being refused.
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
and never deletes files** (the client's race note). The executor below lifts
**only the "never triggers a search" half** of that discipline; the "never
deletes" half stands — Sonarr does the deletion, as part of its own upgrade
flow (see Verification).

## Verification (spike, 2026-07-07 — the mechanism that makes this safe)

Reclaiming was expected to require deleting the UHD files first (Sonarr won't
grab *below* cutoff). A spike against the live Sonarr disproved that for a
custom-format profile:

- Switched *The Continental (2023)* from `WEB-2160p` to `WEB-1080p`, then ran
  **Search Monitored** — no forced grab, no manual import.
- Sonarr auto-grabbed and **auto-imported** a 1080p release (Custom Format
  Score **+1780**), then logged *"File was deleted to import an upgrade"* for
  the 2160p file.

The reason (confirmed by follow-up testing, **not** custom-format-dependent):
the WEB-1080p profile does **not include 2160p in its quality list**, so the
resident 2160p file is **out-of-profile / unwanted**, and Sonarr replaces it
with any in-profile 1080p — a **quality-tier** decision, not a scoring contest.
The +1780 CF score in the first spike was the release-group tier, incidental to
the swap. Sonarr's replace path is **import-then-delete**: the replacement
lands first, the old file is removed second — so there is **no delete-first
step, no no-file window, and no "deleted the only copy with no replacement"
risk** (if no 1080p is ever obtainable, the 2160p simply stays). Resolute never
deletes anything; Sonarr does, safely, as it does for any upgrade.

Two properties of this that the design must respect:

- **Quality-list invariant (load-bearing).** It works because the 1080p-target
  profile **excludes 2160p from its quality list**, making the resident file
  out-of-profile. This is a **checkable config property** (inspect the
  profile's quality list) — not a fragile scoring margin, and confirmed not
  CF-dependent. A profile that *includes* 2160p above 1080p would need a CF
  contest instead and generally would not reclaim (that is the fallback's
  domain).
- **Reclaim vs Recycle Bin.** "Deleted to import an upgrade" honours Sonarr's
  Recycle Bin. This deployment runs **no Recycle Bin**, so the delete frees
  disk immediately and reclaim is single-step (confirmed 2026-07-07). If a
  Recycle Bin is ever configured, reclaim becomes deferred until the bin is
  cleared, and the executor's "estimated GB reclaimed" would have to account
  for it.

## Decision

### 1. Objective-worth endpoint (read, non-destructive)

`GET /api/titles/{tvdb_id}/objective-worth` (bearer auth, like all `/api/*`)
returns Resolute's **`objective`** recommendation only — `objective_score`,
resolution, confidence, and reasons — computed on demand from current TMDB
metadata via the existing metadata path. Never `household_score` (ADR-0011's
rationale: it folds in household-subjective and storage-context terms the
council itself owns).

- Pure and side-effect-free; deterministic from metadata; records no decision.
- Takes the Sonarr-native `tvdb_id` and resolves TMDB facts through Resolute's
  existing id mapping; returns `worth: unavailable` when metadata can't be
  resolved, so Costanza's evidence degrades gracefully rather than blocking a
  case (ADR-0011).
- Small and low-risk: reuses scoring, metadata, and API auth already in place.

### 2. Downgrade executor (`executors/sonarr_downgrade`, gated)

One module, one verb: **reclaim a TV series to 1080p** — implemented as
Sonarr's own upgrade flow, not a hand-rolled delete. Triggered by a Costanza
`downgrade` handoff carrying the decision id + `tvdb_id` (+ target profile).
Sequence:

1. **Preconditions** — execution refuses (blocked, reported, no writes) when
   any of these holds: the title carries a Costanza protection; the target
   profile still lists 2160p (a misconfiguration for reclaim — leaves the
   resident in-profile; the executor verifies the profile *excludes* 2160p);
   the series is airing or has episodes queued/downloading; the decision is
   stale; `RESOLUTE_ALLOW_WRITES=false`. Note: *no 1080p available* is
   **not** a blocker — Sonarr deletes only on import, so the 2160p is
   retained and the run simply reports zero reclaim.
2. **Write-ahead audit** row (target profile, resident files, expected
   reclaim, Costanza decision id), UNIQUE per decision, before any write.
3. **Reclaim:** `update_series_profile` → the 1080p-target profile, then
   trigger a monitored search. Sonarr's normal upgrade path grabs 1080p,
   imports it, and deletes the replaced 2160p file (import-then-delete). The
   executor monitors the grab→import to completion and records the outcome;
   Resolute itself deletes nothing.

Staging mirrors ADR-0009 / ADR-0011. The blast radius is smaller than first
feared (Resolute's write is profile-set + search-trigger; the destructive
delete is Sonarr's vetted upgrade-replace), but it *does* cause file deletion
downstream, so it still climbs the ladder rather than shipping hot:

- **Report-only (default):** dry-run emits the exact plan — target profile,
  the 1080p release that would win, estimated GB reclaimed — as a report; no
  writes.
- **Admin-confirm one-click (flag, ships OFF):** executes on an admin,
  server-side-identity-checked press; exactly once.
- **Capped-auto (flag, ships OFF, far future):** only after admin-confirm
  history proves it; hard-capped, cap-fallback to confirm.
- `RESOLUTE_ALLOW_WRITES=false` forces dry-run at every phase.

Scope fence: reclaims-to-1080p for **Sonarr TV series only**. Never deletes a
series (Maintainerr, ADR-0003), never touches Radarr/movies (Resolute is
TV-only), separate from the request-time profile-set path.

Note (quality semantics, not mechanism): a 2160p→1080p reclaim also drops HDR
(HDR10 → SDR), not just resolution — which is precisely what the
objective-worth evidence and the council vote exist to weigh before a
visually-important title is reclaimed.

## Consequences

- Resolute becomes a **bidirectional quality brain** — request-time
  up-selection and retention down-selection — under one policy vocabulary and
  one audit trail. The objective score gains a second consumer (Costanza's
  evidence).
- Resolute's Sonarr write surface grows by exactly **"trigger a search"**; the
  "never deletes files" discipline is **retained** (Sonarr owns the delete via
  its upgrade flow). Smaller and safer than the delete-first design this ADR
  originally carried.
- Not a hand-rolled destructive verb: Resolute drives Sonarr's ordinary,
  import-then-delete upgrade path. It still ships report-only and climbs the
  trust ladder, because it *causes* deletions downstream — but the no-file
  window and orphaned-delete risks are gone.
- New load-bearing dependency: a **1080p-target profile that excludes 2160p
  from its quality list** (so the resident is out-of-profile). A checkable
  config property — belongs in version control with the recyclarr-managed
  profiles; the executor verifies it before acting. Confirmed *not*
  CF-margin-dependent, so it holds for any release regardless of group or
  PROPER status.
- New inbound coupling: Costanza calls the worth endpoint (soft) and hands off
  downgrade decisions (hard, only once admin-confirm+ is enabled).

## Alternatives rejected / demoted

- **Profile change only, no search.** Reclaims nothing on its own — a search
  (auto or triggered) is what runs the upgrade-replace. The executor triggers
  it.
- **Delete-first, then re-grab.** The original design here; demoted to a
  **fallback** only for profiles that *include* 2160p in their quality list
  (resident not out-of-profile, so a plain 1080p won't replace it). It
  reintroduces the no-file window and the never-delete-without-replacement
  precondition, so it is not the baseline — the recommended config simply
  excludes 2160p from the target profile.
- **Manual-import override.** Not needed — the spike auto-imported. Retained
  only as a manual recovery path if a specific grab parks in the queue.
- **Execute from Costanza.** ADR-0011 / ADR-0003: Costanza never touches
  quality profiles; the Sonarr write is Resolute's.
- **Return `household_score` as evidence.** ADR-0011: double-counts the
  household's live voice and imports storage-pressure circularity. Objective
  lane only.
