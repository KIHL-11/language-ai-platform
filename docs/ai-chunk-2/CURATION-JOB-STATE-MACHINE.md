# CurationJob state machine (design only; NOT IMPLEMENTED)

**Revision:** Round 3 plus Codex F3 attempt-identity/accounting clarification.

The worker remains NOT IMPLEMENTED. The pure budget projection in
`backend/chunk_curation_lab/billing.py` and its synthetic tests validate
accounting arithmetic only; they do not execute §9/§10 job/fault scenarios.

No worker, table, migration or route exists, and every test in §9 and §10 is
**NOT EXECUTED**. The design reuses mechanisms already in the repository:

- the generation reservation lease with `lease_token` in
  `ai_chunks/postgres.py`;
- the B-1 leased cleanup worker;
- the decision idempotency log.

**Retracted from the first revision.** The earlier claims "done windows are
never re-billed" and "no duplicate billing" are **withdrawn**. Database
fencing gives exactly-once *recording*, not exactly-once *provider
execution*. See §6.

## 1. Objects

Five kinds of object, each with its own lifecycle:

| Object | Key | Mutability |
|---|---|---|
| **Job** | `(owner, job_id)`; unique `(owner, job_key)` | Mutable state with a fence; holds `current_batch_id` |
| **Window** | `(job_id, window_index)` | Status row: `pending → succeeded \| failed \| skipped` and back to `pending` on retry; `succeeded` is final |
| **Window attempt** | PK `(owner, attempt_id)`; unique `(job_id, window_index, attempt_no)` | One canonical identity per physical call, with append-only state/usage events (§6) |
| **Window checkpoint** | `(job_id, window_index)` | Written **once**, only from a succeeded attempt; immutable |
| **Published batch** | `(owner, batch_id)`; unique `(owner, job_id, batch_revision)` | Immutable once published; old revisions stay readable |

```
job_key = sha256(owner, library_item_id, transcript_revision,
                 curation_config_hash, provider_id, provider_version)
```

`curation_config_hash` covers:

- the window budget, overlap and token ratios;
- the weights, floor, gates and K;
- the prompt template version.

## 2. Job states

```
create ─► pending ─claim─► processing ─┬─► ready
             ▲                 │       ├─► partial ──retry──► pending
             │ lease expired   │       ├─► failed  ──retry──► pending
             └─────────────────┘       └─► needs_budget_approval ──approve──► pending
pending | processing ─cancel─► cancelled
```

- **`ready`**: every window succeeded and a batch was published.
- **`partial`**: at least one window succeeded and at least one did not.
  The batch is published and labelled partial. It is never `ready`, because
  the coverage rule in the ranking spec §3 forbids it.
- **`failed`**: no window succeeded. No batch is published.
- **`needs_budget_approval`**: the next attempt would exceed the approved
  budget, or uncertain spend crossed the threshold (§6.4). It waits for the
  learner. A retry never happens silently.
- **`cancelled`**: terminal. Checkpoints and earlier batches are kept.

Creating a job is `INSERT … ON CONFLICT (owner, job_key) DO NOTHING`, then
read the row:

- `ready` returns its `current_batch_id` with **no** provider call;
- any other state returns the job.

## 3. Windows, attempts and checkpoints (M5)

These rules resolve the conflict between `ON CONFLICT DO NOTHING` and
failed-window recovery:

- **The checkpoint row exists only for success.**
  `INSERT INTO window_checkpoints … ON CONFLICT (job_id, window_index) DO
  NOTHING` is used **only** when an attempt succeeded. A failed or skipped
  window has **no** checkpoint, so a retry can never be blocked by a stale
  failure row. A duplicate success (from a stale or racing worker) is a
  no-op, guarded by the fence (§4).
- **The window status** is a separate row, changed only by
  compare-and-swap:
  - `pending → succeeded`, together with the checkpoint insert, in one
    transaction;
  - `pending → failed(reason)` or `skipped(reason)`;
  - on retry, `failed | skipped → pending`;
  - `succeeded` can never leave `succeeded`.
- **Attempts** are append-only, with a new `attempt_no` each time. They
  carry the call ledger (§6) and are never updated after they reach a
  terminal attempt state.

## 4. Lease and fencing

The job row has `fence BIGINT`, `lease_token`, `lease_owner` and
`lease_expires_at`.

1. **Claim.** One `UPDATE … SET fence = fence + 1, lease_token = :new … WHERE
   state = 'pending' OR (state = 'processing' AND lease_expires_at < now())
   RETURNING fence`. The worker keeps `(token, fence)`.
