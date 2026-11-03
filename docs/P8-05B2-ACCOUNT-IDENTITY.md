# P8-05B2: WeChat sign-in (simulated), account linking and auth upkeep

Status: implemented and tested against a **fully simulated** WeChat provider
only. No WeChat App ID, secret or callback domain exists, nothing calls
WeChat, and nothing here is production approved. Builds on P8-05B1
(`docs/P8-05B-EMAIL-LOGIN.md`). Master tracker: Issue #75, Workstream A.

## Product decisions in this stage (owner may revise)

- **WeChat never creates an account.** An unlinked WeChat identity gets
  `409 wechat_not_linked` and the UI says to sign in with email and link
  WeChat. Every account therefore keeps a recoverable email identity.
- **One WeChat per account, one account per WeChat.** A WeChat identity is
  never moved between accounts; a conflict changes nothing.
- **Email cannot be unlinked yet** (`unlink_unsupported`): it is the recovery
  identity until adding/changing an email address ships. The generic
  last-sign-in-method guard is still enforced and tested.
- **Unlinking WeChat signs out every other browser** of the account.

## Protocol and checks

WeChat website login is plain OAuth 2.0 with a confidential client. It has no
ID token (so **no nonce**) and no **PKCE**; the code can only be redeemed with
the server-side secret. The checks that apply:

| Check | How |
| --- | --- |
| state | 256-bit random, stored only as a digest in `cloud_oauth_states`, 10 min, single use (consumed before the code exchange, even if it later fails) |
| CSRF / login CSRF | state is bound to the browser's `__Host-language_ai_login` HttpOnly cookie; a callback carrying another browser's state is rejected before the code reaches the provider and does not burn the victim's state |
| link binding | a `link` state is also bound to the signed-in session id and owner; finishing in another session, after logout or after rotation is rejected |
| issuer / app id | the verifier's assertion must report `https://open.weixin.qq.com` and this deployment's app id; anything else is rejected |
| subject | openid must match `^[A-Za-z0-9_-]{6,64}$`; stored as `<app id>:<openid>` (DB check), so a different WeChat application can never collide |
| callback replay | state single use + provider code single use; both tested independently |
| fresh proof | starting a link, finishing it and unlinking require an email proof from the last 10 minutes (`reauthenticated_at` on the session: set by email login or by the session-bound re-check code) |
| explicit confirmation | the UI requires an unchecked checkbox to be ticked before it sends the request. The server-side `confirm: "link-wechat"` / `"unlink-wechat"` constants only prove a deliberate, CSRF-protected call (not a replayed generic POST); they are not proof of a human decision by themselves |
| concurrency | per-account and per-subject advisory locks, `PK(provider,subject)` and `UNIQUE(owner_user_id,provider)`; four concurrent links of one WeChat yield exactly one. WeChat login, link and unlink all take the account lock before touching session rows, so a login racing an unlink gets no session (tested) and link/unlink cannot deadlock on lock order |

The callback lands on the SPA root (`/?oauth=wechat&code&state`). The page
removes the code from the address bar, then finishes with a same-origin
`POST /api/account/wechat/complete`, so the session boundary never accepts a
cross-site request and `SameSite=Strict` cookies keep working.

Residual protocol risk: without PKCE, a stolen still-valid WeChat code could be
redeemed by an attacker who also holds a matching state from their own browser.
It would sign the attacker in as the victim only if the victim's WeChat is
linked. WeChat codes are short lived and single use; this matches WeChat's
documented flow and cannot be closed on our side.

## Routes

