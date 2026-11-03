"""Verified email one-time-code login (P8-05B).

The browser proves control of a mailbox by returning a six-digit code that was
delivered through a trusted mail adapter. Only then is the normalized address
mapped to an internal account and a browser session issued, in one transaction.
No HTTP path accepts a user id, and no adapter here sends real email.

Codes are stored as HMAC digests under a server-side key that is never in the
database, so a database copy alone does not let anyone recover open codes.
"""
import hashlib
import hmac
import logging
import stat
import os
import re
import secrets
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import psycopg

from services.auth.account import IdentityConflict, account_lock, require_fresh_proof
from services.auth.identity import InvalidAuthentication
from services.auth.sessions import REAUTH_LIFETIME, BrowserSessionRepository, SessionUnavailable


LOG = logging.getLogger(__name__)
CODE_LIFETIME = timedelta(minutes=10)
RESEND_INTERVAL = timedelta(seconds=60)
HOURLY_CHALLENGE_LIMIT = 5
MAX_FAILED_ATTEMPTS = 5
DAILY_FAILED_ATTEMPT_LIMIT = 10
LOGIN_COOKIE_NAME = '__Host-language_ai_login'
BINDING_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')
EMAIL_PATTERN = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
CODE_PATTERN = re.compile(r'^[0-9]{6}$')
# Issued instead of six digits once an address reached its daily failed-attempt
# cap: about 59 bits, so the cap no longer has to lock the learner out.
LONG_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTVWXYZ23456789'
LONG_CODE_LENGTH = 12
LONG_CODE_PATTERN = re.compile(f'^[{LONG_CODE_ALPHABET}]{{{LONG_CODE_LENGTH}}}$')
MIN_CODE_KEY_BYTES = 32
MAX_EMAILS_PER_ACCOUNT = 2
CLAIM_CONFIRMATION = {None: 'add-email', 'change': 'change-email'}
OUTBOX_NAME = re.compile(r'^(\d{8}T\d{6}Z)-[0-9a-f]{32}\.txt$')


class EmailLoginRejected(RuntimeError):
    """Invalid, expired, consumed or locked challenge. Deliberately unspecific."""


class EmailLoginThrottled(RuntimeError):
    def __init__(self, retry_after):
        super().__init__('Too many login codes requested')
        self.retry_after = retry_after


class InvalidEmailAddress(ValueError):
    pass


def normalize_email(value):
    """Return the identity subject for an address, or raise InvalidEmailAddress.

    Lower-cases the whole address and accepts ASCII only (no IDN yet). Provider
    specific aliasing (dots, plus tags) is not folded: each is its own subject.
    """
    if not isinstance(value, str):
        raise InvalidEmailAddress('Email address is invalid')
    email = unicodedata.normalize('NFC', value).strip()
    if len(email) > 254 or not email.isascii():
        raise InvalidEmailAddress('Email address is invalid')
    email = email.lower()
    if not EMAIL_PATTERN.fullmatch(email):
        raise InvalidEmailAddress('Email address is invalid')
    return email


def new_client_binding():
    """Random pre-auth secret held in an HttpOnly cookie by the requesting browser."""
    return secrets.token_urlsafe(32)


def _binding_digest(binding):
    if not isinstance(binding, str) or not BINDING_PATTERN.fullmatch(binding):
        raise EmailLoginRejected('Invalid code')
    return hashlib.sha256(f'language-ai-login-binding-v1:{binding}'.encode('ascii')).hexdigest()


def _session_binding(token):
    if not isinstance(token, str) or not BINDING_PATTERN.fullmatch(token):
        raise EmailLoginRejected('Invalid code')
    return hashlib.sha256(f'language-ai-reauth-binding-v1:{token}'.encode('ascii')).hexdigest()


def code_digest(key, challenge_id, code):
    return hmac.new(key, f'language-ai-email-code-v2:{challenge_id}:{code}'.encode('ascii'), hashlib.sha256).hexdigest()


def _new_code(long):
    if long:
        return ''.join(secrets.choice(LONG_CODE_ALPHABET) for _ in range(LONG_CODE_LENGTH))
    return f'{secrets.randbelow(1_000_000):06d}'