2. **Every worker write** (window status, checkpoint, attempt ledger, batch
   publish, heartbeat) is in a transaction that first checks `SELECT … FROM
   jobs WHERE job_id = :id AND fence = :f AND lease_token = :t AND state =
   'processing' FOR UPDATE`.
   - If that returns no row, the worker has lost the lease. It stops, writes
     nothing more, and does **not** retry the write.
3. **Stale results are recorded, not published.** A stale worker can never
   publish a batch or move a window. The provider response it holds is
   **not** applied. Its attempt is still recorded through a fence-free,
   append-only `orphan_attempts` event keyed to its original `attempt_id`; it annotates
  the canonical charge instead of adding a second one (§6.3).

## 5. Batch revisions and decisions (M5)

- **Publishing.** In one fenced transaction the worker:
  1. builds the reduce over **all current checkpoints**;
  2. inserts a new batch with `batch_revision = previous + 1`;
  3. sets `jobs.current_batch_id`;
  4. marks the previous current batch `superseded`. This is a status change
     only; the rows stay.
- **Uniqueness.** `UNIQUE (owner, job_id, batch_revision)` replaces the
  first revision's `UNIQUE (owner, curation_job_id)`. No batch is ever
  deleted to satisfy a constraint.
- **Old batches stay readable.** This covers their candidates, every
  Accept/Edit/Reject decision and replay, and `accepted_study_item_id`.
  - New decisions on a `superseded` batch are refused with 409
    `batch_superseded`, whose body names the current batch.
  - Decisions already made are never changed.
- **Inheritance into the new revision**, by `normalized_identity`. It is
  informational only, and an old decision is never changed:

  | Prior decision on the same identity (any earlier revision of this job) | New revision |
  |---|---|
  | accepted or edited_and_accepted | Candidate shown with `existing_study_item_id`/`active` (the existing "Already in Study Bank" hint) and `inherited_decision: {batch_id, candidate_id, state}`. Accepting again goes through the normal de-duplication: Study Bank fields, notes and SRS unchanged; provenance appended |
  | rejected | Not recommended again. Listed under `others` with status `previously_rejected` and `inherited_decision`. The learner can still accept it explicitly from that list |
  | edit (saved, undecided) | New candidate starts `proposed` from the new evidence. The saved edit stays on the old candidate and is offered as "restore your saved edit", never applied automatically |
  | none | Normal |

- **Traceability: partial → retry → ready.** Each batch revision stores:
  - the job fence at publish time;
  - the set of checkpoints it was built from (window indices with checkpoint
    ids);
  - its coverage manifest.

  So "revision 1 (partial, windows 1–4) → revision 2 (ready, windows 1–6)"
  can be reconstructed exactly.

## 6. Provider calls and billing uncertainty (M6)

### 6.1 Attempt ledger

Each physical provider send gets a server-generated `attempt_id`, committed
with its reservation before dispatch. It binds owner, job, window,
`attempt_no`, request fingerprint, price/currency scale and reserved ceiling.
Those bindings never change; reuse with different bindings is an error.
Every network retry (including transport-layer automatic retries) is a new
physical attempt with a new id. Disable unaccounted transport retries.

State/result reports and budget reservations replay under the same
`(owner, attempt_id)`. Replaying a local write, heartbeat, late response or
orphan event does **not** create a new attempt. Orphan reports reference the
existing reservation by owner and attempt id; unknown/cross-owner ids fail
closed for reconciliation, never silently disappear from admission checks.

Each attempt has these states:

```
reserved ─send─► in_flight ─┬─► succeeded          (response received and validated)
                            ├─► failed_definitive  (an error the provider documents as not billed, e.g. a 4xx before execution)
                            ├─► failed_billed      (an error after execution, e.g. invalid output)
                            └─► uncertain          (timeout, connection reset, worker crash, lost lease)
```

- **`reserved`** is committed **before** sending. It records:
  - `reserved_cost`, a ceiling of `estimated_input_tokens + max_output_tokens`
    at the configured price;
  - the provider idempotency key, if any (§6.5).
- **`in_flight`** is committed immediately before the network call.
- After a worker crash, the reclaimer turns every `reserved` attempt into
  `released` (it was never sent) and every `in_flight` attempt into
  `uncertain`.

### 6.2 What fencing does and does not guarantee

- Fencing guarantees that **at most one result per window is recorded and
  published**.
- It does **not** stop a provider call that already ran from being billed.
- In the failure sequence below, the provider may bill **twice**:
  1. a worker calls the provider and the provider bills;
  2. the worker crashes before writing the result;
  3. another worker takes over and calls the provider again for the same
     window.

