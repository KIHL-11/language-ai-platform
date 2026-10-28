# Phase 8 — Privacy-First Product Analytics Contract (design proposal)

Status: DESIGN / NOT IMPLEMENTED. No real user behavior collection is authorized by this document.

## 1. Two distinct products

The learner's Study/Review progress UI reads the **learner's own authorized learning records**. Admin Analytics reads **authorized aggregate event metrics** for product decisions. They must not share an unrestricted endpoint or imply staff access to user transcripts, raw voice, Study Bank meanings, or media URLs.

Questions to answer: which modes are used, where learners leave a guided flow, whether approved AI Chunks get revisited, whether topic Speaking sessions are completed, how product cohorts retain, where providers fail, and how much AI usage costs. No vague 'engagement score' should substitute for explicit metrics.

## 2. Event envelope (version 1 draft)

Allow-list fields: event_id (random unique UUID, dedup key), event_name (registered literal), schema_version (integer), occurred_at_utc (trusted server reception timestamp plus optional bounded client offset), actor_pseudonym (rotatable server-scoped identifier, not email or provider subject), anonymous_session_id (if permitted), source (web/api), app_version, experiment_id (if assigned), and strictly allow-listed properties. A server-side authenticated owner ID may be used internally for privacy operations but must not appear as a public dashboard dimension. Limit event size, property cardinality and replay window; reject unknown fields rather than pass arbitrary JSON through.

Initial registry and safe properties (no free-text):

| Event | Safe properties |
| --- | --- |
| lesson_started / lesson_completed | language, stage_count, duration_bucket, result |
| chunk_suggestion_viewed / chunk_suggestion_decided | source_kind, language, decision (accept/edit/reject), proposed_count_bucket |
| chunk_saved | language, item_kind, source_kind, was_duplicate |
| review_session_started / review_session_completed | language, reviewed_count_bucket, completion_status |
| speaking_started / speaking_completed | language, level, mode, provider_kind, duration_bucket, status |
| topic_started / topic_completed | topic_id (approved catalog only), language, level, completion_status |
| provider_request_result | provider_kind, operation_kind, status_class, latency_bucket, token_usage_bucket, cost_bucket |
| session_auth_result | method (email/wechat), outcome (success/failure); aggregate only, no address or provider user ID |

Forbidden fields: email, phone, IP as a property, precise GPS, OAuth IDs, tokens, keys, raw audio, complete transcripts, sentence text, Chunk text/meaning, prompts, media URL, document titles, arbitrary errors/stack traces. Sanitize server-side; log exception categories only. No automatic route-path strings with resource IDs. Avoid session replay, unbounded client recordings, fingerprinting, and third-party pixel scripts by default.

## 3. Consent, legal purpose and retention

Define lawful basis per purpose, region and audience with privacy/legal review before collection. In jurisdictions where required, do not start non-essential analytics until valid consent; provide a usable reject option and consent withdrawal. Distinguish security/operational logs from optional product analytics. A DNT flag alone is not a legal basis. Personal Desktop mode has analytics disabled by default. Maintain clear privacy notice, processor contracts, deletion/export procedures, retention schedule and data-region decision before real instrumentation. Aggregated metrics should enforce minimum cohort sizes and role checks to reduce re-identification.

## 4. Metrics and correctness

Definitions, to avoid misleading charts:

- Activation: number of newly authenticated learners who complete >=1 guided learning or topic attempt in first 7 days / eligible new learners. Do not include test accounts.
- Week-1 retention: distinct activated learners who return to an intentional learning activity in days 7–13 after activation / activated cohort. Define the timezone and cohort cut exactly; use UTC for storage.
- Lesson funnel: unique eligible sessions crossing start -> active stage -> completed; retries/double clicks are deduplicated by event_id and entity_id inside the secure processing layer.
- Chunk adoption: accepted proposal batches / presented proposal batches; distinguish proposals from actual saved Study Items. Review return: saved chunks reviewed at least once within an agreed observation window / eligible saved chunks.
- Speaking completion: completed (not interrupted or failed) / started with a real session ID; distinguish provider-specific errors and platform failures.
- AI unit economics: provider-reported usage and cost estimate with timestamped price version; no fake precision when provider usage isn't available.

Event collection is at-least-once: use client event_id + server uniqueness and idempotent aggregation. Reconcile discrepancies between client signals and server-authoritative Study Bank / review / speaking outcomes. Use realistic synthetic test data for dashboards, and **label demo data unmistakably**.

## 5. Admin access and rollout gates

Admin-only endpoints need explicit permissions, MFA where appropriate, audit logs, export limits and aggregate query constraints. No admin self-service read access to full learner text. Separate staging event ingestion with fake users from production. Build and verify opt-in/opt-out, deletion, retention expiration, role isolation, synthetic cohort/funnel fixtures, idempotency, unknown-property rejection and dashboard accessibility before enabling any production instrumentation. Analytics implementation should be additive and cannot interfere with learning persistence.

Deferred decisions: hosted stack vs first-party ingestion, legal basis and consent requirements, retention days, minimum cohort threshold, pricing telemetry, notification preferences, and admin role operators. No production tracking or third-party marketing SDK is in scope now.
