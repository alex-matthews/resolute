# Household preferences (ADR-0003)

This file is the household's taste, written as ordinary prose. The model reads
it verbatim when deciding between the trusted quality profiles. Edit it when
you disagree with recurring decisions — that IS the calibration mechanism.

Copy to `household.md` (or wherever RESOLUTE_HOUSEHOLD_POLICY_PATH points) and
mount it from a Secret, not a git ConfigMap: it will usually name household
members and their viewing habits.

---

Nature and space documentaries are the reason the 4K TV exists — store them at
the best quality available.

Kids' cartoons and background sitcoms are 1080p; nobody watches them on the
good screen.

Star Wars anything gets top quality. Long-running reality shows never do.

When free space is tight, be conservative about big 2160p commitments —
a prestige drama can wait at 1080p and be upgraded later.