### 6.3 Conservative accounting

**Aggregate canonical attempts, not event rows or an uncertain/orphan
UNION ALL.** For the selected owner/job budget scope, join all state and
orphan/usage reports on `(owner, attempt_id)`, then sum exactly once per id:

```
charge(a) =
  verified actual cost                   if billed usage is evidenced
  reserved ceiling                       if unresolved uncertain or orphaned
  0                                      if released or definitively unbilled
hold(a) = reserved ceiling                if reserved/in_flight and not orphaned
spent = Σ charge(a)
held  = Σ hold(a)
admit iff spent + held + reserved_cost(new) ≤ approved_budget
```

- An orphan is an annotation of a call, **never an independent charge**.
  `in_flight → uncertain` moves the reservation from held to spent; an
  orphan report for that id cannot add another ceiling.
- An unresolved attempt counts its full ceiling. A verified receipt replaces
  that one amount; it is not added to it. Repeated reconciliation and duplicate
  callbacks have zero budget delta. Unknown outcomes are never assumed free.
- Known actual overruns are recorded even above the ceiling and block new
  calls when they exhaust the budget; they are not clamped or discarded.
- Conflicting snapshots, request bindings or usage amounts fail closed for
  reconciliation. Do not choose the first/last row or the cheapest report.
  A terminal unbilled state plus an orphan is a contradiction, not a refund.
- Accounting events carry a unique event id, but event ids are not charging
  identities. A canonical attempt view is derived from them. Budget and
  admission updates must be atomic in the future production transaction;
  enforce PK/FK/owner constraints before writes.
- An orphan without usage evidence keeps the attempt uncertain; a verified
  receipt resolves its uncertain amount. Evidence here means authenticated
  provider usage/billing data. The lab only receives synthetic evidence
  references; it does not authenticate receipts or implement storage.
- The pure `budget_totals` projection and tests exercise duplicate snapshots,
  uncertain plus repeated orphan reports, late receipts, held funds,
  distinct retries, owner namespaces and conflicting reports. They are not
  the worker/fault-injection tests in §9/§10.

### 6.4 When the learner must approve again

The job moves to `needs_budget_approval`, with an explanation, when either:

- the next attempt would exceed the approved budget; or
- uncertain spend exceeds `max(1 window ceiling, 10% of the approved
  budget)`; or
- the same window has 2 `uncertain` attempts.

A retry after approval re-reserves. There is no automatic second retry on an
uncertain attempt without approval when a paid provider is configured.

### 6.5 Provider idempotency keys

- Rely on provider-side de-duplication **only if** the configured provider
  documents idempotent request keys for this endpoint and the adapter
  declares `supports_idempotency_key = True`.
- Then each attempt sends `idempotency_key = sha256(job_id, window_index,
  checkpoint_generation)`, identical across retries of the same window, and
  a physical retry still gets a **new attempt_id** and reservation, even
  though the provider key is unchanged. A key alone never refunds an
  uncertain attempt or bypasses §6.3/§6.4. Key validity/retention must also be
  checked before relying on provider-side de-duplication.
- If authenticated provider evidence proves both sends share one billed
  execution, reconcile the allocation once: one attempt carries the proven
  charge, and the replay attempt carries evidenced zero cost. Without that
  proof, keep conservative reservations for both physical sends. The budget
  aggregate never de-duplicates by provider key alone.
- Otherwise the system **assumes possible double billing** on every retry of
  an uncertain attempt, and §6.3 and §6.4 apply.
- The `llm_client` in this repository sends no idempotency key today, so the
  conservative rules apply.

## 7. Timeouts, retry and cancellation

- **Provider calls.** 60 s per call. Automatic retries are allowed only for
  `failed_definitive` (429 and documented pre-execution errors), at most
  2, with back-off, and within the budget.
- **Uncertain attempts** follow §6.4.
- **Invalid output** (`failed_billed`) is not retried automatically.
- **Lease TTL** is 2 × (call timeout × 3), with heartbeat extension.
- **Cancellation** sets `cancel_requested`. It is checked before each
  attempt. An `in_flight` call is allowed to finish and is recorded, but no
  batch is published after cancel.

## 8. Owner isolation and Study Bank

- Every table is owner-scoped with forced RLS. Workers set `app.current_owner`
  from the claimed job row.
- A job or batch id belonging to another owner returns 404, identical to a
  missing one.
- **Jobs never write Study Bank items.** Study Bank writes happen only
  through the existing decision path, with its idempotency key, version
  check and de-duplication.

