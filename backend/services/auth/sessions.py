"""Provider-neutral browser sessions. No public credential-minting path."""
import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from services.auth.identity import AuthenticatedPrincipal, InvalidAuthentication


COOKIE_NAME = '__Host-language_ai_session'
TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')
ABSOLUTE_LIFETIME = timedelta(hours=12)
IDLE_LIFETIME = timedelta(minutes=30)
# How long a fresh email proof (login or explicit re-check) unlocks linking and
# unlinking sign-in methods. Rotation does not carry it over.
REAUTH_LIFETIME = timedelta(minutes=10)


class SessionUnavailable(RuntimeError):
    pass


def digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def csrf_token(token):
    return hashlib.sha256(('language-ai-csrf-v1:' + token).encode('ascii')).hexdigest()


@dataclass(frozen=True)
class SessionPolicy:
    origin: str

    def __post_init__(self):
        parsed = urlsplit(self.origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or self.origin != f'https://{parsed.netloc}'):
            raise ValueError('Cloud session origin must be an exact HTTPS origin')


class BrowserSessionRepository:
    """Trusted auth service repository; separate least-privilege DB role.

    Auth lookup cannot use tenant RLS: it establishes the owner from a random
    credential. This role must not be granted access to learning tables.
    """
    def __init__(self, dsn, clock=lambda: datetime.now(timezone.utc)):
        self.dsn, self.clock = dsn, clock

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    def _now(self):
        now=self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise SessionUnavailable('Session clock must be timezone-aware')
        return now.astimezone(timezone.utc)

    def issue_for_verified_account(self, owner):
        """Trusted caller only, after verified provider proof/account mapping.

        Never exposed by HTTP; requires an existing server-provisioned account.
        Never accepts a browser learner ID as proof and never inserts cloud_users.
        """
        try:
            with self._connect() as connection:
                return self.issue_in_transaction(connection,owner,self._now())
        except psycopg.Error as exc: raise SessionUnavailable('Session storage unavailable') from exc

    def issue_in_transaction(self, connection, owner, now, *, reauthenticated=False, replaces=''):
        """Issue inside a trusted caller's transaction (verified login mapping).

        ``replaces`` is the browser's previous session token, revoked in the same
        transaction so a pre-login session can never outlive the login.
        """
        if isinstance(replaces,str) and TOKEN_PATTERN.fullmatch(replaces):
            connection.execute('UPDATE cloud_browser_sessions SET revoked_at=%s WHERE token_hash=%s AND revoked_at IS NULL',(now,digest(replaces)))
        token=secrets.token_urlsafe(32)
        session_id=str(uuid.uuid4())
        connection.execute('INSERT INTO cloud_browser_sessions(session_id,owner_user_id,token_hash,created_at,expires_at,last_seen_at) VALUES(%s,%s,%s,%s,%s,%s)',(session_id,owner,digest(token),now,now+ABSOLUTE_LIFETIME,now))
        if reauthenticated:
            connection.execute('UPDATE cloud_browser_sessions SET reauthenticated_at=%s WHERE session_id=%s',(now,session_id))
        return token

    def session_in_transaction(self, connection, token):
        """Lock and return the valid session row for ``token`` plus the current time."""
        return self._row(connection,token)

    def owner_hint(self, connection, token):
        """Unlocked owner lookup used only to pick an account lock; validate afterwards."""
        if not isinstance(token,str) or not TOKEN_PATTERN.fullmatch(token):
            raise InvalidAuthentication('Invalid session')
        row=connection.execute('SELECT owner_user_id FROM cloud_browser_sessions WHERE token_hash=%s',(digest(token),)).fetchone()
        if row is None: raise InvalidAuthentication('Invalid session')
        return row['owner_user_id']

    def revoke_others_in_transaction(self, connection, owner, keep_session_id, now):
        connection.execute('UPDATE cloud_browser_sessions SET revoked_at=%s WHERE owner_user_id=%s AND session_id<>%s AND revoked_at IS NULL',(now,owner,keep_session_id))

    def _row(self, connection, token):
        if not isinstance(token,str) or not TOKEN_PATTERN.fullmatch(token):
            raise InvalidAuthentication('Invalid session')
        row=connection.execute('SELECT * FROM cloud_browser_sessions WHERE token_hash=%s FOR UPDATE',(digest(token),)).fetchone()
        now=self._now()
        if row is None or row['revoked_at'] is not None or row['expires_at'] <= now or row['last_seen_at']+IDLE_LIFETIME <= now:
            raise InvalidAuthentication('Invalid session')
        return row,now

    def verify(self, token):
        try:
            with self._connect() as connection:
                row,now=self._row(connection,token)
                connection.execute('UPDATE cloud_browser_sessions SET last_seen_at=%s WHERE session_id=%s',(now,row['session_id']))
                return AuthenticatedPrincipal(user_id=row['owner_user_id'],session_id=str(row['session_id']),authentication_method='opaque-browser-session')
        except psycopg.Error as exc: raise SessionUnavailable('Session storage unavailable') from exc

    def rotate(self, token):
        replacement=secrets.token_urlsafe(32)
        try:
            with self._connect() as connection:
                row,now=self._row(connection,token)
                connection.execute('UPDATE cloud_browser_sessions SET revoked_at=%s WHERE session_id=%s',(now,row['session_id']))
                connection.execute('INSERT INTO cloud_browser_sessions VALUES(%s,%s,%s,%s,%s,%s,NULL)',(str(uuid.uuid4()),row['owner_user_id'],digest(replacement),now,row['expires_at'],now))
        except psycopg.Error as exc: raise SessionUnavailable('Session storage unavailable') from exc
        return replacement

    def revoke(self, token):
        try:
            with self._connect() as connection:
                row,now=self._row(connection,token)
                connection.execute('UPDATE cloud_browser_sessions SET revoked_at=%s WHERE session_id=%s',(now,row['session_id']))
        except psycopg.Error as exc: raise SessionUnavailable('Session storage unavailable') from exc

    def revoke_all(self, owner):
        """Trusted account-security operation, never a client-selected owner."""
        try:
            with self._connect() as connection:
                connection.execute('UPDATE cloud_browser_sessions SET revoked_at=%s WHERE owner_user_id=%s AND revoked_at IS NULL',(self._now(),owner))
        except psycopg.Error as exc: raise SessionUnavailable('Session storage unavailable') from exc


class BrowserSessionVerifier:
    def __init__(self, repository): self.repository=repository
    def __call__(self,request):
        return self.repository.verify(request.cookies.get(COOKIE_NAME,''))
