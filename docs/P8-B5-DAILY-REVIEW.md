# P8-B5: Cloud Daily Review — concurrent tabs and learner-local days

Stacked on B-4 (Draft PR #84, `91ee966`). Synthetic owners and a disposable
PostgreSQL only. Personal Desktop is unchanged.

## Concurrency (existing mechanism, proven; one client fix)

`rate_item` locks the card (`SELECT … FOR UPDATE`) and requires
`expected_review_count` to match before scheduling.

- **Two tabs** rating one card: exactly one succeeds and the other gets 409.
- **A retried rating** (lost response) also gets 409, so the count never
  rises twice.
- **Client fix.** In cloud, `apiFetch` raises `CloudConflictError` on a 409
  before `DailyReviewPage` sees a `ReviewApiError`. As a result, the losing
  tab showed "rating not saved" and kept the stale card. It now treats
  `CloudConflictError` as a card conflict: it reloads the queue and explains
  why. An identity-change 409 is not a card conflict, because the workspace
  remounts.
- **Sign-out or account switch while a rating is in flight:** the response is
  dropped (the generation check), and nothing is shown to the next account.

## Learner-local days

- **Migration 0012** adds `cloud_study_items.first_reviewed_at`.
  - The first rating sets it (`COALESCE`, so later ratings never move it).
  - A CHECK requires `review_count >= 1` whenever it is set.
  - An index covers counting.
  - Cards reviewed before 0012 keep NULL and never count.
  - The down migration refuses while first reviews exist.
  - No SRS, ReviewState, `due_at` or history column changes.
- **Time zone.** `GET /api/review/queue?time_zone=<IANA>` is validated with
  `zoneinfo`. Offsets, abbreviations and paths are a 422. In cloud,
  `new_limit` becomes a quota per learner-local calendar day, across
  sessions and tabs.
  - **Local day bounds.** `local_day_bounds` resolves each local midnight
    separately, so a day is 23 or 25 hours across a DST change. A midnight
    that a DST change skips (America/Santiago) starts the day at the
    transition.
  - **Storage.** All timestamps stay UTC.
- **Frontend.** Cloud sends `Intl…resolvedOptions().timeZone`. Personal sends
  nothing, and its store has no first-review count, so it keeps the
  per-session limit.

## Tests

- **`test_cloud_daily_review.py`** (28 tests):
  - two tabs, a retry and cross-owner access;
  - the first review recorded only once;
  - local day bounds: New York 23 h and 25 h, Berlin both changes, Santiago's
    skipped midnight, Kolkata +5:30, UTC;
  - IANA-only validation;
  - a first review on the DST night attributed to the right local day;
  - the quota across sessions on a 25-hour day, resetting the next local day;
  - Tokyo local day versus UTC day;
  - cards reviewed before 0012 never counting;
  - the queue never changing SRS;
  - the API applying and validating the zone;
  - Personal per-session behaviour;
  - archived cards;
  - 0012 rollback and the CHECK constraint.
- **`CloudDailyReview.test.tsx`** (4 tests): the time zone sent, a 409 reload,
  an in-flight rating across an account switch, and sign-out.
- **`DailyReviewPage.test.tsx`:** Personal sends no `time_zone`.
- **Mutation check:** 12 mutants. All are killed except one equivalent pair:
  the first-review guard is enforced twice (by the value passed and by
  `COALESCE`). Removing both at once is killed.

## Risks

- **Quota under concurrency.** The quota is read when a queue is built. Two
  tabs that build queues at the same moment can together introduce up to
  2 × `new_limit` new cards that day. Ratings themselves stay atomic. A hard
  cap would need a per-day reservation.
- **Time zone source.** It comes from the browser and is not stored. A
  learner who travels gets the local day of the browser they use. A stored
  preference belongs with account settings (Workstream A/D).
- **Summary counts.** These are unchanged: due is still a UTC instant, which
  is correct; new counts every unreviewed card.