## 9. Executable test specification: jobs and batches — **NOT EXECUTED**

| # | Test | Expected |
|---|---|---|
| J1 | Create the same job from 5 threads | One job |
| J2 | Create on a ready job | Its current batch; stub call count 0 |
| J3 | Two workers claim | One succeeds |
| J4 | Worker A's lease expires, B reclaims, then A writes a window and publishes | A's writes affect 0 rows; A's attempt appears in `orphan_attempts`; B's batch is current |
| J5 | Window 3 fails; retry | Window 3 goes `failed → pending → succeeded`; the checkpoint is inserted once; windows 1–2 keep their checkpoints untouched |
| J6 | Partial (revision 1), retry, ready (revision 2) | Two batches; revision 1 is `superseded` and readable with its decisions; `current_batch_id` is revision 2; each revision lists its checkpoint set |
| J7 | Accept in revision 1, then publish revision 2 | The revision 1 decision is unchanged; revision 2 shows the "Already in Study Bank" hint; the Study Bank is unchanged |
| J8 | Reject in revision 1, then publish revision 2 | Not recommended in revision 2; listed as `previously_rejected`; the revision 1 state is unchanged |
| J9 | A decision on a superseded batch | 409 `batch_superseded`; nothing written |
| J10 | Cancel during window 3 | `cancelled`; no new batch; checkpoints kept |
| J11 | Owner B gets, retries or decides on owner A's job or batch | 404, as for missing; no change |
| J12 | A worker without an owner context | RLS returns nothing; it fails closed |
| J13 | Accept a curated candidate after its sentence changed | 409 `candidate_source_changed` |
| J14 | Accept a duplicate of an archived item with a note, a manual meaning and SRS | Item unchanged; provenance appended |

## 10. Fault injection specification: billing — **NOT EXECUTED**

A stub provider counts calls and simulated billing.

| # | Fault | Expected |
|---|---|---|
| F1 | The stub returns successfully; the worker is killed before the success transaction | After lease expiry the attempt is `uncertain` and counted at its ceiling; the next attempt needs approval if §6.4 applies; the stub's billed counter is 2 after a retry, and the ledger shows 1 succeeded and 1 uncertain |
| F2 | As F1, with a stub that honours valid idempotency keys | New attempt id, same provider key; reserve conservatively and obey approval rules until a billing/replay receipt proves one charged execution; then allocate one actual charge and one evidenced zero |
| F3 | The worker is killed after `reserved`, before `in_flight` | The attempt is `released`; not counted; no approval needed |
| F4 | A timeout on send | `uncertain`; counted; at most one automatic retry within budget, then `needs_budget_approval` |
| F5 | Two windows uncertain, crossing 10% of the budget | `needs_budget_approval` before the next attempt |
| F6 | The budget allows 4 of 6 windows | Windows 5–6 are `skipped(budget)`; the job is `partial`, never `ready` |
| F7 | The stub returns invalid JSON | `failed_billed`; counted; no automatic retry |
| F8 | A stale worker receives a provider response after losing its lease | Response discarded; `orphan_attempts` records the cost; no window or batch change |
| F9 | A crash during publish (after the reduce, before commit) | No batch revision appears; `current_batch_id` is unchanged; the re-run publishes exactly one revision |
| F10 | Reconciliation lowers an uncertain cost with evidence | `spent` decreases by the evidenced amount only; it can never go below the succeeded and billed totals |


### F3 accounting identity fault specifications — NOT EXECUTED on a worker

| # | Scenario | Required future transactional result |
|---|---|---|
| A1 | One in_flight attempt becomes uncertain, then emits 100 orphan callbacks | One attempt id; one ceiling charged; held decreases once |
| A2 | The orphan callback arrives before/after the reclaim event | Same canonical charge and admission decision |
| A3 | Late authenticated receipt is replayed after reconciliation | Ceiling replaced once; no second debit or second refund |
| A4 | Two physical retries share a provider key but have no billing proof | Two attempt ids; conservative cost for both |
| A5 | Receipt proves an idempotent replay has zero incremental cost | One charge allocation plus evidenced zero, never two refunds |
| A6 | Unknown/cross-owner attempt, conflicting payload or conflicting receipt | Reject and stop budget admission pending reconciliation |
| A7 | Two workers reserve against the last available budget simultaneously | At most the funded attempt proceeds; spent plus held never over-admits |

The isolated arithmetic tests cover projections relevant to A1/A3–A6.
A2 event persistence/order and A7 transactional concurrency require the
future worker/database and remain NOT EXECUTED.
