# Calibration & Feedback Workflow

resolute learns through an explicit, reviewable loop — not silent weight
drift. The policy file is the model; feedback is the training signal; you are
the optimizer.

## 1. Record feedback

Every decision carries `feedback_options`. Record the household's actual
preference whenever it differs (or agrees, early on):

```bash
resolute feedback last agree
resolute feedback last prefer_1080p --reason-tag background_watch
resolute feedback 01KWME0M... prefer_2160p --reason-tag showcase \
  --comment "looked stunning in the trailer"
```

or `POST /api/feedback` from any client. Reason tags are validated against
`policy.feedback_reason_tags` so clusters stay analyzable.

## 2. Review the signal

```bash
resolute calibrate          # agreement rate, decision mix, override clusters
resolute review-overrides   # every disagreement, newest first
```

## 3. Apply the learning to policy.yaml

Overrides cluster by reason tag, and each cluster maps to a specific knob:

| Cluster | Knob |
| --- | --- |
| `showcase` overrides to 2160p | add genre/keyword to `visual_genres`, raise `weights.visual_genre`, or lower `thresholds.uhd_score` |
| `background_watch` overrides to 1080p | add genre to `low_payoff_genres` |
| `storage` overrides | raise `storage_pressure` or lower `max_episodes_2160p` |
| `rewatch_favorite` / franchise overrides | add to `franchises_2160p` |
| requester-specific pattern | adjust `requesters.<name>.bias_2160p` |
| `prestige_exception` (judge got it right/wrong) | tune the judge prompt (bump `PROMPT_VERSION`) |
| `bad_metadata` | fix the evidence source, not the policy |

Commit the policy change to git — the diff is the calibration record.

## 4. Re-verify against golden cases

```bash
resolute fixtures-test
```

Golden cases (`fixtures/golden/expectations.json`) encode decisions the
household considers settled. Add a case whenever an override was important
enough that regressing it would hurt; the suite (and CI) fails if a policy
tweak breaks a settled decision.

## 5. Judge calibration

Model-backed decisions store provider, model, prompt version, evidence hash,
raw output, and latency. When overrides implicate the judge:

- inspect stored verdicts for the overridden decisions
  (`GET /api/decisions/{id}` -> `verdict`, `model_involvement`);
- adjust `judge/prompts.py`, bump `PROMPT_VERSION`;
- replay ambiguous fixtures with
  `resolute decide "The Bear" --tmdb-id 136315 --fixtures fixtures/evidence --force-judge`.

## Cadence

Weekly during shadow phase, monthly after writes are enabled. The rollout
gates in docs/rollout.md consume the `calibrate` agreement rate directly.