| Route | Auth | Purpose |
| --- | --- | --- |
| `POST /api/account/wechat/start` | sessionless, exact Origin | returns `authorization_url`; sets the login cookie |
| `POST /api/account/wechat/complete {code,state}` | sessionless + state binding | `{purpose:"login",user_id,csrf_token}` or `{purpose:"link"}` |
| `POST /api/account/reauth/email/start` | session + CSRF | sends a re-check code to the account's own address (no address input) |
| `POST /api/account/reauth/email/verify {challenge_id,code}` | session + CSRF | marks this session freshly proven for 10 min |
| `POST /api/account/wechat/link/start {confirm}` | session + CSRF + fresh proof | returns `authorization_url` |
| `GET /api/account/identities` | session | linked methods (WeChat openid is never shown) and remaining proof time |
| `POST /api/account/identities/unlink {provider,confirm}` | session + CSRF + fresh proof | 204; 422 `last_login_method` / `unlink_unsupported` / `not_linked` |

Errors: 428 `reauthentication_required`, 409 `identity_conflict` /
`provider_already_linked` / `wechat_not_linked`, 400 `wechat_callback_invalid`.

## Adapters and isolation of the simulation

- `WeChatVerifier` protocol: `authorization_url(state, redirect_uri)` and
  `exchange(code, redirect_uri) -> ExternalAssertion(issuer, app_id, openid)`.
- `WeChatOAuthAdapter`: the real request/response shape (qrconnect URL,
  `sns/oauth2/access_token`, `errcode`, `snsapi_login` scope). Its HTTP
  transport is injected and **nothing wires it up**; tests use a fake
  transport with no network.
- `SimulatedWeChatProvider` lives in `backend/tests/`, not in `services/`. A
  test asserts no service module imports it. It can only be installed through
  the trusted `configure_wechat_login` hook from Python; no environment
  variable or HTTP route can install a verifier.
- Any `CLOUD_WECHAT_*` variable makes cloud bootstrap fail closed (503) until a
  real adapter is authorized.

Enabling real WeChat later needs: an approved open-platform website app (App
ID, secret held server side), the registered callback domain equal to
`CLOUD_SESSION_ORIGIN`, an HTTPS transport with timeouts and certificate
checks, a decision on `unionid`, and owner sign-off.

## Account-security fixes to P8-05B1

| P8-05B1 risk | Now |
| --- | --- |
| Anyone could block new sign-ins for an address for ~24 h by spending its 10 daily failures | **Fixed.** At the cap, new codes are 12 characters from a 30-letter alphabet (~59 bits) and are not subject to the daily cap; six-digit codes stay refused. Worst case left: the hourly send quota (5/hour) delays a learner by up to an hour — see Workstream F. |
| Six-digit codes stored as plain SHA-256 (DB copy → codes in 10^6 tries) | **Fixed.** HMAC-SHA256 under `CLOUD_AUTH_CODE_KEY` (hex, ≥ 32 bytes), which is never in the database. Required whenever email delivery is configured. Codes issued before the upgrade stop verifying (10 min impact). |
| Pre-login session revocation failure left that session valid | **Fixed.** Revoked inside the login transaction; if that fails no session is issued and the code stays usable. Same for WeChat login. |
| Other tabs not told about sign-in | **Fixed.** `signed-in` on the existing BroadcastChannel; other tabs re-check `/api/account/me`. |
| No per-IP / global limits | **Open — Workstream F dependency** (below). |

## Known limits found in review

Second review (post-fix, `a4469c8`). Fixed in the follow-up commit:

- **Medium, fixed:** a session rotating at the same moment could outlive unlink's or
  revoke-all's sign-out. Rotation and revoke-all now take the account lock
  first, like login, link and unlink (tests prove rotation waits, and concurrent
  rotate + unlink leaves only the unlinking browser signed in).
- **Low, fixed:** an email re-login in the same browser during an unlink could
  deadlock. The previous session is now revoked before identity rows are touched.
- **Low, fixed:** the re-check form could get stuck after a code expired or was
  used up; it now offers "Send a new code" and drops expired pending codes.

Still open:

- **Medium, accepted as interim (owner decision 5), not a production approval:**
  the 60 s / 5-per-hour send limits are per address across all browsers, so
  anyone who knows an address can block new email sign-ins for it (about 1 h
  at a time, renewable) and flood the mailbox. Real fix: per-client limits
  (Workstream F) and, independently, a remembered-device cookie, registered on
  successful sign-in, that gets its own quota.
