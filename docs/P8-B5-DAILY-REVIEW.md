# P8-B5: Cloud Daily Review — concurrent tabs and the daily new-card quota

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

## Daily new-card quota (owner decision, Codex review remediation)

The first candidate (`7331fda`) treated `new_limit` as a per-request quota and
took the time zone from each queue request. The Codex review found four
Mediums (M1–M4). The owner decided that Cloud Daily Review has an
**account-level hard quota of 10 new cards per local day**:

- EN, DE, words and chunks share it.
- Only a successful first rating (`review_count` 0 → 1) consumes it.
- Due reviews of reviewed cards never consume it.
- Filters, request parameters, time zone, tabs, devices and deleting cards
  cannot reset it.
- Personal Desktop is unchanged.

**Migration 0012 (`0012_review_new_card_quota`)** was rewritten in place,
because #85 is unmerged. It replaces the `first_reviewed_at` column of
`7331fda` with two owner-scoped tables, both with forced RLS and an owner
policy:

- **`cloud_review_settings`**
  - One row per owner, created on first use.
  - `time_zone` defaults to `'UTC'`, with `configured`.
  - `daily_new_limit` defaults to 10 (CHECK 0–100) and is not writable
    through any API.
  - `pending_time_zone` and `pending_effective_at` are set together or not
    at all.
- **`cloud_review_new_card_ledger`**
  - The primary key is `(owner_user_id, study_item_id)`, so a card is charged
    at most once.
  - It records `quota_day` (the local date), `time_zone` and `consumed_at`.
  - It has **no FK to the card**, so delete, archive and restore never refund.
- **Rollback.** The down migration refuses while either table holds rows.
  It never silently deletes them. Run it as the table owner with BYPASSRLS,
  or as a superuser.
- **Unchanged.** No SRS, ReviewState or card column changes. Cards reviewed
  before 0012 have no ledger row, are never charged and keep rating.

**M3: the hard limit is enforced in the rating transaction.** `rate_item`:

1. Locks the card (`FOR UPDATE`) and checks `expected_review_count` (409 on
   stale), as before.
2. On a first rating, locks the owner's settings row (`FOR UPDATE`). This
   serializes every first rating for the account across tabs, devices,
   languages and kinds.
3. Counts the ledger rows for today's `quota_day`. With none left it raises
   `DailyNewCardQuotaExceededError`; otherwise it inserts the ledger row.
4. Updates the card.

All of this is one transaction, so the check, the charge and the rating
commit or roll back together.

- **Lock order.** Always card first, then settings. `set_time_zone` takes
  only the settings row, and no path takes them in reverse.
- **API.** The rate API returns **429**, not 409, with
  `{"detail": {"code": "daily_new_card_quota_exhausted", "message": …}}`.
  Cloud `apiFetch` turns any 409 into a stale-card conflict, so a different
  status keeps the two apart. A direct API call goes through the same
  transaction, so it cannot bypass the quota.

**M1: `new_limit` only narrows.** The queue offers
`min(new_limit, remaining today)` new cards. With no `time_zone` the quota
still applies, because the zone is stored, not sent. Personal (SQLite) has no
allowance and keeps its per-session `new_limit`.

**M2: the time zone is stored, not per request.**

- **API.**
  - `GET /api/review/settings` returns the zone, limit, `new_cards_today`,
    `new_cards_remaining`, `quota_day` and any pending zone.
  - `PUT /api/review/settings {user_id, time_zone}` takes an IANA name only
    (`zoneinfo`; offsets, abbreviations and paths are a 422).
    `daily_new_limit` is rejected (422, `extra=forbid`).
  - Both use the session identity; a forged `user_id` gets a 403. In
    Personal they return 404.
- **Effective boundary.**
  - If no new card has been charged today, or the zone is unchanged, a change
    applies at once.
  - Otherwise it is stored as pending until the **next local midnight of the
    current zone** and applied when the settings row is next locked or read.
