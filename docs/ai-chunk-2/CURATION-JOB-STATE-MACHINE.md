# CurationJob state machine (design only; NOT IMPLEMENTED)

No worker, table or route exists. Every test in §8 is **NOT EXECUTED**. This
design reuses mechanisms already proven in the repository:

- the generation reservation lease with `lease_token` in
  `ai_chunks/postgres.py`;
- the leased cleanup-outbox worker from B-1;
- the decision idempotency log.

## 1. Identity and de-duplication

`job_key = sha256(owner, library_item_id, transcript_revision, curation_config_hash, provider_id, provider_version)`

- `curation_config_hash` covers:
  - the window budget, overlap and token ratios;
  - the weights, floor, gates and K;
  - the prompt template version.
- There is a **unique** `(owner, job_key)`. Creating a job runs `INSERT … ON
  CONFLICT DO NOTHING`, then returns the existing row:
  - if it is `ready`, the cached batch, with **no AI call**;
  - if it is `partial` or `failed`, that job, which the learner may retry;
  - if it is `pending` or `processing`, that job.
- A new transcript revision or config means a new job. The old job's batch
  stays readable, and its evidence re-validates as `sentence_changed` or
  `revalidated` (see the ranking spec §4).

## 2. States

```
create ─► pending ─lease─► processing ─┬─► ready     (coverage complete, reduce done)
             ▲                │        ├─► partial   (some windows settled ≠ done)
             │ lease expired  │        └─► failed    (no window done, or refused before start)
             └────────────────┘
pending/processing ─cancel─► cancelled
partial/failed ─retry─► pending   (attempt+1; only windows not `done` run)
```

- **Window rows:** `pending → done | failed(reason) | skipped(reason)`.
  - A `done` window is immutable and is never re-run or re-billed.
  - The job finishes when every window is settled. Status comes from the
    coverage manifest, so it can never be "complete with gaps".
- **Terminal states:** `ready` and `cancelled`. `partial` and `failed` are
  terminal until the learner retries.
- **Budget exhausted:** the remaining windows become `skipped(budget)` and
  the job is `partial`. It is **never** `ready`, as enforced by the coverage
  rule in the ranking spec §3.

## 3. Lease and fencing

Each job row has `lease_owner` (worker id), `lease_token` (a fresh UUID per
claim), `lease_expires_at`, `fence BIGINT` (incremented on every claim) and
`attempt`.

- **Claim.** In one transaction:

  ```sql
  UPDATE jobs SET fence = fence + 1, lease_token = :t,
         lease_expires_at = now() + :ttl, attempt = attempt + 1
  WHERE … AND state IN ('pending')
     OR (state = 'processing' AND lease_expires_at < now())
  RETURNING fence
  ```

  The worker keeps `(token, fence)`.
- **Every write** (window result, heartbeat, finish) is a compare-and-swap:
  `… WHERE job_id = :id AND lease_token = :t AND fence = :f AND state =
  'processing'`.
  - Zero rows updated means the worker lost the lease. It must stop and
    must not retry its write.
- **Window results** use `INSERT … ON CONFLICT (job_id, window_index) DO
  NOTHING`, plus the fence check, so a duplicate submission is a no-op.
- **Finishing** uses the same compare-and-swap, which moves the job to
  ready, partial or failed and writes the batch in **one transaction**. A
  stale worker therefore cannot overwrite a newer result.

## 4. Timeouts, retries, cancellation and cost

- **Provider calls.** 60 s per call, at most 2 retries with back-off, and
  only for timeouts, 429 and 5xx. Invalid output is not retried; the window
  becomes `failed(provider_invalid_output)`.
- **Lease.** The TTL is 2 × (call timeout × (retries + 1)), with heartbeat
  extension. Crash recovery is lease expiry: the next claimer resumes at the
  first window that is not `done`.
- **Cancellation.** It sets `cancel_requested`. The worker checks it before
  each window and finishes as `cancelled`. Windows already done remain, but
  no batch is published.
