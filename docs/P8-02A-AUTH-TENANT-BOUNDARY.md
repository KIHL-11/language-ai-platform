# P8-02A — Server-owned identity and tenant boundary

Status: **implemented as a cloud fail-closed foundation; not a production auth
system**. No Email, WeChat, OAuth, PostgreSQL migration, or personal-data import
is implemented by this change.

## Deployment modes and identity contract

`LANGUAGE_AI_DEPLOYMENT_MODE` has two accepted values:

- `personal` (the default) preserves the existing Personal Desktop request and
  browser learner-ID contracts.
- `cloud` requires a trusted server-side identity verifier. If none is installed,
  tenant and protected-workload requests fail with `503
  cloud_identity_not_configured`. An invalid/expired session fails with `401`.

The verifier is installed by trusted server bootstrap as
`app.state.identity_verifier`; this repository intentionally provides no public
header, environment default, Email login, or OAuth implementation. In tests,
synthetic principals are injected only through FastAPI dependency overrides.
`X-User-Id` is never an authentication mechanism.

`AuthenticatedPrincipal.user_id` is the server-owned tenant key. In cloud mode,
legacy body/query `user_id` remains temporarily present for compatibility but
must equal that principal or the request fails with `403 identity_mismatch`.
The value never selects a different tenant. P8-02B should remove legacy claims
from new cloud contracts once the frontend migration is versioned.

## Storage boundary

Study Bank, Review, Chunk Memory, and Speaking JSON already record a user ID.
P8-02A scopes their API calls to the authenticated principal and keeps their
existing resource lookups non-enumerating (`404`) when an ID belongs to another
owner.

P8-02B1 adds a separately configured PostgreSQL owner-scoped adapter for core
Library CRUD/progress and authorized audio delivery. Personal Library v3 JSON,
local media, direct `/media` paths, transcript repair, Shadowing, and cross-store
Library joins are unchanged. Those unowned legacy paths remain usable in
Personal Desktop and fail closed in cloud mode. No owner fields are silently
written into legacy JSON. See `docs/P8-02B1-OWNER-SCOPED-LIBRARY.md`.

`OwnerScopedRepository` records the minimum P8-02B persistence contract:
list/get/create/update/delete must all take the server-owned owner ID, uniqueness
must be owner-scoped, and wrong-owner IDs must be indistinguishable from missing
IDs.

AI credential minting is additionally closed in cloud mode until per-owner rate
and budget controls exist (`503 cloud_provider_controls_not_configured`). An
authenticated session alone is not sufficient to issue a paid-provider token.

## Route, store, and asset matrix

Legend:

- **public**: no tenant record; must not reveal a credential or learner data.
- **cloud-auth**: principal is mandatory in cloud; legacy `user_id` is checked
  against it.
- **personal-only**: legacy store/artifact has no proven cloud ownership and is
  deliberately refused in cloud pending P8-02B.

