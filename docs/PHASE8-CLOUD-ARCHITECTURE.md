# Phase 8 — Cloud Foundation Architecture (design proposal)

Status: DESIGN / NOT IMPLEMENTED. Baseline: main at 234f0587b0f1321a7b8af636e259ebf2f017d3ad (2026-10-28). This document does not authorize production deployment or moving personal data.

## 1. Product boundary and existing assets

Keep React/TypeScript/Vite + FastAPI. Reuse Learning Library, Study Bank v2, Chunk Memory, deterministic Daily Review, Intelligent Speaking Agent, its provider adapters, and DQ-02 read-only dictionary assets. Personal Desktop mode continues to work without an account, cloud database, or tracking. Existing files are not silently migrated or overwritten.

Current identity is a browser-local UUID stored under language_ai_learner_id_v1 and accepted by many FastAPI routes as user_id. This is **not authenticated identity**. Current JSON stores use process-local locks; do not deploy the existing API publicly as a multi-tenant service merely because user_id differs.

Architecture decision: build an account/authorization boundary **before** exposing any multi-user records. Proposed cloud store: PostgreSQL for durable transactional learner records; separate object storage for explicitly uploaded media; immutable dictionary SQLite and forms Sidecar remain isolated read-only lexical resources. Hosting, account provider, data region, and email delivery are undecided and require a separate selection.

## 2. Domain boundaries and candidate tables

| Domain | Candidate tables | Ownership / invariants |
| --- | --- | --- |
| Identity | users, auth_identities, login_challenges, auth_sessions | users.id is server-issued UUID; external issuer+subject unique; no social provider ID as primary owner key |
| Study Bank | study_items, study_provenance, review_states | every row keyed by owner_user_id; stable item IDs and per-item SRS state retained |
| Learning | library_items, media_assets, listening_progress | asset metadata is tenant-scoped; blobs belong to a verified owner |
| Chunks | chunk_practice | compatibility to Study Bank without duplicate rating authority |
| Speaking | speaking_sessions, speaking_turns, corrections, suggestions, target_usage | bounded and privacy-controlled; audio is NOT retained by default |
| Topic training | topic_templates, topic_attempts, topic_attempt_targets | topic templates are curated/global; attempts belong to one owner |
| AI proposals | chunk_suggestion_batches, chunk_suggestions | AI suggestions are non-authoritative drafts until user confirms |
| Analytics | analytics_events, privacy_preferences, deletion_requests | allow-listed event data; admin access separated from user-facing APIs |
| Operations | migration_jobs, audit_events | scoped, redact secrets, preserve append-only evidence |

All tenant tables must have an owner_user_id foreign key plus owner-scoped uniqueness where relevant; foreign keys cannot cross tenants. Use optimistic revision/version for concurrent updates, and use DB transactions for operations that span a Study Bank write and a Chunk Memory compatibility write. Study item deduplication retains the existing normalized lemma/chunk identity, language, and kind, now scoped by authenticated owner. Do not claim SQL row-level security is active until tested; consider DB RLS as defense in depth, not as a substitute for API authorization.

## 3. Identity and authorization contract (P8-02)

- Authentication establishes users.id server-side. API handlers must **never trust user_id from query/body** as authorization, even when the old request shape still supplies it for compatibility. In cloud mode, reject mismatches or ignore legacy user_id after verifying owner from session, per endpoint migration plan.
- Cookie session preferred for same-origin web app: Secure, HttpOnly, SameSite, rotation on authentication, bounded lifetime, CSRF protections for mutating requests, login rate limiting, replay-protected email challenges and clear logout/session revocation. Prefer short-lived verification codes/links over inventing local password storage.
- Email identity uses verified normalized address, not display name. External identities use (provider, issuer, subject) uniqueness. WeChat authorization must validate state and callback, use correct application-specific identifiers, and avoid assuming all WeChat contexts share the same identity. Linking two accounts requires explicit authenticated action and fresh identity proof; never auto-merge by nickname or email.
- Every read/write scope checks authenticated owner; cross-tenant resource identifiers return non-enumerating 404. Storage object access must use authorized short-lived links. Operators/admins use separate roles, auditable actions, minimal privileges; dashboards never imply entitlement to raw transcripts.
- Public unauthed endpoints, e.g., health, have no tenant data. AI credential minting requires authenticated, rate-limited owner context and budget controls. No long-lived provider API keys in frontend.

## 4. Personal Desktop migration boundary

Local mode and cloud mode are separate deployments. No background sync and no upload by default. User explicitly opts in to account import after a verified login, previews record counts, destination account and conflicts, and receives a local export/rollback snapshot. Do not let mere possession of a browser learner_id claim another person's cloud data. Use authenticated one-time import session, record-level ownership re-keying, stable legacy provenance mapping, idempotent import batch IDs and resumable uploads. Preserve existing item IDs when safe, original meaning/note, full SRS counters/dates, archived state, library provenance, and speaking transcript retention limits. Duplicate conflicts require visible resolution; errors leave source untouched. A completed hash check is not proof that a restored account works: add round-trip restore tests.

## 5. API surface and compatibility

Suggested route groups, **not yet implemented**:

- /api/account/me and /api/account/sessions for authenticated identity/session lifecycle.
- /api/account/identities for explicitly binding/unbinding Email/WeChat.
- /api/account/imports for consented local-to-account import, preview, commit, and status.
- /api/ai/chunk-suggestions for bounded AI candidate batches and approval decisions.
- /api/topics and /api/topic-attempts for curated scenarios and completion evidence.
- /api/analytics/events for bounded allow-listed events; /api/admin/analytics for role-gated aggregates.

Do not reuse an untrusted user_id as a tenant selector in any new route. Existing local endpoints may retain their current contract in Personal Desktop mode, but must be wrapped/refactored before the same routes are reachable on a public cloud host. Use one identity-context dependency/service with cross-module authorization tests rather than ad-hoc checks per route.

## 6. Threat model / non-negotiable gates

Threats: horizontal IDOR via user_id or library_item_id, account takeover, forged OAuth callback, identity mis-link, stolen sessions, prompt injection through transcript text, media URL SSRF, expensive LLM quota abuse, analytics re-identification, retention beyond user consent, cross-tenant search/index leakage, and failed restore. Use constrained egress for media extraction; LLM inputs are untrusted content, not instructions. Use encryption in transit/at rest, secrets management, backup restore drills, rate limits, audit logging and data minimization. Ensure tests show a second user cannot view or mutate another user's Library, Chunk, SRS state, Speaking session, media or import job.

## 7. P8-01 acceptance / open decisions

**Architecture review must resolve:** hosting/region and GDPR role analysis, authenticated session strategy, database and backup lifecycle, transactional compatibility boundary, Email verification provider, WeChat official application prerequisites, explicit import consent, analytics purposes/legal basis, admin access model, and retention schedule.

Deliverables: data model diagram or migration DDL design, API contract, threat model, privacy event inventory, migration/rollback design and review sign-off. This document is a starting design contract, not a claim that accounts, a SQL schema, cloud sync or public deployment exist.
