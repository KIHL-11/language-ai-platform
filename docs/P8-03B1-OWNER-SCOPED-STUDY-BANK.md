# P8-03B1 — Owner-scoped Study Bank and Daily Review

Status: **implemented and tested with synthetic PostgreSQL 16 data; not
production deployed**. This change does not migrate Personal Desktop records or
enable cloud AI candidate persistence.

## Boundary and storage selection

In Personal mode, Study Bank v3 JSON and the existing deterministic Daily Review
scheduler are unchanged. In cloud mode, `AuthenticatedPrincipal.user_id` is the
only tenant key. The application must be bootstrapped with
`app.state.cloud_study_bank_repository`; missing identity, owner provisioning or
repository fails closed, and cloud requests never fall back to Personal JSON.

The reversible migration pair
`backend/migrations/postgresql/0002_owner_scoped_study_bank.{up,down}.sql`
adds `cloud_study_items` and normalized `cloud_study_media_provenance`. Every
primary, unique, lookup, update and delete key is owner-qualified. Foreign keys
reference the trusted `cloud_users` registry and the same-owner Library row.
Both Study tables enable and force owner policies using a transaction-local
`app.current_owner` set only from the authenticated principal. RLS is defense in
depth for an omitted SQL predicate; production must use a non-superuser,
non-`BYPASSRLS` application role.
The database constrains kind/language, ReviewState counters, UTC timestamps,
ease, rating history, provenance JSON shape and monotonic revision.

Production rollback must restore a tested database backup. The down migration
is for isolated development/test environments, not ad-hoc deletion of learner
data.

## Learning-state semantics

The Python `schedule_review` function remains the single source of truth for
Again/Hard/Good/Easy. PostgreSQL stores all existing ReviewState fields. Rating
locks the owner-qualified item row, checks `expected_review_count`, computes one
transition, and commits it atomically. Stale/double ratings return 409.

Study item updates and deletes require `expected_revision` in cloud mode;
Personal mode keeps it optional for compatibility. Current cloud UI mutations
send the revision returned by the server. Duplicate saves converge on
the unique owner/kind/source-language/target-language/normalized-text key,
merge new provenance, and preserve ReviewState, learner meaning/note, active
state and archive state. A duplicate never restores an archived item. Deletes
retain the existing hard-delete semantics; historical Chunk Memory is separate
and is not changed by cloud Study Bank operations.

## Provenance authorization

Manual `ai_text` provenance is bounded user-submitted evidence. Media provenance
is accepted only when the owner-scoped Library repository can read the item and
the submitted Lesson ID, URL, title, language, sentence ID/text/translation and
timestamps and source/target languages exactly match its authoritative Lesson
snapshot, and the normalized saved expression must occur in that sentence.
Wrong-owner and
missing Library IDs are the same 404. Speaking provenance remains 503 until
Speaking has durable owner-scoped storage. Cloud writes do not synchronize the
legacy JSON Chunk Memory because that store has no proven cloud ownership.

The normalized provenance row is inserted in the same transaction as the Study
item. A concurrent Library deletion therefore makes the foreign key fail and
rolls back the Study write. Later Library deletion nulls only the normalized
live link; the immutable JSON evidence snapshot remains, preserving the existing
Study Bank behavior after source deletion.

## Transactions and isolation

Each bulk save is one PostgreSQL transaction. `INSERT ... ON CONFLICT DO
NOTHING` plus `SELECT ... FOR UPDATE` serializes duplicate provenance merges.
Updates and ratings lock only the selected `(owner_user_id, item_id)` row.
Driver/connection failures become a bounded 503 and roll back the whole
transaction. Resource lookups always include the owner, so foreign IDs and
unknown IDs are non-enumerating 404s.

The synthetic integration suite provisions two users against PostgreSQL 16 and
tests cross-tenant CRUD/rating/provenance, concurrent duplicate saves and
ratings, revision/review conflicts, forced transaction rollback, archive/SRS
preservation, and absence of Personal JSON writes.

## Deferred gates

- P8-03B2 durable AI candidate batches and provider single-flight/cost controls;
- durable owner-scoped Speaking and Chunk Memory storage;
- production authentication, CSRF/session lifecycle and owner provisioning;
- production migrations, backups/restores, monitoring, retention/export and
  optional PostgreSQL RLS;
- explicitly consented Personal-to-cloud import.

Until those gates pass this is a cloud-tested persistence foundation, not a
production-ready public deployment.