| Surface | Class | Read/write/credential implications |
| --- | --- | --- |
| `/api/health` | public | cloud response is only `ok`; Personal mode retains local provider status fields |
| `/api/voices`, `/api/ipa`, `/api/segment`, `/api/dictionary/lookup` | public | immutable/local lexical or deterministic data; no learner records |
| `/api/speaking/providers` | public | non-secret configured/model capability metadata only |
| `/`, `/assets/*`, SPA files, OpenAPI docs | public | application assets/schema; no generated learner media |
| `/api/lesson/create` | cloud-auth | stateless lesson construction; no owner persistence |
| `/api/media/discover` | cloud-auth | bounded external search; production quotas/rate limits remain P8-02B |
| `/api/tts`, `/api/transcribe`, `/api/extract-doc` | cloud-auth | external/CPU/upload workload; no durable upload; production abuse limits remain P8-02B |
| `/api/assess` | personal-only provider gate | server Azure use; cloud budget/rate policy not implemented |
| `/api/realtime/client-secret`, `/api/speaking/gemini/client-token` | personal-only provider gate | credential issuance blocked in cloud until authenticated quota/budget service exists |
| `/api/extract`, `/api/lesson/from-url` | personal-only | generates unowned local media paths; media ownership and SSRF/egress policy deferred |
| `/media/*` | personal-only | generated file path has no tenant owner; path traversal remains rejected |
| `/api/config/azure` GET/POST | personal-only | local secret configuration file; never a cloud account-management API |
| Library core list/detail/create/progress/listening/delete/audio | cloud-auth when an owner-scoped repository is configured | principal-scoped PostgreSQL metadata; wrong owner is a non-enumerating 404; no fallback to JSON |
| Library transcript repair and shadowing attempt/cache | personal-only | not migrated to the owner-scoped repository; remains fail closed in cloud |
| Library study candidates/contextual word save/status | personal-only | depends on an unowned Library item even though Study Bank is user-scoped |
| `/api/chunks/from-lesson`, `/api/chunks/search` | personal-only | legacy/global chunk index is not a tenant repository |
| Chunk Memory create/from-lesson | personal-only | accepts legacy Lesson/media provenance that cannot yet be owner-verified |
| Chunk Memory list/practice/archive | cloud-auth | principal scopes reads and mutations; forged client/resource IDs rejected |
| Study Bank bulk save | personal-only | accepts Library/Speaking provenance that cannot yet be owner-verified generically |
| Study Bank list/update/delete | cloud-auth | principal scopes records; forged client/resource IDs rejected |
| `/api/review/*` | cloud-auth | principal scopes summary/queue/rating; SRS state is unchanged by auth layer |
| Speaking context/session routes (non-media) | cloud-auth | principal scopes targets, sessions, events, finish, and safe tools |
| Speaking media context / `get_media_context` tool | personal-only | depends on unowned Library records |
| `/api/integration/target-languages` | cloud-auth | derived only from owner-scoped Study Bank |
| Integration overview/evidence | personal-only | currently joins owner-scoped data with global Library summaries |
| Speaking suggestion save | cloud-auth | session and Study Bank/Chunk writes use authenticated owner |
| uploads to assess/transcribe/extract-doc/shadowing | mixed as above | temporary files only; shadowing remains personal-only until Library ownership exists |

There is no export endpoint or object-storage signed URL in the current API.
Future exports and media URLs must derive owner from the principal and use
short-lived owner-scoped authorization.

## Local learner-ID claim/import contract (design only)

Possession of `language_ai_learner_id_v1` is never proof of cloud ownership.
A future import must follow this explicit one-time sequence:

1. The learner completes a trusted cloud login and receives a server-owned user
   ID.
2. The local app creates an export snapshot and record-count/hash manifest;
   source files remain untouched.
3. The server creates a short-lived, single-account import grant bound to that
   authenticated user, an idempotency key, and a bounded manifest. The browser
   learner ID is provenance only, not authority.
4. The user previews destination account, item counts, conflicts, archive state,
   SRS counters/dates, provenance, Library media metadata, and bounded Speaking
   history, then explicitly confirms.
5. A transaction re-keys accepted records to the server owner, preserving IDs
   where safe, meaning/note, ReviewState, archive state, provenance, and retention
   limits. Cross-owner references are rejected; duplicate conflicts remain
   visible and resumable.
6. The server verifies record counts/hashes and a read-back acceptance suite,
   records the completed import batch, and prevents replay. Failure leaves the
   local source and prior cloud state unchanged; the local export is the rollback
   artifact.

No part of this flow is executed or persisted by P8-02A.

## P8-02B blockers

- Select and implement a trusted session verifier, rotation/revocation, CSRF,
  login rate limiting, and secure cookie policy.
- Migrate Library, media assets, global chunk search, and integration joins to
  `OwnerScopedRepository` implementations (proposed PostgreSQL/object storage),
  with non-enumerating cross-owner tests.
- Implement per-owner provider quotas, budgets, audit events, and concurrency
  limits before credential minting or costly workloads open in cloud.
- Add signed owner-scoped media delivery instead of exposing filesystem paths.
- Implement the separately approved import/claim workflow and rollback tests.
- Add production backup/restore, retention/deletion/export, admin separation,
  and optional database RLS defense in depth.

Until those are complete, this application must not be represented as a
production-ready public multi-tenant deployment.
