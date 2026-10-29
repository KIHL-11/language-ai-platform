# P8-03A — AI Chunk Organizer

Status: **implemented with a deterministic Mock Adapter for Personal Desktop;
cloud candidate writes remain fail-closed**. No live model, provider credential,
production migration, or automatic learning-state mutation is included.

## Flow and contracts

The organizer accepts a bounded English or German manual passage (maximum 2,000
characters) or up to 12 sentences from a saved Personal Library item. A model
adapter proposes at most eight chunks. The service validates every adapter
object with a strict schema and verifies that the proposed phrase occurs in the
authorized source. Library sentence IDs, media times, URL, title and translation
are copied from the saved Lesson; the adapter cannot invent them.

The current adapter is `DeterministicMockChunkAdapter`. It performs no network
I/O. Known fixture expressions receive deterministic Chinese explanations;
unknown fallback candidates carry `meaning_uncertain` and an empty explanation
instead of a fabricated translation. Source text is data, not executable prompt
or tool input.

Candidate states are:

`proposed -> accepted | edited_and_accepted | rejected | expired`

Viewing a proposal has no side effect. The learner explicitly accepts, edits and
accepts, or rejects it. Request and decision idempotency keys prevent accidental
replay; expected candidate versions reject stale decisions. Batches are bounded
per owner and globally, and process-local in Personal Desktop, so a restart discards unreviewed proposals
without touching learning records.

Accepted candidates use `StudyBankStore.save_items`. Existing normalization and
deduplication remain authoritative. Duplicate saves merge provenance, retain an
existing meaning/note, preserve every `ReviewState` field, and do not reactivate
an archived item. A new accepted chunk gets the existing immediately-eligible
Daily Review state. Media-backed accepted chunks retain the existing curated
Chunk Memory compatibility synchronization after the Study Bank save. That sync
does not overwrite an existing meaning or practice counters; a bounded response
warning reports a compatibility-sync failure while the accepted Study Bank record
remains authoritative and idempotent. Manual-text candidates have no
invented Lesson/media identity and therefore do not create a fake Chunk Memory
link. Rejection never writes Study Bank, Chunk Memory, or SRS.

Manual input uses an immutable `ai_text` provenance snapshot with an exact source
range. Saved-Library input uses existing media provenance. No audio or complete
Library history is sent to the adapter.

## Identity and cloud boundary

All routes resolve the owner through the P8-02A identity dependency. The legacy
`user_id` is retained only for Personal Desktop compatibility. In cloud mode the
authenticated principal must match the claim, then the request fails with
`cloud_ai_candidate_storage_not_configured` before adapter execution or access to
the Personal JSON Study Bank.

This fail-closed behavior is intentional. P8-02B1 migrated only Library metadata
and media to PostgreSQL; Study Bank/Daily Review and proposal persistence do not
yet have an owner-scoped PostgreSQL adapter. P8-03A must not represent a global
JSON file or process memory as multi-tenant cloud storage. A later migration must
provide transactional, owner-scoped proposal and Study Bank repositories before
these cloud writes can open.

## API

- `POST /api/ai/chunk-suggestions`
- `GET /api/ai/chunk-suggestions/{batch_id}`
- `POST /api/ai/chunk-suggestions/{batch_id}/decisions`

The model cannot assign an SRS rating. Existing Review APIs remain authoritative
for Again/Hard/Good/Easy. Live AI adapters, model cost controls, persistent cloud
candidate batches, and PostgreSQL Study Bank migration are deferred.
