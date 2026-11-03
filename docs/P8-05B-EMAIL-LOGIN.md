# P8-05B1: verified email one-time-code login

Status: implemented with synthetic/sandbox verification only. No real email is
sent, no mail provider credentials exist, and nothing here is production
approved. Master tracker: Issue #75, Workstream A.

## Flow

1. `POST /api/account/email/start {email}` normalizes the address, applies the
   resend throttle, stores a challenge and hands a six-digit code to the
   configured mail adapter. Response: `challenge_id`, `expires_in`, `resend_after`.
   It also sets `__Host-language_ai_login`, a random HttpOnly/Secure/SameSite=Strict
   pre-auth cookie (10 min). The challenge stores only its digest.
2. `POST /api/account/email/verify {challenge_id, code}` only succeeds from the
   browser holding that pre-auth cookie, so a phished code and challenge id cannot
   be redeemed elsewhere. It consumes the challenge
   and, in the same transaction, maps the verified address to an internal account
   (creating `cloud_users` + `cloud_external_identities` on first login) and
   issues a browser session using the P8-05A cookie. A session cookie presented
   with the verify request is revoked, so pre-login sessions never survive.
3. `GET /api/account/providers` reports `{email, wechat}` so the UI can show a
   provider-not-configured state. `wechat` is always `false` in this stage.
4. `POST /api/account/sessions/revoke-all` (session + CSRF) signs the account out
   on every browser. Together with email login this is the recovery path for a
   lost or shared device.

Neither request accepts a user id; extra fields are rejected (422).
`POST /api/account/login` keeps returning 503.

## Limits

| Control | Value |
| --- | --- |
| Code | 6 digits from `secrets`, stored only as an HMAC-SHA256 digest (key `CLOUD_AUTH_CODE_KEY`, not in the database) bound to the challenge id (P8-05B2) |
| Lifetime | 10 minutes, single use |
| Failed attempts | 5 per challenge, then the challenge is closed; 10 per address per rolling day, after which **no open six-digit code is accepted** and new codes are 12-character codes (~59 bits) that the daily cap does not block (P8-05B2; replaces the earlier 24 h refusal) |
| Resend | 60 s between codes per address; 5 codes per address per rolling hour (429 + `Retry-After`) |
| Superseding | a new code closes earlier open codes **from the same browser** only, and only after it was delivered |
| Wrong browser | rejected without counting a failed attempt, so another client cannot lock a learner's code |

Start and verify take a per-address PostgreSQL advisory lock, so concurrent
requests cannot bypass the throttle or create two accounts for one address.
Failed-attempt counts are committed before the rejection is returned.

Addresses are trimmed and lower-cased; ASCII only (no IDN yet). Provider aliases
(dots, `+tag`) are **not** folded: each is a separate identity. Responses do not
reveal whether an address already has an account; a 429 does reveal that a code
was requested for that address recently.

## Browser boundary

The two login paths are sessionless, so no CSRF token can exist. The session
boundary middleware still requires HTTPS on the configured host, an exact
`Origin` header and no cross-site `Sec-Fetch-Site`; responses are `no-store`.
The frontend shows the email form only when the server reports the provider as
configured, then confirms the HttpOnly cookie through `/api/account/me`.

## Database and privileges (migration 0007)

- `cloud_external_identities(provider, subject)` → `owner_user_id`; provider is
  constrained to `email` until another verifier ships. Subjects must already be
  normalized (DB check).
- `cloud_email_login_challenges` holds the address, code digest, expiry, failed
  attempts and `closed_at`.

The auth role from P8-05A additionally needs:

```sql
GRANT SELECT, INSERT, UPDATE ON cloud_email_login_challenges, cloud_external_identities TO <auth_role>;
GRANT INSERT ON cloud_users TO <auth_role>;
```

This widens the P8-05A ADR, which kept account provisioning out of the auth
role: verified email login is now the approved provisioner. The role still has
no access to learning tables (tested with a NOSUPERUSER/NOBYPASSRLS role).
Expired/closed challenges are not purged yet; see Remaining work.

## Configuration

Email login is disabled unless explicitly configured. In addition to the P8-05A
`CLOUD_SESSION_DATABASE_URL` and `CLOUD_SESSION_ORIGIN`:

| Variable | Meaning |
| --- | --- |
| `CLOUD_EMAIL_DELIVERY` | unset: disabled. `sandbox-directory`: local staging outbox. Anything else fails closed (503). |
| `CLOUD_EMAIL_SANDBOX_DIR` | absolute directory private to the service user (no group/other bits); each code is written as a 0600 text file |
| `LANGUAGE_AI_STAGING` | must be `1` for `sandbox-directory` |
| `CLOUD_AUTH_CODE_KEY` | required with any delivery: hex, at least 32 random bytes, server-side secret |

`sandbox-directory` is refused unless `LANGUAGE_AI_STAGING=1` and the origin host
is `localhost`, `*.localhost` or `*.test`, so a misconfigured deployment cannot write real
users' codes to disk. Tests use the in-memory `SandboxMailbox`, which no HTTP
route can read.

### Local staging

Run the backend with `LANGUAGE_AI_DEPLOYMENT_MODE=cloud`, a disposable
PostgreSQL with migrations 0001–0007, `CLOUD_SESSION_ORIGIN=https://app.localhost:<port>`
behind a local TLS terminator, `LANGUAGE_AI_STAGING=1`, `CLOUD_EMAIL_DELIVERY=sandbox-directory` and a
scratch `CLOUD_EMAIL_SANDBOX_DIR`. Request a code in the browser and read it from
the newest file in that directory. Use synthetic addresses only.

### Real provider (not authorized yet)

A production adapter must implement `LoginCodeMailer.send_login_code` and needs
an approved provider account and credentials (server-side only), a verified
sending domain with SPF/DKIM/DMARC, a bounce/complaint policy and the
data-processing decision for sending addresses to that provider.

## Remaining work in Workstream A

**P8-05B2 update:** WeChat (simulated), link/unlink, purge, the 24 h lockout,
plain code digests, pre-login revocation and tab sync are addressed in
`docs/P8-05B2-ACCOUNT-IDENTITY.md`. The list below is the P8-05B1 snapshot.

- WeChat OAuth with simulated provider responses; account link/unlink with
  fresh proof for both identities.
- Per-client-IP and global send/guess limits (needs the trusted-proxy decision
  from Workstream F). Until then two risks stay open: (a) code spraying across
  many addresses is bounded only per address (10 guesses/day against 10^6
  codes); (b) anyone can exhaust one address's hourly code quota, delaying that
  learner's sign-in, and fill their inbox; (c) **accepted for now, owner
  decision:** anyone can spend an address's 10 daily failed attempts with their
  own codes and block new sign-ins for that address for up to 24 hours
  (existing sessions keep working). This is the price of bounding brute force
  per address; per-IP limits are the real fix.
- A pre-login session whose revocation hits a storage error after sign-in stays
  valid server-side until its idle/absolute expiry (the browser cookie is replaced).
- Purge of old challenges and staging outbox files.
- Notify other open tabs after sign-in (logout already broadcasts).
- Production mail adapter once credentials are approved.
