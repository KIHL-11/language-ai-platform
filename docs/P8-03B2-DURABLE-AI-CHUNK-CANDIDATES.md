# P8-03B2 — Durable Cloud AI Chunk Candidates

## Boundary

Cloud candidate batches are stored in PostgreSQL and are owned exclusively by
the server-authenticated principal. `user_id` remains in the legacy request
contract, but a mismatch is rejected. Cloud mode never falls back to the
Personal Desktop in-memory candidate store, JSON Study Bank, or global Chunk
Memory.

Personal Desktop keeps the P8-03A in-memory proposal workflow unchanged.

## Durable model

Migration `0003_owner_scoped_ai_chunk_candidates` adds owner-qualified tables
for batches, candidates, immutable source evidence, generation reservations,
decisions, and decision idempotency keys. Every table has `FORCE ROW LEVEL
SECURITY` using the transaction-local `app.current_owner` setting. Production
connections must use a least-privilege `NOSUPERUSER NOBYPASSRLS` runtime role.
The runtime role needs `SELECT, UPDATE` on `cloud_library_items` because source
validation takes a `FOR KEY SHARE` row lock to close Library update/delete races;
it does not update the row in the candidate workflow.

Library evidence stores an immutable item-id snapshot and a same-owner Library
foreign key. A Library deletion after candidate creation clears only the live
foreign-key column; the bounded evidence snapshot remains reviewable. Deletion
during generation fails the candidate transaction. Acceptance after the source
has been deleted fails when Study Bank provenance is validated by its same-owner
foreign key.

## Generation idempotency and crash recovery

Generation first reserves `(owner, idempotency_key, request_fingerprint)` in a
short transaction. The deterministic Mock Adapter runs only after storage and
owner provisioning succeed, and outside the database transaction. Completion
atomically writes the batch, candidates, evidence and completed reservation.

An unfinished reservation has a bounded lease. A process restart can reclaim an
expired lease with the same request fingerprint. A different fingerprint is a
409 conflict. Concurrent requests cannot both own the live lease. P8-03B2 does
not enable a paid provider; a future paid adapter must additionally provide a
provider-side idempotency or durable worker contract before activation.

## Decisions and Study Bank

Accept, edit-and-accept and reject require the candidate's expected version and
a decision idempotency key. Decision keys are serialized across workers.

Accept and edit-and-accept use the P8-03B1 Study Bank upsert on the **same
PostgreSQL connection and transaction** as the candidate transition and durable
decision response. Therefore a Study Bank write, candidate state, and replay
record either all commit or all roll back. Duplicate Study Bank saves preserve
the existing ReviewState, learner meaning/note, provenance, and archived state.
Reject writes no Study Bank or Daily Review state. Cloud Chunk Memory sync stays
disabled because it is not owner-scoped PostgreSQL storage.

## Retention

New cloud batches and idempotency records have a 30-day default retention
window. `expire_due(owner, limit)` is an explicit, auditable, owner-scoped,
bounded maintenance operation; it is not an untracked background promise.
Expired proposed candidates enter the terminal `expired` state and cannot be
accepted. A previously completed decision may still be replayed by its original
idempotency key until that key is reclaimed. A later bounded maintenance pass
purges batches that have remained expired for another retention window, along
with their candidate decision records; accepted Study Bank items are not
deleted.

Pending generation is capped per owner. Inputs, selected Library sentences,
candidate counts, evidence, explanations, and flags retain the P8-03A Pydantic
bounds. No provider prompts, credentials, audio, full Library, or unbounded
transcripts are persisted.

## Failure behavior

- Missing trusted identity: 401/503 at the P8-02A identity boundary.
- Claimed identity mismatch: 403.
- Missing candidate, Study Bank, or Library adapter: 503 before model use.
- Foreign or missing batch/candidate/source: non-enumerating 404.
- Stale candidate, conflicting idempotency key, or expired batch: 409.
- PostgreSQL failure: controlled 503 with no driver details.
- Transaction failure: complete rollback, including Study Bank changes.

## Deployment gates

This phase uses only `DeterministicMockChunkAdapter`; no OpenAI, Gemini, or
external AI request is made. Production still requires migration backup/restore
drills, retention-job scheduling and observability, minimum-privilege runtime
role provisioning, production authentication, and a separate durable contract
before enabling any paid model adapter.
