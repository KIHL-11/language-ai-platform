# P8-B1: Cloud Library media integrity and reliable cleanup

Workstream B increment B-1, Issue #58 items 1–3, 5 and 6. Item 4 (cloud
progress `expected_revision`) moves to B-2 per `docs/PHASE8-WORKSTREAM-B-PLAN.md`.
Synthetic data, a disposable PostgreSQL and the filesystem object adapter only.
Not a production rollout; Personal Desktop is unchanged.

## Read integrity

`GET /api/library/items/{id}/audio` in cloud mode now:

1. resolves the owner-scoped media row (unchanged; cross-owner and missing are
   the same 404);
2. checks the object key against the owner hash and item id (unchanged);
3. opens the object with `O_NOFOLLOW` and refuses any key whose resolved path
   differs from its literal path, so a symlinked object or directory is never
   followed;
4. requires a regular file whose size equals the stored `byte_size` and is at
   most `MAX_AUDIO_BYTES` (100 MiB);
5. hashes the bytes in 1 MiB chunks, reading at most `byte_size + 1` bytes, and
   compares with the stored SHA-256 in constant time;
6. streams **the same open handle** it hashed. `VerifiedAudioResponse` keeps
   Starlette's Range handling but never reopens the path and disables
   `pathsend`, so swapping the file after verification cannot change what is sent.

Any mismatch returns HTTP 500 with
`{"code": "cloud_media_integrity_failed", "message": "Library audio failed an integrity check."}`
and no bytes. The log line carries the item id and an error class, never a key,
owner or path. A missing object is the usual 404.

`ETag` is the content SHA-256, so `If-Range` works across requests.

**Cost and limits:** every request, including each Range request, rehashes the
whole object. That is bounded (≤ 100 MiB read, 1 MiB of memory) but linear in
size. A verified-object cache keyed on inode and ctime is a possible later
optimisation; it is not needed at synthetic scale.

## Cleanup outbox

Migration `0010_library_media_cleanup_worker` (numbered after the account
stack's 0007–0009) adds `next_attempt_at`, `last_error_code`, a `dead` status
and a due-row index. Existing rows are backfilled with `next_attempt_at = updated_at`.
The down migration **refuses** while any row is `dead`.

Every object that may need deleting has a row:

| Writer | Row created | `next_attempt_at` |
| --- | --- | --- |
| Item delete | in the same transaction as the metadata delete | now |
| Audio replacement | old key, in the same transaction as the metadata switch | now |
| Upload | new key, committed **before** any byte is written | now + 15 min grace |

An upload adopts its key by deleting its own row inside the metadata
transaction. If the row is gone (a worker claimed it after the grace), the
upload aborts with `MediaUploadAbortedError` and nothing references the object.
A crash between writing bytes and committing leaves the row; the worker removes
the object after the grace.

The request path tries the cleanup immediately after commit. Whatever fails is
left to the worker.

### Worker protocol

1. **Claim** (own committed transaction): `UPDATE … FROM (SELECT … FOR UPDATE SKIP LOCKED LIMIT n)`
   bumps `attempt_count` and sets `next_attempt_at = now + min(30 s · 2^(attempts−1), 6 h)`.
   A crash after the claim counts as an attempt; the row returns after its backoff.
2. **Process** (second transaction): re-select the row `FOR UPDATE SKIP LOCKED`
   with the claimed `attempt_count` (a stale claim finds nothing and is skipped),
   refuse if any media row still references the key, delete the object while
   holding the row lock, then delete the row. Holding the lock means an upload
   adopting the same key blocks until the delete finishes, then finds no row
   and aborts.
3. **Failures:** object-store error → `last_error_code = object_delete_failed`,
   retried after backoff; after 10 attempts the row becomes `dead`. A key that
   does not match the row's owner hash or item → `dead / namespace_mismatch`,
   nothing deleted. A key still referenced by a media row →
   `dead / object_still_referenced`, nothing deleted.

Owner, item and key always come from the validated outbox row; no caller can
name a path. Deleting a missing object is a success, so every step is idempotent.

### Running it

```bash
CLOUD_LIBRARY_DATABASE_URL=postgresql://<cleanup_role>@<host>/<db> \
CLOUD_LIBRARY_MEDIA_ROOT=<object root> \
PYTHONPATH=backend python -m services.library.cleanup_worker --batch 50 --max-batches 20
```

Output is counts only, for example
`claimed=3 deleted=3 retry_scheduled=0 dead=0 skipped=0 backlog_pending=0 backlog_due=0 backlog_dead=0`.
Exit codes are 0 (done), 1 (database unavailable; rerun) and 3 (dead rows need
an operator). Overlapping runs are safe, so a one-minute schedule is fine.

Suggested role grants: `SELECT, UPDATE, DELETE` on `cloud_media_cleanup_outbox`
and `SELECT` on `cloud_library_media_assets`. No access to items, users or other
tables is needed.

### Dead rows

A dead row means an object was **not** deleted. Inspect it by `cleanup_id` and
`last_error_code`:

- `object_delete_failed`: fix the object store, then
  `UPDATE … SET status = 'pending', attempt_count = 0, last_error_code = NULL, next_attempt_at = now()`.
- `namespace_mismatch` / `object_still_referenced`: a write bypassed the
  repository. Investigate before changing anything; do not reset blindly.

## Tests

`backend/tests/test_library_media_reliability.py` (35 tests) covers:

- tampering, truncation, extension and cross-owner swaps of synthetic audio;
- symlinked objects and directories;
- bounded chunked reads and oversized metadata;
- Range serving from the verified handle, plus a swap after verification;
- backoff, dead-lettering and the CLI;
- two concurrent workers deleting each object exactly once;
- crashes after claim and after deletion;
- the row lock during delete and stale claims;
- replacement success and failure, a crash before commit, an expired upload,
  an upload racing an item delete (deterministic, paused transaction) and four
  concurrent replacements;
- migration backfill and refused rollback.

Mutation check: each guard (hash compare, size check, handle reuse, reference
check, adoption check, namespace dead-lettering, item lock, due filter,
dead-lettering after max attempts, grace, transactional replacement enqueue,
backoff growth, process row lock and claim match) was removed in turn. Every one
fails the suite except two equivalents that SHA-256 already covers (the
`remaining` length check and the `fstat` size check).

## Not covered

- Production object storage, signed URLs, encryption/KMS, AV scanning and the
  upload endpoint (no cloud upload route exists yet).
- Quarantining corrupt objects; they are refused, not moved.
- A metrics exporter. Counts come from the CLI output and `cleanup_backlog()`.
- Cloud progress optimistic concurrency (Issue #58 item 4), which is B-2.