- **Quota day.** The quota counts by `quota_day`, the local date in the
  effective zone. Switching zones therefore cannot reopen a calendar day that
  is already spent. For example: 10 used in Tokyo, then a switch to New York
  that takes effect at Tokyo midnight; the New York date is still 2026-11-04,
  so it stays spent until New York midnight.
- **Local day bounds.** `local_day_bounds` resolves each local midnight
  separately, so a day is 23 or 25 hours across a DST change. Non-whole-hour
  zones such as Kolkata (+5:30) work too, and Santiago's skipped midnight
  starts the day at the transition.
- **Storage.** All timestamps stay UTC.
- **Frontend.**
  - On cloud mount `DailyReviewPage` loads the settings. If the account was
    never configured, it suggests the browser zone once (`PUT`). After that,
    the browser zone is never sent again, and queue requests carry no zone.
  - The start screen shows "New cards left today N of 10 (zone)".
  - **On the quota 429**, matched by the structured code, not just the
    status, the page:
    - shows "Today's new-card limit is used up. Due reviews are still
      available";
    - drops the remaining new cards from the session and keeps the due ones;
    - does not reload the queue as it would on a 409.
  - Any other 429 is an ordinary "rating not saved".

## Tests

- **`test_cloud_daily_review.py`** (31 tests, owner checklist 1–10):
  1. The quota applies with no `time_zone`.
  2. EN/DE words and chunks share 10.
  3. `new_limit=100` cannot raise it, and the PUT refuses `daily_new_limit`.
  4. 10 concurrent API ratings with 6 already used give exactly 4×200 and
     6×429.
  5. A direct rate call over the limit gets 429 with the code.
  6. Delete, archive and restore never refund, and a re-added card gets 429.
  7. Time zone behaviour:
     - default UTC, and the first choice applies at once;
     - Tokyo → New York is pending until Tokyo midnight, after which the New
       York date stays spent, and the next New York day resets;
     - a 25-hour Berlin DST day;
     - local-day bounds for New York, Berlin, Santiago and Kolkata;
     - IANA-only names.
  8. A retried rating gets 409 with one ledger row; a failure after the
     charge (forced `schedule_review` error) rolls the ledger back; of two
     tabs, one wins.
  9. Due cards still rate after the quota is spent.
  10. Compatibility and isolation:
      - owner isolation, and a forged `user_id` → 403;
      - pre-0012 reviewed cards are never charged;
      - Personal keeps its per-session limit, and settings return 404 there;
      - archived cards cannot be rated;
      - the 0012 rollback refuses while data exists.
- **`CloudDailyReview.test.tsx`** (8 tests):
  - the browser zone is suggested once and the queue has no zone;
  - a configured zone is left alone;
  - the quota 429 message, distinct from the 409 reload;
  - new cards are skipped while due cards stay;
  - a 429 without the code is an ordinary failure;
  - the 409 reload, an in-flight rating across an account switch, and
    sign-out.
- **`DailyReviewPage.test.tsx`:** Personal sends no `time_zone`.
- **Mutation check:** 15 mutants, all killed. Backend (10):
  - quota check, first-rating gate and ledger insert each removed;
  - the settings lock removed;
  - the pending boundary ignored;
  - a zone switch always immediate;
  - the quota day counted in UTC;
  - deletion refunding;
  - `new_limit` able to raise;
  - the quota error mapped to 409.

  Frontend (5): the quota error not recognised, any 429 treated as the
  quota, no settings bootstrap, a configured zone overwritten, new cards kept
  after the quota.

## Risks

- **Summary counts.** "New" counts every unreviewed card, not today's
  remaining quota; the start screen shows the remaining quota beside it.
- **No UI to change the zone after the first suggestion.** The API supports
  it. A settings screen belongs with account settings (Workstream A/D).
- **The limit is fixed at 10.** The column allows 0–100, but nothing can
  write it. Changing it is an owner decision.
- **The settings row lock serializes an account's first ratings.** This is
  intended, and the contention is per account only.
