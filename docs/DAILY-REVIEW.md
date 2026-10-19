# Daily Review

Daily Review is the long-term spaced-review workflow for explicitly curated Words & Chunks. It is separate from the seven-stage Guided Lesson: Listening and Library curation create Study Bank items, and Daily Review decides which active items are ready. Everything is deterministic and local; it uses no AI, external SRS, analytics, or network service beyond the local backend.

## Storage and migration

Study Bank storage version 2 adds a nested `review` object to every `StudyItem`:

- `due_at`
- `interval_seconds`
- `ease_factor`
- `repetition_count`
- `lapse_count`
- `review_count`
- `again_count`, `hard_count`, `good_count`, and `easy_count`
- `last_reviewed_at`
- `last_rating`

All persisted timestamps are timezone-aware UTC values. Ease is bounded from 1.3 through 3.0, rating counters must total `review_count`, and the last-review fields must agree with whether an item has ever been reviewed.

A new item is due immediately: `due_at` equals its original `created_at`, interval and counters are zero, and ease is 2.5. Saving a duplicate, merging provenance, editing learner fields, archiving, and restoring preserve the complete review state.

Version 1 remains readable without a startup or GET rewrite. Its items receive the new-card state in memory with `due_at = created_at`. The next successful Study Bank mutation writes the complete file atomically as version 2. Item identity, timestamps, content, active state, metadata, and provenance are retained. Malformed data and unsupported future versions fail without being overwritten. A new store starts at version 2.

## Scheduler

This is a conservative MVP interval scheduler. It is neither FSRS nor claimed to be exact Anki SM-2. The backend is authoritative; the frontend only formats the returned `due_at`.

Constants:

- minimum/default/maximum ease: 1.3 / 2.5 / 3.0
- one day: 86,400 seconds
- maximum interval: 365 days

Ratings use the state immediately before the rating:

- **Again:** interval 10 minutes, repetition count resets to zero, lapse count increases, and ease decreases by 0.20 to the minimum.
- **Hard:** repetition count increases and ease decreases by 0.15 to the minimum. A new card or interval below one day gets one day; otherwise the interval is the greater of one day and the previous interval multiplied by 1.20.
- **Good:** repetition count increases and ease is unchanged. A new card or interval below one day gets one day, exactly one day becomes three days, and a mature interval is multiplied by the previous ease.
- **Easy:** repetition count increases and ease rises by 0.15 to the maximum. A new card or interval below four days gets four days; otherwise the interval is multiplied by the previous ease and 1.30.

Every interval is rounded to integer seconds where multiplication is needed and capped at 365 days. The selected rating counter and total review count increase, `last_reviewed_at` and `last_rating` are recorded, and `due_at` becomes the injected UTC clock time plus the interval. Again does not force a same-session relearning card; it can appear in a newly requested queue once its ten-minute interval expires.

## Summary, queue, and rating API

- `GET /api/review/summary` returns due, new, and active counts split by words and chunks. It accepts `user_id` and an optional `language`.
- `GET /api/review/queue` accepts `user_id`, optional `language` and `kind`, plus bounded `limit` and `new_limit` values. It never writes or reserves cards.
- `POST /api/review/items/{item_id}/rate` accepts `user_id`, `rating`, and `expected_review_count`. It returns the updated item, previous/next due timestamps, and new interval.

The default session limit is 20 and the default new-card limit is 10. Previously reviewed cards due at or before the current timestamp come first, ordered by due time, creation time, and ID. Remaining capacity is filled by new cards ordered by creation time and ID. If due reviewed cards fill the session, no new cards are included.

A rating verifies the active item and expected count, runs the scheduler, and performs one atomic Study Bank write while holding the store lock. A stale or duplicate submit returns HTTP 409 and cannot apply a second rating. Archived and deleted items do not appear in summary or queue. Archive and restore retain scheduling; deleting an item deletes its nested state with it.

## Product boundaries

Study Bank is the scheduling source of truth for both words and chunks. Listening and My Library save curated candidates into Study Bank; those new items become immediately eligible for Daily Review.

Guided Active Recall remains part of the fixed Guided Lesson sequence and continues to use Chunk Memory priority and `success` / `partial` / `failure` statistics. Daily Review uses `again` / `hard` / `good` / `easy` and never modifies Chunk Memory practice counts, mastery, or Guided Lesson progress. Chunk curation continues its existing one-way synchronization into Chunk Memory for backward compatibility.

The review counters provide bounded signals for future Speaking Agent work, such as difficult, repeatedly missed, and recently reviewed items. There is deliberately no unlimited event log.

## MVP limitations

- no FSRS optimization or exact Anki claim
- no review event log or history chart
- no custom scheduler parameters or per-day new-card preference UI
- no local-midnight statistics, streaks, XP, gamification, or leaderboard
- no cloud sync or remote SRS service
- no pronunciation review
- no forced immediate same-session requeue after Again
- Guided Active Recall and Daily Review keep separate performance models
