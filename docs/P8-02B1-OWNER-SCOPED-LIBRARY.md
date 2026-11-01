# P8-02B1 — Owner-scoped Library and media foundation

Status: **implemented with synthetic PostgreSQL data and a test/development
object adapter; not production deployed**. This change does not migrate Personal
Desktop data, select a production object-storage provider, or implement a real
login/session system.

## Decision

Cloud Library records use PostgreSQL. Every Library and media key includes the
server-owned `AuthenticatedPrincipal.user_id`; no query parameter, request body,
`X-User-Id`, Library item ID, or object key can select a tenant. The application
receives an `OwnerScopedLibraryRepository` from trusted process bootstrap as
`app.state.cloud_library_repository`. In cloud mode, a missing identity verifier,
missing repository, or unprovisioned owner fails closed. There is no fallback to
`learning_library.json`.

Personal Desktop remains the default deployment mode and continues to use
`LearningLibraryStore`, schema v3 JSON, `library_media`, and the existing local
API behavior. Cloud code never reads or rewrites those files.

## PostgreSQL structure and invariants

Reversible migration files:

- `backend/migrations/postgresql/0001_owner_scoped_library.up.sql`
- `backend/migrations/postgresql/0001_owner_scoped_library.down.sql`

The schema contains:

- `cloud_users`: minimal server-owned owner registry. P8-05 may extend/replace
  its provisioning workflow, but Library operations never create an owner from
  request data.
- `cloud_library_items`: composite primary key `(owner_user_id, item_id)`,
  owner-scoped unique `(owner_user_id, source_key)`, validated JSONB Lesson and
  progress snapshots, and a monotonic revision.
- `cloud_library_media_assets`: composite owner/item foreign key, owner-scoped
  one-audio constraint, content metadata, byte count and SHA-256. A media row
  cannot reference a Library item owned by another tenant.
- `cloud_media_cleanup_outbox`: durable object-cleanup evidence written in the
  same transaction as a Library delete. A failed physical deletion remains
  discoverable and retryable instead of being reported without a cleanup key.

`TIMESTAMPTZ` is used for all times. PostgreSQL constraints reject malformed
owners, empty/oversized source keys, non-positive media size, malformed hashes,
unknown media states/kinds, and backwards timestamps. SQL migrations are
transactional. The down migration is reversible for isolated/test deployments;
production rollback must restore a verified database backup rather than run a
destructive down migration against user data.

## Transaction and concurrency boundaries

Create/upsert is one PostgreSQL transaction using the unique
`(owner_user_id, source_key)` constraint. Concurrent saves of the same source by
one owner converge on one item; another owner receives a distinct item. Progress
updates lock only the selected owner/item row and increment its revision. Delete
removes the owner-scoped database row and cascades only that item's metadata.

Object bytes and PostgreSQL cannot share one atomic transaction. Audio staging
therefore writes a unique object first, commits metadata second, deletes the new
object if the database write fails, and removes the replaced object only after a
successful commit. Delete writes a cleanup-outbox row before cascading the
authorization metadata; successful cleanup acknowledges that row, while failure
retains it with an attempt counter for a future bounded retry worker. The orphan
is inaccessible but remains auditable. No raw filesystem path appears in an API
response.

## Media boundary

`FilesystemMediaObjectStore` is an isolated test/development adapter. It hashes
the owner in the object namespace, generates server-side object keys, writes via
atomic replace, rejects traversal/symlink escape, and is never mounted through
`/media`. The `/api/library/items/{id}/audio` route first performs an
owner-qualified PostgreSQL join and only then resolves the internal object.
The object adapter independently verifies that the object-key owner hash and item
UUID match the authorized metadata; the database also binds the item UUID into
the key constraint. Wrong-owner and missing audio both return the same 404.

Cloud Library creation deliberately clears a Personal Desktop `/media/...`
`Lesson.audio_url`; possession of that URL is not ownership proof. P8-02B1 does
not expose a cloud upload endpoint. Production object storage, upload scanning,
retention, signed URLs/range requests, encryption/KMS and lifecycle cleanup are
P8-02B2 decisions.

## Route state

Cloud-authenticated, owner-scoped routes in P8-02B1:

- `POST /api/library/items`
- `GET /api/library/items`
- `GET /api/library/items/{item_id}`
- `PATCH /api/library/items/{item_id}/progress`
- `PATCH /api/library/items/{item_id}/listening-progress`
- `DELETE /api/library/items/{item_id}`
- `GET /api/library/items/{item_id}/audio`

The core routes derive owner only from the trusted principal. Invalid IDs and
cross-owner IDs are non-enumerating 404s.

Still fail-closed in cloud mode because their complete ownership chain has not
been migrated:

- direct `/media/*` paths;
- transcript repair and Sync Shadowing analysis/reference cache;
- study-candidate/contextual-word Library joins and bulk provenance writes;
- Speaking media context and Integration joins;
- media extraction that creates local unowned files.

These are explicit P8-02B2 work, not hidden fallbacks.

## Synthetic verification and rollback

`backend/tests/test_owner_scoped_library.py` runs against a real PostgreSQL 16
service and an isolated temporary media root. It provisions two synthetic owners
and verifies list, same-source uniqueness, create, read, progress/listening
updates, delete, audio delivery, direct-media refusal, wrong-owner/nonexistent
404 behavior, concurrent upsert, unprovisioned-owner refusal, no Personal JSON
mutation, and fail-closed provenance paths. No test reads or writes real learner
records.

The hosted workflow `.github/workflows/phase8-cloud-foundation.yml` supplies the
test PostgreSQL service, runs the complete backend suite and dependency check,
then runs frontend test/lint/build separately.

Before a future production migration:

1. select region/provider, provision encrypted PostgreSQL and object storage;
2. take and restore-test a database backup plus object inventory;
3. run the up migration in staging and verify composite ownership constraints;
4. import only through the separately approved consent/import contract;
5. compare record counts and owner-scoped hashes, then run cross-owner probes;
6. cut over behind a maintenance/feature gate;
7. rollback application and database/object snapshot together if acceptance
   fails. Never run the down migration as an ad-hoc production rollback.

## Deferred production gates

- real Email/WeChat/OAuth and secure browser sessions;
- CSRF, account lifecycle, session revocation and owner provisioning;
- production PostgreSQL migrations/backups/restore drills and optional RLS;
- production object storage and authorized upload/delivery;
- legacy JSON/media import with explicit user consent and rollback;
- Study Bank, Chunk Memory, Review, Speaking and Integration PostgreSQL cutover;
- per-owner workload/provider quotas, audit events and retention/deletion/export.

Until these gates pass, P8-02B1 is a cloud-tested isolation foundation, not a
production-ready public deployment.