def normalize_code(value):
    """Six digits, or a long code typed with any case, spaces or dashes; None if neither."""
    if not isinstance(value, str) or len(value) > 32:
        return None
    compact = value.replace('-', '').replace(' ', '').upper()
    return compact if CODE_PATTERN.fullmatch(compact) or LONG_CODE_PATTERN.fullmatch(compact) else None


def format_code(code):
    return code if len(code) == 6 else '-'.join(code[i:i + 4] for i in range(0, len(code), 4))


class LoginCodeMailer(Protocol):
    def send_login_code(self, *, email: str, code: str, expires_at: datetime) -> None: ...


@dataclass(frozen=True)
class SandboxMessage:
    email: str
    code: str
    expires_at: datetime


class SandboxMailbox:
    """In-memory mailbox for isolated tests. Never reachable over HTTP."""

    def __init__(self):
        self._lock = threading.Lock()
        self.messages: list[SandboxMessage] = []
        self.notices: list[tuple[str, str]] = []

    def send_login_code(self, *, email, code, expires_at):
        with self._lock:
            self.messages.append(SandboxMessage(email, code, expires_at))

    def send_security_notice(self, *, email, kind):
        with self._lock:
            self.notices.append((email, kind))

    def latest(self, email):
        with self._lock:
            return next(m for m in reversed(self.messages) if m.email == email)


