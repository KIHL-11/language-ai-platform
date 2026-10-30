# ADR: provider-neutral browser session foundation

Status: implemented foundation, isolated synthetic verification only. No public
login, external provider, production account or personal-data import is enabled.

## Accounts and authentication

`cloud_users.user_id` is a stable internal server-issued UUID for future accounts.
Existing synthetic fixtures retain their IDs. Browser learner IDs, body/query
user_id and impersonation headers never prove account ownership.

Future provider bootstrap must verify issuer, audience, subject, state/nonce and
fresh proof before mapping `(provider, issuer, subject)` to an internal user.
That mapping must be unique and transactional; no matching by nickname or email
alone. New accounts require the approved provisioner; no request in this stage
creates a cloud_users row. Account linking requires fresh proof for both accounts
and explicit confirmation. P8-05B must implement the provider-specific verifier,
subject mapping store, challenges and rate limiting; until then login returns 503.

`issue_for_verified_account` is a trusted Python service operation, not an HTTP
endpoint. It requires an existing account via FK. Tests call it only for isolated
synthetic users. There is no test-login route, unsigned JWT, header bypass or
browser-ID claim path.

## Session lifecycle and privilege separation

Tokens contain 256 random bits (`secrets.token_urlsafe(32)`). PostgreSQL stores
only SHA-256 digests. They expire absolutely after 12 hours and after 30 minutes
of inactivity. Verification, rotation and revocation use row locks. Rotation
revokes the old token and inserts a fresh one in one transaction, retaining the
original absolute expiry. A failed rotation leaves the old token valid. Logout
revokes the presented session; trusted account-security code can revoke all.
No token or DSN is included in controlled errors or application logs.

Authentication establishes ownership before tenant RLS is possible. Its DB role
is separate: only SELECT/INSERT/UPDATE on cloud_browser_sessions, no learning
table access, no superuser, BYPASSRLS, DDL or account-provisioning privileges.
Learning repositories continue to use their own owner-qualified/RLS roles.
Authentication database failure is 503, not anonymous or Personal fallback.

## Cookie, CSRF, origins and transport

Cookie: `__Host-language_ai_session`, Secure, HttpOnly, SameSite=Strict, Path=/,
no Domain. No insecure-development cookie option exists for cloud.
The configured origin must be a single exact HTTPS origin, without path,
credentials or wildcard. Cross-origin cloud requests are rejected before CORS.
No credentialed cross-origin cloud frontend is supported in this phase.

All unsafe cloud requests under configured session bootstrap require exact
Origin and a valid session plus X-CSRF-Token. The CSRF value is a domain-separated
SHA-256 derivation of the secret session token, returned only by authenticated
same-origin `/api/account/me` or rotation. It is not an authentication token.
It changes on rotation; old CSRF is rejected. SameSite and Fetch Metadata checks
add defense in depth; they do not replace Origin/header checks. Public login is
the only unsafe exception and always fails 503 without mutation.

`GET /api/account/me`, `POST /api/account/session/rotate` and
`POST /api/account/logout` are no-store. Raw session credentials remain HttpOnly.
There is no login UI or automatic provider traffic. A future cloud frontend must
load server identity/CSRF and send the CSRF header on writes; current Personal
Desktop API behavior is preserved. Do not deploy the existing browser learner-ID
flow as a cloud account implementation.

Trusted bootstrap uses `CLOUD_SESSION_DATABASE_URL` and `CLOUD_SESSION_ORIGIN`,
or explicitly installs the repository/policy with configure_browser_sessions.
Partial/invalid config fails closed. With no config, existing identity/storage
gates continue to refuse protected cloud access.

Only ASGI scheme/Host are trusted. Forwarded headers are never read here. A
deployment must disable Uvicorn proxy-header trust unless a separately reviewed
proxy allowlist verifies the actual peer. Use direct TLS or a trusted ASGI TLS
boundary; never expose a server that trusts arbitrary X-Forwarded-Proto/Host.
The TLS proxy must strip incoming forwarded headers and normalize Host. This
stage does not configure or authorize a production proxy.

## Deletion, rollback and migration boundaries

Account deletion cascades authentication sessions; dependent learning deletion
requires its own reviewed policy. Revoke sessions before security deletion.
Expired/revoked rows may be purged by an approved maintenance role; retention,
audit events, rate limits and credential stuffing controls are deployment gates.
Rollback first disables cloud session bootstrap and revokes credentials, then
applies migration 0005 down under controlled maintenance. It must not alter any
learning tables. There is no automatic migration or destructive startup action.

Personal data import is deferred: verified destination login, explicit export,
preview, consent, immutable backup, transactional owner re-keying and restore
verification require separate authorization. No browser UUID can claim records.

Unsupported live Speaking/media/tools/provider credential routes remain closed.
Production blockers: P8-05B verified login/account mapping, trusted proxy/TLS
deployment review, cloud client identity/CSRF integration, quotas, observability,
backup/restore, retention and independent deployment approval.
