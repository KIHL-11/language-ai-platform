# P8-04B1 cloud Topic Mock storage

This stage opens only deterministic text Topic attempts. Existing cloud Speaking
session/provider/media/tool routes remain 503; no microphone or credential is
created. Personal Desktop continues using its original JSON paths and contracts.

Migration 0004 creates owner-qualified sessions, target snapshots and durable
request records, all FORCE RLS with transaction-local app.current_owner. Session
JSON is a bounded validated SpeakingSession containing finalized turns and usage;
feedback is persisted on finish. No provider protocol, key or audio is accepted.

Cloud bootstrap installs PostgresTopicRepository as app.state.cloud_topic_repository.
The runtime role must be NOSUPERUSER NOBYPASSRLS with SELECT on cloud_users,
SELECT/UPDATE on cloud_study_items (row locking), and CRUD on the three cloud_topic
tables. It must not own deployment credentials or schema-changing permissions.

The server principal supplies owner identity; claimed identity mismatch is 403.
Other owners' attempt IDs produce the same 404 as missing IDs. Targets must be
active owner-owned chunks of the topic language and are locked during creation.
Their immutable snapshots remain readable after archive/deletion; FK deletion
only clears the live reference. Practice never rates or edits Study Bank or SRS.

POST start/turn/finish requires an idempotency key and catalog version in cloud.
A transaction serializes the key, verifies its payload fingerprint, locks the
session and applies expected event count. Learner turn, partner reply and evidence
commit together; finish and its feedback commit together. A replay returns the
original response across process restarts. Changed payload/key reuse conflicts.
Terminal attempts reject fresh mutations. Version mismatch requires preview reload.

GET topic attempts returns the latest ten IDs. GET an attempt resumes finalized
turns or reads saved feedback. The UI offers reload after uncertain network or
concurrency failures. A resume does not restart providers or activate audio.

Retention/export/deletion boundary: no automatic production purge or export is
enabled. Sessions are bounded to 40 turns/30,000 transcript characters and at most
five target snapshots; request rows cascade when their session is deleted, and all
rows cascade when a cloud account is deleted. A production release requires an
owner-scoped retention/export/delete policy, quotas and operations review; total
account history is not capped by this MVP. No personal data migration occurs.

Deployment remains blocked on trusted production authentication, managed database
migrations/backups, operational limits and independent approval. P8-04B2 must
separately authorize durable live provider sessions and cost controls.
