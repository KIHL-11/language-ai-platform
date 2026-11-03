# P8-05B3: adding and changing verified email addresses

Status: implemented with sandbox mail only; not production approved. Builds on P8-05B1/B2
(`docs/P8-05B-EMAIL-LOGIN.md`, `docs/P8-05B2-ACCOUNT-IDENTITY.md`). Master
tracker: Issue #75, Workstream A. Owner decisions in force: WeChat never
creates an account; no email may be removed until add/change/recovery is
complete and approved; no real mail or WeChat; email throttles are an interim
test-stage measure only, not a production approval.

## Model

- An account has **one or two** verified email identities. The oldest is the
  *primary* address shown as "Signed in as" and used for notices; both can sign
  in and both can be used for the fresh-proof re-check. A second address is the
  recovery address the later recovery flow will build on.
- Email identities can be **added** (while fewer than two) or **changed**
  (replace one address with another). There is still **no removal**; changing
  never reduces the number of sign-in methods.
- An address belongs to at most one account (`PK(provider,subject)`), so it can
  never be added to a second account or moved between accounts.

## Flow

1. Fresh proof: the session must hold an email proof from the last 10 minutes
   (P8-05B2 `reauthenticated_at`; email login or the re-check code). The
   re-check can now be sent to either of the account's own addresses.
2. `POST /api/account/email/claim/start {email, replaces, confirm}` (session +
   CSRF + fresh proof). `replaces` is `null` to add, or one of the account's
   own addresses to change it. `confirm` must be `add-email` / `change-email`
   (the UI requires a ticked checkbox). The server normalizes the new address,
   refuses the account's own addresses and a third address, and sends a code to
   the **new** address. It does not reveal at this step whether the address
   belongs to another account; the response is the same either way.
3. `POST /api/account/email/claim/verify {challenge_id, code}` (session + CSRF +
   fresh proof still valid). The code proves control of the new mailbox. Under
   the new address's lock and the account lock, in one transaction:
   - address already mapped to **another** account → `409 email_in_use`
     (safe to say now: the caller controls that mailbox); nothing changes;
   - add: insert the identity, unless the account now has two;
   - change: replace the old subject with the new one (one `UPDATE`), only if the
     old address still belongs to this account;
   - change also **revokes every other browser session** of the account and
     closes **every open code sent to the replaced address** (login codes from
     any browser included); the current session stays and is marked freshly
     proven.
4. After commit, a security notice goes to the replaced address (on change) or
   the primary address (on add). The response says `notice: "sent" | "failed"
   | "unavailable"`; anything but `sent` is logged as a warning and the UI shows
   an alert telling the learner the other address was **not** told. The change
   itself is not undone: the learner just proved control of the new address,
   and rolling back would not protect anyone. Risk: until a real mail adapter
   with retries exists, a failed notice means the old address's owner may not
   learn about the change. A durable notice outbox is listed below.

After a change the old address cannot get back into the account: its codes are
closed, its sessions revoked, and a new code for it maps to nothing, so a
login with it creates a separate, empty account (it is not reserved; the
address may belong to someone else now).

Challenge rows: purpose `claim`, `owner_user_id`, `replaces_subject`, bound to
the requesting session like re-check codes, sharing the per-address throttles
(60 s resend, 5/hour, daily failure cap with long codes).

## Failure and concurrency rules

- Every step is one transaction; any storage error rolls back the code
  consumption, the identity change and the session revocation together.
- Code consumption takes the new address's advisory lock (shared with first
  login), then the account lock: a first login and a claim of the same address
  serialize, and exactly one wins (`PK` as backstop, mapped to 409).
- Two concurrent claims on one account cannot exceed two addresses or replace
  the same address twice.
- Codes are bound to the session that requested them; another session, another
  account, a logout or a rotation makes the code unusable.

## Migration 0009 and rollback

Up: partial unique index for one WeChat per account, owner index, `claim`
purpose and `replaces_subject`. Down **refuses** (raises, changes nothing)
while any account holds two addresses: the 0008 schema allows one, and only
the learner can say which address to keep. With one address per account
(including changed ones) the rollback is lossless: every identity and every
code row is kept, open add/change codes are closed and relabelled so their
failed attempts still count. Code deployed for 0009 must be rolled back
together with the schema. 0008's rollback likewise refuses while WeChat
identities exist. Tests compare full before/after snapshots.

## Maintenance (task C)

`python -m services.auth.maintenance` now also purges browser sessions that
can never authenticate again (revoked, past absolute expiry, or idle) for more
than a day. Active sessions are untouched (tested at the exact boundary).

Run it as a dedicated role, not the API role:

```sql
CREATE ROLE language_ai_auth_maintenance LOGIN NOSUPERUSER NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO language_ai_auth_maintenance;
GRANT SELECT, DELETE ON cloud_browser_sessions, cloud_email_login_challenges, cloud_oauth_states
  TO language_ai_auth_maintenance;
```

It cannot read learning tables, update sessions or create users (tested). No
row pre-locking is used, because `FOR UPDATE` would need UPDATE.

Operations:

- **Schedule:** hourly (cron/systemd timer/K8s CronJob), `--batch 500
  --max-batches 20`. Exit 0 = done, 1 = storage error (committed batches kept;
  rerun), 2 = another run holds the lock (skip).
- **Concurrency:** a session-level advisory lock allows one run at a time.
- **Safety:** each batch is its own transaction with `lock_timeout 2s` and
  `statement_timeout 30s`; a timed-out batch rolls back alone.
- **Recovery:** idempotent; after a crash just run again. There is nothing to
  roll back: purged rows are, by construction, ones no sign-in path can use
  (codes past the 25 h throttle window, states expired > 1 h, dead sessions
  > 1 day). Restoring them would come only from a database backup.
- **Outbox** (`--outbox`, staging only): unchanged from P8-05B2.

## Out of scope (next)

Removing an address, choosing which address is primary, account recovery
without any working address, a durable retried notice outbox, choosing the
re-check address in the UI (the API accepts `email`), and real mail delivery.
Primary is the oldest address; identical creation timestamps fall back to
alphabetical order.
