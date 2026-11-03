# P8-B2: Cloud Guided and Listening with optimistic concurrency

Workstream B increment B-2. Covers Issue #58 item 4 and puts the Cloud Library
learning flow into the cloud workspace. Stacked on B-1 (Draft PR #81). Uses
synthetic data, a disposable PostgreSQL and mock AI only. Personal Desktop
behaviour and data are unchanged.

## Server contract

`PATCH /api/library/items/{id}/progress` and `…/listening-progress`:

| Mode | `expected_revision` | Result |
| --- | --- | --- |
| Cloud | missing | 409 `library_revision_required` (same for any id, so nothing is enumerated) |
| Cloud | other owner's or unknown item | 404, whatever revision is sent |
| Cloud | stale | 409 `library_revision_conflict`; nothing changes |
| Cloud | current, invalid progress | 422; nothing changes |
| Cloud | current, valid | 200 with the new `revision` |
| Personal | absent or any value | ignored; the store, file and response are unchanged |

Cloud item responses (`POST`, `GET`, both `PATCH`es) are `CloudLibraryItem`,
which is `LibraryItem` plus `revision`. It is a subclass, so the Personal
Desktop JSON file never gains a field. Guided and Listening share one item
revision, and re-saving a lesson also bumps it, so any open tab must reload.

**Atomicity.** One transaction does the following:
1. `SELECT … FOR UPDATE OF item`; a missing or cross-owner item is a 404.
2. Compare the revision; a mismatch is a 409.
3. Validate the progress against the lesson; invalid progress is a 422.
4. `UPDATE … WHERE revision = expected RETURNING revision`.

A second writer holding the same revision waits for the row lock, then sees the
new revision and fails. The UPDATE condition repeats the check as a second
layer. Progress writes touch only `cloud_library_items`: Study Bank, SRS and
Daily Review state keep their own tables and semantics.

## Client

- **`cloudProgressSync`** keeps one writer per opened item. It serializes and
  coalesces writes, so only the latest snapshot of each kind is sent, and every
  write carries the revision it was based on. On a 409 it reloads the item:
  - **Guided:** the further position wins as a whole, ranked by
    (completed stages, stage, sentence). `lesson_memory_imported` is never
    unset, and a replaced lesson (different training order) wins outright.
    When the saved state is further ahead, the view remounts on it and
    explains why. Snapshots queued before such a merge are discarded, using an
    epoch counter, so a stale tab can never move saved progress back.
  - **Listening:** position and display settings follow the tab in use, so the
    snapshot is re-sent on the reloaded revision.
  - **Retries:** at most 3 conflicts in a row, then the view says so. Network
    errors, expiry and identity changes are never retried.
- **Session binding.** Every Library request in the cloud views carries the
  session generation it was opened under. `CloudAuthClient.request` refuses
  before sending if the sign-in has changed, so a write queued under one
  account can never be sent under the next. Server 409s are now
  `CloudConflictError`, which keeps them separate from local identity changes.
- **Window focus.** Re-checking the session on focus or rotation for the *same*
  account no longer publishes `loading` or bumps the generation. It used to
  unmount the whole cloud workspace on every focus, which made training
  unusable. A different account, a 401 or a failure still clears everything as
  before.
- **Cloud Library UI** (`src/cloud/CloudLibrary.tsx`) goes list → lesson →
  Guided or Listening. Every view reloads the item from the server and keeps it
  in memory only, never `localStorage`.
  - **Guided** reuses `LessonViewer`/`TrainingSession` with realtime voice
    forced off (`voiceEnabled={false}`, whatever `VITE_LIVE_DIALOGUE_PROVIDER`
    says) and a memory client that makes no network call, so Active Recall
    reports "unavailable" and the learner continues.
  - **Listening** reuses `ListeningWorkspace` with a revision-checked writer.
    Dictionary and Words & Chunks are off (`studyFeatures={false}`) until the
    cloud dictionary and Study Bank provenance land (B-3/B-4). Shadowing is
    not offered.

## Tests

- **Backend:** `test_cloud_progress_revision.py` has 14 tests. They cover:
  - the single-learner flow;
  - a missing revision, which gives a 409 without enumeration;
  - stale revisions for both kinds;
  - the shared revision;
  - 8 concurrent writers with exactly one winner;
  - a deterministic lock wait;
  - safe retry and replay;
  - cross-owner 404s;
  - invalid progress;
  - re-saving the lesson;
  - writes staying within the Library table;
  - the Personal contract, including a check that the file has no
    `revision`.
- **Frontend:** 32 new tests across these files:
  - **`cloudProgressSync.test.ts`:**
    - merging;
    - coalescing;
    - bounded retries;
    - no retry on non-conflict errors;
    - the queue-drain race;
    - dropping stale snapshots after a merge.
  - **`client.test.ts`:**
    - silent same-user re-check;
    - generation-bound refusal before sending;
    - conflict classification.
  - **`CloudLibrary.test.tsx`**, which runs against a stateful mock of the
    server contract:
    - Guided with CSRF and revisions, and no Personal or voice routes;
    - adopting another tab's further progress;
    - Listening without study routes;
    - no writes after an account switch or sign-out;
    - an open session surviving a focus re-check;
    - an unavailable lesson;
    - no voice even when the build enables it.
  - **`CloudGuided.test.tsx`:** a replaced view's late callback is ignored.
  - **Personal regression:**
    - a plain Listening PATCH with study features on;
    - realtime voice still offered in Personal.
- **Mutation check:** 17 guards were removed one at a time. Each fails the
  suite except one equivalent: the UPDATE's `revision =` condition, which the
  row lock plus the pre-check make unreachable.
- **Flakiness:** the concurrency test ran 30 times without failure. Its first
  version picked positions outside the 6 s lesson, so it failed only when one
  of those writers won; that was a test bug.

## Not covered

- The cloud dictionary, saved-language recall and Study Bank saves from
  Listening (B-3/B-4).
- **Pre-existing gap, not changed here:** in cloud mode
  `/api/chunks/memory/active-recall` reads the Personal local stores keyed by
  the authenticated id. The cloud UI no longer calls it. Moving it behind an
  owner-scoped store is Workstream B/F work.
- Cloud lesson creation and import (Workstream D), Shadowing in cloud, and
  realtime voice (Workstream C).
- A browser E2E; jsdom tests drive the real components against a mocked
  server.