class DirectoryMailbox:
    """Local staging outbox: one owner-only file per message, nothing is sent."""

    def __init__(self, directory):
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            raise ValueError('Sandbox mail directory must be absolute')

    def send_login_code(self, *, email, code, expires_at):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        status = self.directory.stat()
        if status.st_mode & 0o077 or status.st_uid != os.getuid():
            raise OSError('Sandbox mail directory must be private to the service user')
        target = self.directory / f'{expires_at.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}.txt'
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='ascii') as handle:
            handle.write(f'To: {email}\nSubject: Language AI sign-in code\n\nCode: {format_code(code)}\n'
                         f'Expires: {expires_at.isoformat()}\n')

    def send_security_notice(self, *, email, kind):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        status = self.directory.stat()
        if status.st_mode & 0o077 or status.st_uid != os.getuid():
            raise OSError('Sandbox mail directory must be private to the service user')
        stamp = datetime.now(timezone.utc)
        descriptor = os.open(self.directory / f'{stamp:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='ascii') as handle:
            handle.write(f'To: {email}\nSubject: Language AI account security notice\n\nEvent: {kind}\n'
                         'If this was not you, sign in and sign out everywhere, then contact support.\n')

    def purge(self, now, *, grace=timedelta(hours=1), limit=1000):
        """Delete at most ``limit`` outbox files whose code expired before ``now - grace``.

        Touches only regular files this adapter could have written (exact name
        pattern, owned by the service user); never follows symlinks.
        """
        if not self.directory.exists():
            return 0
        status = self.directory.lstat()
        if not stat.S_ISDIR(status.st_mode) or status.st_mode & 0o077 or status.st_uid != os.getuid():
            raise OSError('Sandbox mail directory must be private to the service user')
        cutoff, removed = now - grace, 0
        for entry in os.scandir(self.directory):
            if removed >= limit:
                break
            match = OUTBOX_NAME.fullmatch(entry.name)
            if match is None or not entry.is_file(follow_symlinks=False):
                continue
            expires = datetime.strptime(match.group(1), '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            if expires < cutoff and entry.stat(follow_symlinks=False).st_uid == os.getuid():
                os.unlink(entry.path)
                removed += 1
        return removed


def _email_lock(connection, email):
    # Serializes throttle checks and first-login account creation per address.
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('language-ai-email:' || %s, 0))", (email,))


def _daily_cap_reset(connection, email, now):
    """When the address falls back under the daily failed-attempt cap; None if it is under it now."""
    rows = connection.execute(
        'SELECT created_at, failed_attempts FROM cloud_email_login_challenges WHERE email=%s AND created_at > %s '
        'AND failed_attempts > 0 ORDER BY created_at', (email, now - timedelta(days=1))).fetchall()
    total = sum(row['failed_attempts'] for row in rows)
    for row in rows:
        if total < DAILY_FAILED_ATTEMPT_LIMIT:
            break
        total -= row['failed_attempts']  # oldest failures leave the rolling window first
        if total < DAILY_FAILED_ATTEMPT_LIMIT:
            return row['created_at'] + timedelta(days=1)
    return None


@dataclass(frozen=True)
class EmailChallenge:
    challenge_id: str
    expires_at: datetime
    resend_after: datetime


@dataclass(frozen=True)
class VerifiedLogin:
    owner_user_id: str
    session_token: str
    created_account: bool


@dataclass(frozen=True)
class ReauthChallenge:
    challenge_id: str
    expires_at: datetime
    resend_after: datetime
    email: str


class EmailLoginService:
    """Trusted account service; runs on the auth database role, not tenant RLS."""

    def __init__(self, sessions, mailer, *, code_key):
        if (not isinstance(sessions, BrowserSessionRepository) or not callable(getattr(mailer, 'send_login_code', None))
                or not isinstance(code_key, bytes) or len(code_key) < MIN_CODE_KEY_BYTES):
            raise ValueError('Invalid email login configuration')
        self.sessions, self.mailer, self._code_key = sessions, mailer, code_key

    def start(self, raw_email, binding):
        """Issue a code bound to ``binding``; only that browser can redeem it."""
        return self._issue(normalize_email(raw_email), _binding_digest(binding), 'login', None)

    def start_reauth(self, session_token, raw_email=None):
        """Send a re-check code to one of the signed-in account's own addresses only."""
        bound = _session_binding(session_token)
        try:
            with self.sessions._connect() as connection:
                row, _ = self.sessions.session_in_transaction(connection, session_token)
                emails = self._owner_emails(connection, row['owner_user_id'])
        except psycopg.Error as exc:
            raise SessionUnavailable('Login storage unavailable') from exc
        if raw_email is None:
            email = emails[0] if emails else None
        else:
            email = normalize_email(raw_email)
        if email is None or email not in emails:
            raise EmailLoginRejected('No such email identity')
        issued = self._issue(email, bound, 'reauth', row['owner_user_id'])
        return ReauthChallenge(issued.challenge_id, issued.expires_at, issued.resend_after, email)

    def start_claim(self, session_token, raw_email, replaces, confirmation):
        """Send a code to a new address the signed-in account wants to add or switch to.

        Same response whether or not the address belongs to someone else; that
        is only revealed once the caller has proven control of the mailbox.
        """
        bound = _session_binding(session_token)
        email = normalize_email(raw_email)
        replaced = normalize_email(replaces) if replaces is not None else None
        if confirmation != CLAIM_CONFIRMATION['change' if replaced else None]:
            raise IdentityConflict('confirmation_required')
        try:
            with self.sessions._connect() as connection:
                session, now = self.sessions.session_in_transaction(connection, session_token)
                require_fresh_proof(session, now)
                emails = self._owner_emails(connection, session['owner_user_id'])
        except psycopg.Error as exc:
            raise SessionUnavailable('Login storage unavailable') from exc
        if email in emails:
            raise IdentityConflict('email_already_yours')
        if replaced is not None and replaced not in emails:
            raise IdentityConflict('not_linked')
        if replaced is None and len(emails) >= MAX_EMAILS_PER_ACCOUNT:
            raise IdentityConflict('email_limit')
        return self._issue(email, bound, 'claim', session['owner_user_id'], replaced)

    def verify_claim(self, challenge_id, code, session_token):
        """Add or switch to the proven address in one transaction.

        Returns ``(kind, notice)``. The change is committed before the security
        notice is attempted; ``notice`` is 'sent', 'failed' or 'unavailable' and
        a failure is logged, so callers can tell the learner the other address
        was not told.
        """
        bound = _session_binding(session_token)

        def accept(connection, row, now):
            owner = row['owner_user_id']
            account_lock(connection, owner)  # after the address lock, before the session row
            try:
                session, _ = self.sessions.session_in_transaction(connection, session_token)
            except InvalidAuthentication as exc:
                raise EmailLoginRejected('Invalid code') from exc
            if session['owner_user_id'] != owner:
                raise EmailLoginRejected('Invalid code')
            require_fresh_proof(session, now)
            mapped = connection.execute("SELECT owner_user_id FROM cloud_external_identities WHERE provider='email' AND subject=%s",
                                        (row['email'],)).fetchone()
            if mapped is not None:
                raise IdentityConflict('email_already_yours' if mapped['owner_user_id'] == owner else 'email_in_use')
            emails = self._owner_emails(connection, owner)
            replaced = row['replaces_subject']
            if replaced is None:
                if len(emails) >= MAX_EMAILS_PER_ACCOUNT:
                    raise IdentityConflict('email_limit')
                connection.execute("INSERT INTO cloud_external_identities(provider,subject,owner_user_id,verified_at,created_at) "
                                   "VALUES('email',%s,%s,%s,%s)", (row['email'], owner, now, now))
                kind, notice_to = 'added', emails[0]
            else:
                changed = connection.execute(
                    "UPDATE cloud_external_identities SET subject=%s, verified_at=GREATEST(%s,created_at) "
                    "WHERE provider='email' AND subject=%s AND owner_user_id=%s", (row['email'], now, replaced, owner)).rowcount
                if changed != 1:
                    raise IdentityConflict('not_linked')
                # The old mailbox may be the reason for the change: sign out every
                # other browser and void every open code sent to it (login codes
                # from any browser included), so nothing issued before the change
                # can be redeemed after it.
                self.sessions.revoke_others_in_transaction(connection, owner, session['session_id'], now)
                connection.execute('UPDATE cloud_email_login_challenges SET closed_at=%s WHERE email=%s AND closed_at IS NULL',
                                   (now, replaced))
                kind, notice_to = 'changed', replaced
            connection.execute('UPDATE cloud_browser_sessions SET reauthenticated_at=%s WHERE session_id=%s',
                               (now, session['session_id']))
            return kind, notice_to
        kind, notice_to = self._consume(challenge_id, code, bound, 'claim', accept)
        notify = getattr(self.mailer, 'send_security_notice', None)
        if not callable(notify):
            LOG.warning('Security notice not sent (no adapter): email-%s for challenge %s', kind, challenge_id)
            return kind, 'unavailable'
        try:
            notify(email=notice_to, kind=f'email-{kind}')
        except Exception:  # noqa: BLE001  the change is already committed; report, never hide
            LOG.warning('Security notice delivery failed: email-%s for challenge %s', kind, challenge_id, exc_info=True)
            return kind, 'failed'
        return kind, 'sent'

    def _issue(self, email, bound, purpose, owner, replaces=None):
        now = self.sessions._now()
        challenge_id = str(uuid.uuid4())
        try:
            with self.sessions._connect() as connection:
                _email_lock(connection, email)
                recent = connection.execute(
                    'SELECT count(*) AS n, max(created_at) AS latest, min(created_at) AS oldest FROM cloud_email_login_challenges '
                    'WHERE email=%s AND created_at > %s', (email, now - timedelta(hours=1))).fetchone()
                # At the daily guess cap, keep the learner able to sign in with a
                # code nobody can guess instead of refusing new codes for a day.
                code = _new_code(long=_daily_cap_reset(connection, email, now) is not None)
                if recent['latest'] is not None and recent['latest'] + RESEND_INTERVAL > now:
                    throttled = recent['latest'] + RESEND_INTERVAL
                elif recent['n'] >= HOURLY_CHALLENGE_LIMIT:
                    throttled = recent['oldest'] + timedelta(hours=1)
                else:
                    throttled = None
                    connection.execute(
                        'INSERT INTO cloud_email_login_challenges(challenge_id,email,client_binding,code_hash,created_at,expires_at,'
                        'purpose,owner_user_id,code_format,replaces_subject) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (challenge_id, email, bound, code_digest(self._code_key, challenge_id, code), now, now + CODE_LIFETIME,
                         purpose, owner, 'long' if len(code) != 6 else 'short', replaces))
        except psycopg.Error as exc:
            raise SessionUnavailable('Login storage unavailable') from exc
        if throttled is not None:
            raise EmailLoginThrottled(max(1, int((throttled - now).total_seconds()) + 1))
        try:
            self.mailer.send_login_code(email=email, code=code, expires_at=now + CODE_LIFETIME)
        except Exception as exc:
            self._close(challenge_id)
            raise SessionUnavailable('Login code delivery unavailable') from exc
        # Supersede only this browser's earlier codes, and only once the new one
        # is out: other clients cannot invalidate a code a learner already has.
        try:
            with self.sessions._connect() as connection:
                connection.execute('UPDATE cloud_email_login_challenges SET closed_at=%s WHERE email=%s AND client_binding=%s '
                                   'AND challenge_id<>%s AND closed_at IS NULL', (now, email, bound, challenge_id))
        except psycopg.Error:
            pass  # Older codes still expire on their own.
        return EmailChallenge(challenge_id, now + CODE_LIFETIME, now + RESEND_INTERVAL)

    def _close(self, challenge_id):
        try:
            with self.sessions._connect() as connection:
                connection.execute('UPDATE cloud_email_login_challenges SET closed_at=%s WHERE challenge_id=%s AND closed_at IS NULL',
                                   (self.sessions._now(), challenge_id))
        except psycopg.Error:
            pass  # An unsent challenge still expires on its own.

    def verify(self, challenge_id, code, binding, *, replaces=''):
        """Consume the challenge and issue a session for the mapped account.

        ``replaces`` (the browser's previous session token) is revoked in the
        same transaction; if that fails, no session is issued at all.
        """
        bound = _binding_digest(binding)

        def accept(connection, row, now):
            # Revoke the previous session before touching identity rows: unlink
            # locks session rows first, so the opposite order could deadlock.
            self.sessions.revoke_token_in_transaction(connection, replaces, now)
            owner, created = self._map_identity(connection, row['email'], now)
            return VerifiedLogin(owner, self.sessions.issue_in_transaction(connection, owner, now, reauthenticated=True), created)
        return self._consume(challenge_id, code, bound, 'login', accept)

    def verify_reauth(self, challenge_id, code, session_token):
        """Mark the requesting session as having freshly proven its email address."""
        bound = _session_binding(session_token)

        def accept(connection, row, now):
            try:
                session, _ = self.sessions.session_in_transaction(connection, session_token)
            except InvalidAuthentication as exc:
                raise EmailLoginRejected('Invalid code') from exc
            if session['owner_user_id'] != row['owner_user_id'] or row['email'] not in self._owner_emails(connection, row['owner_user_id']):
                raise EmailLoginRejected('Invalid code')
            connection.execute('UPDATE cloud_browser_sessions SET reauthenticated_at=%s WHERE session_id=%s',
                               (now, session['session_id']))
            return now + REAUTH_LIFETIME
        return self._consume(challenge_id, code, bound, 'reauth', accept)

    def _consume(self, challenge_id, code, bound, purpose, accept):
        try:
            challenge_id = str(uuid.UUID(str(challenge_id)))
        except ValueError as exc:
            raise EmailLoginRejected('Invalid code') from exc
        code = normalize_code(code)
        if code is None:
            raise EmailLoginRejected('Invalid code')
        try:
            with self.sessions._connect() as connection:
                found = connection.execute('SELECT email FROM cloud_email_login_challenges WHERE challenge_id=%s',
                                           (challenge_id,)).fetchone()
                if found is None:
                    raise EmailLoginRejected('Invalid code')
                _email_lock(connection, found['email'])
                row = connection.execute('SELECT * FROM cloud_email_login_challenges WHERE challenge_id=%s FOR UPDATE',
                                         (challenge_id,)).fetchone()
                now = self.sessions._now()
                if row['closed_at'] is not None or row['expires_at'] <= now or row['purpose'] != purpose:
                    raise EmailLoginRejected('Invalid code')
                if not secrets.compare_digest(row['client_binding'], bound):
                    raise EmailLoginRejected('Invalid code')  # not counted: another browser cannot lock it
                if row['code_format'] == 'short' and _daily_cap_reset(connection, row['email'], now) is not None:
                    raise EmailLoginRejected('Invalid code')  # cap covers short codes issued before it was reached
                if not secrets.compare_digest(row['code_hash'], code_digest(self._code_key, challenge_id, code)):
                    failed = row['failed_attempts'] + 1
                    connection.execute(
                        'UPDATE cloud_email_login_challenges SET failed_attempts=%s, closed_at=%s WHERE challenge_id=%s',
                        (failed, now if failed >= MAX_FAILED_ATTEMPTS else None, challenge_id))
                    result = None
                else:
                    connection.execute('UPDATE cloud_email_login_challenges SET closed_at=%s WHERE challenge_id=%s',
                                       (now, challenge_id))
                    result = accept(connection, row, now)
        except psycopg.Error as exc:
            raise SessionUnavailable('Login storage unavailable') from exc
        if result is None:  # raised after commit so the failed attempt is counted
            raise EmailLoginRejected('Invalid code')
        return result

    def _map_identity(self, connection, email, now):
        mapped = connection.execute(
            "SELECT owner_user_id FROM cloud_external_identities WHERE provider='email' AND subject=%s", (email,)).fetchone()
        if mapped is not None:
            connection.execute("UPDATE cloud_external_identities SET verified_at=GREATEST(%s,created_at) WHERE provider='email' AND subject=%s",
                               (now, email))
            return mapped['owner_user_id'], False
        owner = f'u_{uuid.uuid4().hex}'
        connection.execute('INSERT INTO cloud_users(user_id,created_at) VALUES(%s,%s)', (owner, now))
        connection.execute(
            "INSERT INTO cloud_external_identities(provider,subject,owner_user_id,verified_at,created_at) VALUES('email',%s,%s,%s,%s)",
            (email, owner, now, now))
        return owner, True

    @staticmethod
    def _owner_emails(connection, owner):
        """The account's addresses, primary (oldest) first."""
        rows = connection.execute("SELECT subject FROM cloud_external_identities WHERE provider='email' AND owner_user_id=%s "
                                  'ORDER BY created_at, subject', (owner,)).fetchall()
        return [row['subject'] for row in rows]

    @classmethod
    def _email_in(cls, connection, owner):
        emails = cls._owner_emails(connection, owner)
        return emails[0] if emails else None

    def email_for(self, owner):
        try:
            with self.sessions._connect() as connection:
                return self._email_in(connection, owner)
        except psycopg.Error as exc:
            raise SessionUnavailable('Login storage unavailable') from exc