- **Budget.**
  - It is reserved **before** each call: per-job token and window caps, a
    per-owner daily cap, and a global concurrency of 2.
  - The reservation is recorded on the window row, so a retry after a crash
    does not double-count an attempt that never reached the provider. An
    attempt that did reach the provider is counted even if its result was
    lost, which is conservative.
- **Paid calls** need `AI_CHUNK_PROVIDER=llm` **and**
  `AI_CHUNK_PAID_CALLS_AUTHORIZED=true`. Neither is set in tests, CI or the
  defaults.

## 5. No duplicate recommendations or Study Bank writes

- **One batch per job.** A unique `(owner, curation_job_id)` on batches, and
  the batch is created only by the fenced finish.
- **Study Bank writes happen only through the existing decision path** after
  a learner action, with the decision idempotency key, the version check and
  `save_item_in_transaction` de-duplication. Jobs never write Study Bank
  items.

## 6. Owner isolation

- **Cloud.**
  - Jobs, windows and curated batches are owner-scoped with forced RLS, the
    same as 0003.
  - Every query sets `app.current_owner`.
  - A job id from another owner returns 404, identical to a missing one.
  - Workers act **as the job's owner**, setting the owner context from the
    job row they claimed, so RLS also binds the worker.
- **Account switch** in the browser. The existing session generation checks
  in `CloudAuthClient` drop responses that belong to the previous account.
- **Personal.** A single local user; jobs live in the Personal store (see
  CH-01-02-DESIGN-REVISION.md §6).

## 7. What this prevents

| Threat | Mechanism |
|---|---|
| Two workers committing at once | One claim succeeds (the conditional UPDATE); the other's compare-and-swap writes match 0 rows |
| An expired worker overwriting a newer result | The fence or token changed when the job was reclaimed, so its compare-and-swap fails |
| A retry duplicating recommendations or billing | Done windows are immutable; `ON CONFLICT DO NOTHING`; one batch per job; budget reserved per window attempt |
| Budget exhaustion labelled complete | The coverage rule: any `skipped(budget)` window means `partial` |
| Cross-account read or write | RLS, the owner context, and 404 non-enumeration |

## 8. Executable test specification — **NOT EXECUTED**

Each would run on the disposable PostgreSQL 17 with a stub provider. Each
needs the job tables and worker to exist first.

| # | Test | Expected |
|---|---|---|
| J1 | Create the same job twice, including concurrently from 5 threads | One row and one job id |
| J2 | A ready job is created again | The same batch; stub provider call count stays 0 |
| J3 | Two workers claim one pending job | Exactly one claim succeeds |
| J4 | Worker A's lease expires, B reclaims, then A submits a window and a finish | A's writes update 0 rows; B's result is the one stored |
| J5 | A crash after 3 of 6 windows | The reclaim runs only windows 4–6; the provider sees no repeat for 1–3 |
| J6 | The stub times out on window 2 twice, then succeeds | 3 attempts recorded; the window is done; budget counted 3 times |
| J7 | Invalid JSON on window 4 | No retry; `failed(provider_invalid_output)`; the job is partial |
| J8 | The budget allows 4 of 6 windows | Windows 5–6 are `skipped(budget)`; the job is partial; the manifest has ranges |
| J9 | Cancel during window 3 | `cancelled`; no batch published |
| J10 | Retry a partial job | Only unsettled windows run; a new batch replaces the old one by fenced finish; no Study Bank write |
| J11 | Owner B GETs or retries owner A's job | 404 identical to missing; no state change |
| J12 | A worker without an owner context | RLS returns no rows; the worker fails closed |
| J13 | Accept a curated candidate after the Library sentence changed | 409 `candidate_source_changed`; no Study Bank change |
| J14 | Accept a curated candidate that duplicates an archived item with a note and SRS | Item unchanged except appended provenance (reusing the existing durable test pattern) |