- **Low:** no global budget for short-code guesses across many addresses (about
  10 guesses/day/address). Needs Workstream F.
- **Low:** rolling back 0008 does not revoke sessions created by WeChat login
  (they stay valid until expiry, at most 12 h), and deleting re-check/long-code
  rows partly resets the daily failure count.
- **Low:** if the browser's previous session is rotated during a new login,
  the rotated copy is not revoked; it belongs to that browser's earlier sign-in.

- `POST /api/account/wechat/start` has no rate limit; each call stores one
  10-minute state row, bounded only by the purge job. A per-IP limit belongs to
  the Workstream F dependency below.
- Rotating `CLOUD_AUTH_CODE_KEY` invalidates codes issued in the last 10
  minutes (learners request a new code). No dual-key verification exists.
- Rolling back 0008 leaves open six-digit codes stored as HMAC digests, which
  0007 code cannot check: about 10 minutes of failed sign-ins after a rollback.
- The browser accepts any `https:` authorization URL from the same-origin
  server. When a real adapter is wired, pin the host to `open.weixin.qq.com`.

## Purge

`python -m services.auth.maintenance [--batch 500] [--max-batches 20] [--outbox]`
with `CLOUD_SESSION_DATABASE_URL` (and `CLOUD_EMAIL_SANDBOX_DIR` for
`--outbox`, staging only). Each batch is its own short transaction with
`FOR UPDATE SKIP LOCKED`; a run deletes at most `batch × max-batches` rows per
table, so a backlog drains over several runs.

- Login challenges older than 25 h (the rolling failed-attempt day + margin).
- OAuth states expired more than 1 h ago.
- Outbox: only regular files with the adapter's exact name pattern, owned by
  the service user, whose code expired over 1 h ago; symlinks and other files
  are never touched, and a non-private directory is refused.

Suggested schedule: hourly. Browser session rows are not purged yet.

## Database (migration 0008) and grants

Adds `wechat` to the provider check with a subject check, `UNIQUE(owner,provider)`,
challenge `purpose`/`owner_user_id`/`code_format`, session `reauthenticated_at`
and `cloud_oauth_states`. Rollback deletes WeChat identities, re-check and long
codes; email accounts remain. Additional grants for the auth role:

```sql
GRANT SELECT, INSERT, UPDATE ON cloud_oauth_states TO <auth_role>;
GRANT DELETE ON cloud_external_identities TO <auth_role>;              -- unlink
GRANT DELETE ON cloud_email_login_challenges, cloud_oauth_states TO <auth_role>;  -- purge job
```

The role still has no access to learning tables (tested).

## Workstream F design dependency: trusted client IP and global limits

Not implemented on purpose: limiting by `X-Forwarded-For` without a trusted
proxy definition lets any client choose its own IP. Needed from Workstream F:

0. Also covers `POST /api/account/wechat/start` (unlimited today) and the
   email code send quota.
1. The production topology: which reverse proxy/CDN terminates TLS, and its
   egress addresses or a private network boundary.
2. A server setting such as `CLOUD_TRUSTED_PROXIES` (CIDRs); the client IP is
   the right-most address in `X-Forwarded-For` not in that set, or the socket
   peer when the request did not come from a trusted proxy. Uvicorn
   `--forwarded-allow-ips` must match and must not be `*`.
3. Buckets: per IP (IPv6 per /64) for code sends and failed verifications;
   per IP × address; a global ceiling on sends per minute that alerts rather
   than blocks learners already holding a code.
4. Storage: PostgreSQL counters are enough at current scale; a shared store is
   needed only with several API instances under load.
5. Policy for 429s that does not reveal whether an address has an account.

Until then: code spraying across many addresses is bounded per address only,
and anyone can use an address's hourly send quota (delaying that learner up to
an hour and sending them unwanted emails).