def code_key_from_environment():
    """Server-side HMAC key for login codes: CLOUD_AUTH_CODE_KEY, hex, >= 32 bytes."""
    try:
        key = bytes.fromhex(os.getenv('CLOUD_AUTH_CODE_KEY', '').strip())
    except ValueError as exc:
        raise SessionUnavailable('CLOUD_AUTH_CODE_KEY must be hex') from exc
    if len(key) < MIN_CODE_KEY_BYTES:
        raise SessionUnavailable('CLOUD_AUTH_CODE_KEY must hold at least 32 random bytes')
    return key


def mailer_from_environment(origin):
    """Build the configured adapter, or None when email login stays disabled.

    Only the local staging outbox exists. It needs an explicit staging flag and
    refuses non-local origins so a misconfigured deployment cannot silently
    write real users' codes to disk.
    """
    delivery = os.getenv('CLOUD_EMAIL_DELIVERY', '').strip()
    if not delivery:
        return None
    if delivery != 'sandbox-directory':
        raise SessionUnavailable('Unsupported email delivery adapter')
    if os.getenv('LANGUAGE_AI_STAGING', '').strip() != '1':
        raise SessionUnavailable('Sandbox email delivery requires LANGUAGE_AI_STAGING=1')
    host = urlsplit(origin).hostname or ''
    if host != 'localhost' and not host.endswith(('.localhost', '.test')):
        raise SessionUnavailable('Sandbox email delivery is limited to local staging origins')
    directory = os.getenv('CLOUD_EMAIL_SANDBOX_DIR', '').strip()
    if not directory:
        raise SessionUnavailable('Incomplete sandbox email configuration')
    try:
        return DirectoryMailbox(directory)
    except ValueError as exc:
        raise SessionUnavailable('Invalid sandbox email configuration') from exc

