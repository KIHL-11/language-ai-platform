"""WeChat OAuth sign-in and account linking behind a replaceable verifier (P8-05B2).

WeChat website login is plain OAuth 2.0 with a confidential client: no ID
token (so no nonce) and no PKCE. Protection therefore rests on a random state
stored server side as a digest, bound to the requesting browser (and, for
linking, to one freshly re-verified session), single use and short lived; on
the issuer/app id the verifier reports matching this deployment; and on the
provider's single-use authorization code.

No adapter in this module is wired to the environment. The real-protocol
adapter needs an injected HTTP transport and owner-approved credentials; the
simulated provider used by tests lives in the test tree, not here.
"""
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from urllib.parse import urlencode

import psycopg

from services.auth.email_login import BINDING_PATTERN, EmailLoginRejected, _binding_digest
from services.auth.identity import InvalidAuthentication
from services.auth.sessions import REAUTH_LIFETIME, BrowserSessionRepository, SessionPolicy, SessionUnavailable


STATE_LIFETIME = timedelta(minutes=10)
WECHAT_ISSUER = 'https://open.weixin.qq.com'
APP_ID_PATTERN = re.compile(r'^wx[0-9a-f]{16}$')
OPENID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{6,64}$')
STATE_PATTERN = BINDING_PATTERN
PROVIDER_CODE_PATTERN = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
LINK_CONFIRMATION = 'link-wechat'
UNLINK_CONFIRMATION = {'wechat': 'unlink-wechat'}


class ExternalLoginRejected(RuntimeError):
    """Invalid, expired, replayed or foreign callback. Deliberately unspecific."""


class IdentityNotLinked(RuntimeError):
    """The verified WeChat subject belongs to no account; no account is created."""


class IdentityConflict(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class ReauthenticationRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalAssertion:
    issuer: str
    app_id: str
    openid: str


class WeChatVerifier(Protocol):
    issuer: str
    app_id: str

    def authorization_url(self, *, state: str, redirect_uri: str) -> str: ...

    def exchange(self, *, code: str, redirect_uri: str) -> ExternalAssertion: ...


class WeChatOAuthAdapter:
    """Real WeChat website-login protocol shape. Not authorized or wired yet.

    ``transport(url, params)`` must return the decoded JSON body; it is injected
    so nothing here can reach api.weixin.qq.com without an explicit decision.
    """

    AUTHORIZE_URL = 'https://open.weixin.qq.com/connect/qrconnect'
    TOKEN_URL = 'https://api.weixin.qq.com/sns/oauth2/access_token'
    issuer = WECHAT_ISSUER

    def __init__(self, app_id, secret, transport):
        if not APP_ID_PATTERN.fullmatch(app_id or '') or not secret or not callable(transport):
            raise ValueError('Invalid WeChat adapter configuration')
        self.app_id, self._secret, self._transport = app_id, secret, transport

    def authorization_url(self, *, state, redirect_uri):
        query = urlencode({'appid': self.app_id, 'redirect_uri': redirect_uri, 'response_type': 'code',
                           'scope': 'snsapi_login', 'state': state})
        return f'{self.AUTHORIZE_URL}?{query}#wechat_redirect'

    def exchange(self, *, code, redirect_uri):
        try:
            body = self._transport(self.TOKEN_URL, {'appid': self.app_id, 'secret': self._secret, 'code': code,
                                                    'grant_type': 'authorization_code'})
        except Exception as exc:
            raise SessionUnavailable('WeChat token exchange unavailable') from exc
        if not isinstance(body, dict) or body.get('errcode') not in (None, 0):
            raise ExternalLoginRejected('WeChat rejected the code')
        openid, scope = body.get('openid'), body.get('scope')
        if (not isinstance(openid, str) or not isinstance(scope, str) or 'snsapi_login' not in scope.split(',')
                or not isinstance(body.get('access_token'), str)):
            raise ExternalLoginRejected('Malformed WeChat token response')
        # WeChat does not echo the app id; the code is only redeemable with our
        # own secret, which is what binds it to this application.
        return ExternalAssertion(self.issuer, self.app_id, openid)


def _elevated(sessions, connection, session_token):
    session, now = sessions.session_in_transaction(connection, session_token)
    _require_fresh_proof(session, now)
    return session, now


def _require_fresh_proof(session, now):
    proven = session.get('reauthenticated_at')
    if proven is None or proven + REAUTH_LIFETIME <= now:
        raise ReauthenticationRequired('Confirm your email address first')


def _state_digest(state):
    return hashlib.sha256(f'language-ai-oauth-state-v1:{state}'.encode('ascii')).hexdigest()


def _lock(connection, key):
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))


@dataclass(frozen=True)
class WeChatLogin:
    owner_user_id: str
    session_token: str


@dataclass(frozen=True)
class LinkedIdentity:
    provider: str
    label: str
    linked_at: object


class WeChatAccountService:
    """Trusted account service on the auth DB role: WeChat login, link, unlink."""

    def __init__(self, sessions, verifier, policy, *, app_id):
        if (not isinstance(sessions, BrowserSessionRepository) or not isinstance(policy, SessionPolicy)
                or not APP_ID_PATTERN.fullmatch(app_id or '') or getattr(verifier, 'app_id', None) != app_id
                or getattr(verifier, 'issuer', None) != WECHAT_ISSUER
                or not callable(getattr(verifier, 'exchange', None)) or not callable(getattr(verifier, 'authorization_url', None))):
            raise ValueError('Invalid WeChat login configuration')
        self.sessions, self.verifier, self.app_id = sessions, verifier, app_id
        # WeChat appends code and state; the SPA root finishes the exchange by a
        # same-origin POST, so the session boundary never sees a cross-site request.
        self.redirect_uri = f'{policy.origin}/?oauth=wechat'

    def _subject(self, assertion):
        if (not isinstance(assertion, ExternalAssertion) or assertion.issuer != WECHAT_ISSUER
                or assertion.app_id != self.app_id or not OPENID_PATTERN.fullmatch(assertion.openid or '')):
            raise ExternalLoginRejected('Assertion is not from this WeChat application')
        return f'{assertion.app_id}:{assertion.openid}'

    def _new_state(self, connection, purpose, binding, now, owner=None, session_id=None):
        state = secrets.token_urlsafe(32)
        connection.execute(
            'INSERT INTO cloud_oauth_states(state_hash,provider,purpose,client_binding,owner_user_id,session_id,created_at,expires_at) '
            "VALUES(%s,'wechat',%s,%s,%s,%s,%s,%s)",
            (_state_digest(state), purpose, _binding_digest(binding), owner, session_id, now, now + STATE_LIFETIME))
        return self.verifier.authorization_url(state=state, redirect_uri=self.redirect_uri)

    def start_login(self, binding):
        try:
            with self.sessions._connect() as connection:
                return self._new_state(connection, 'login', binding, self.sessions._now())
        except psycopg.Error as exc:
            raise SessionUnavailable('Login storage unavailable') from exc

    def start_link(self, session_token, binding, confirmation):
        """Explicitly confirmed, freshly re-verified session only; one WeChat per account."""
        if confirmation != LINK_CONFIRMATION:
            raise IdentityConflict('confirmation_required')
        try:
            with self.sessions._connect() as connection:
                session, now = self._elevated(connection, session_token)
                if self._identity(connection, session['owner_user_id'], 'wechat') is not None:
                    raise IdentityConflict('provider_already_linked')
                return self._new_state(connection, 'link', binding, now, session['owner_user_id'], session['session_id'])
        except psycopg.Error as exc:
            raise SessionUnavailable('Login storage unavailable') from exc

    def _elevated(self, connection, session_token):
        return _elevated(self.sessions, connection, session_token)

    @staticmethod
    def _identity(connection, owner, provider):
        return connection.execute('SELECT subject FROM cloud_external_identities WHERE owner_user_id=%s AND provider=%s',
                                  (owner, provider)).fetchone()

    def _claim_state(self, state, binding):
        """Consume the state (committed even if the exchange later fails)."""
        if not isinstance(state, str) or not STATE_PATTERN.fullmatch(state):
            raise ExternalLoginRejected('Invalid state')
        try:
            bound = _binding_digest(binding)
        except EmailLoginRejected as exc:
            raise ExternalLoginRejected('Invalid state') from exc
        try:
            with self.sessions._connect() as connection:
                row = connection.execute('SELECT * FROM cloud_oauth_states WHERE state_hash=%s FOR UPDATE',
                                         (_state_digest(state),)).fetchone()
                now = self.sessions._now()
                if (row is None or row['consumed_at'] is not None or row['expires_at'] <= now
                        or not secrets.compare_digest(row['client_binding'], bound)):
                    return None
                connection.execute('UPDATE cloud_oauth_states SET consumed_at=%s WHERE state_hash=%s', (now, row['state_hash']))
                return row
        except psycopg.Error as exc:
            raise SessionUnavailable('Login storage unavailable') from exc

    def complete(self, code, state, binding, session_token=''):
        """Finish a login or link callback. Returns WeChatLogin, or None for a link."""
        if not isinstance(code, str) or not PROVIDER_CODE_PATTERN.fullmatch(code):
            raise ExternalLoginRejected('Invalid code')
        row = self._claim_state(state, binding)
        if row is None:
            raise ExternalLoginRejected('Invalid state')
        subject = self._subject(self.verifier.exchange(code=code, redirect_uri=self.redirect_uri))
        try:
            with self.sessions._connect() as connection:
                if row['purpose'] == 'login':
                    return self._login(connection, subject, session_token)
                self._link(connection, row, subject, session_token)
                return None
        except psycopg.errors.UniqueViolation as exc:
            raise IdentityConflict('identity_conflict') from exc
        except psycopg.Error as exc:
            raise SessionUnavailable('Login storage unavailable') from exc

    def _login(self, connection, subject, previous):
        now = self.sessions._now()
        mapped = connection.execute(
            "SELECT owner_user_id FROM cloud_external_identities WHERE provider='wechat' AND subject=%s", (subject,)).fetchone()
        if mapped is None:
            raise IdentityNotLinked('Sign in with email and link WeChat first')
        # Serialize with unlink, then re-read: a session is only issued while
        # the mapping still exists, so unlink's sign-out cannot be outrun.
        _lock(connection, f'language-ai-account:{mapped["owner_user_id"]}')
        mapped = connection.execute(
            "SELECT owner_user_id FROM cloud_external_identities WHERE provider='wechat' AND subject=%s FOR UPDATE", (subject,)).fetchone()
        if mapped is None:
            raise IdentityNotLinked('Sign in with email and link WeChat first')
        connection.execute("UPDATE cloud_external_identities SET verified_at=GREATEST(%s,created_at) WHERE provider='wechat' AND subject=%s",
                           (now, subject))
        # A WeChat login does not count as a fresh email proof.
        token = self.sessions.issue_in_transaction(connection, mapped['owner_user_id'], now, replaces=previous)
        return WeChatLogin(mapped['owner_user_id'], token)

    def _link(self, connection, row, subject, session_token):
        owner = row['owner_user_id']
        _lock(connection, f'language-ai-account:{owner}')  # before any session row, as in unlink
        try:
            session, now = self.sessions.session_in_transaction(connection, session_token)
        except InvalidAuthentication as exc:
            raise ExternalLoginRejected('Link must finish in the session that started it') from exc
        if str(session['session_id']) != str(row['session_id']) or session['owner_user_id'] != owner:
            raise ExternalLoginRejected('Link must finish in the session that started it')
        _require_fresh_proof(session, now)
        _lock(connection, f'language-ai-identity:wechat:{subject}')
        mapped = connection.execute(
            "SELECT owner_user_id FROM cloud_external_identities WHERE provider='wechat' AND subject=%s", (subject,)).fetchone()
        if mapped is not None:
            if mapped['owner_user_id'] != owner:
                raise IdentityConflict('identity_conflict')  # never move a WeChat between accounts
            return
        if self._identity(connection, owner, 'wechat') is not None:
            raise IdentityConflict('provider_already_linked')
        connection.execute(
            "INSERT INTO cloud_external_identities(provider,subject,owner_user_id,verified_at,created_at) VALUES('wechat',%s,%s,%s,%s)",
            (subject, owner, now, now))

    def unlink(self, session_token, provider, confirmation):
        unlink_identity(self.sessions, session_token, provider, confirmation)


def unlink_identity(sessions, session_token, provider, confirmation):
    """Remove a sign-in method; signs out every other browser of the account.

    Independent of the WeChat verifier, so a link can be removed even after
    WeChat sign-in is switched off. The account lock is taken before any
    session row is updated, matching link and login.
    """
    if UNLINK_CONFIRMATION.get(provider) is None:
        raise IdentityConflict('unlink_unsupported')  # email is the recovery identity in this stage
    if confirmation != UNLINK_CONFIRMATION[provider]:
        raise IdentityConflict('confirmation_required')
    try:
        with sessions._connect() as connection:
            owner = sessions.owner_hint(connection, session_token)
            _lock(connection, f'language-ai-account:{owner}')
            session, now = _elevated(sessions, connection, session_token)
            rows = connection.execute('SELECT provider FROM cloud_external_identities WHERE owner_user_id=%s FOR UPDATE',
                                      (owner,)).fetchall()
            if provider not in {row['provider'] for row in rows}:
                raise IdentityConflict('not_linked')
            if len(rows) <= 1:
                raise IdentityConflict('last_login_method')
            connection.execute('DELETE FROM cloud_external_identities WHERE owner_user_id=%s AND provider=%s', (owner, provider))
            sessions.revoke_others_in_transaction(connection, owner, session['session_id'], now)
    except psycopg.Error as exc:
        raise SessionUnavailable('Login storage unavailable') from exc


def account_identities(sessions, session_token):
    """The session owner's sign-in methods and seconds left on its fresh email proof."""
    try:
        with sessions._connect() as connection:
            session, now = sessions.session_in_transaction(connection, session_token)
            rows = connection.execute(
                'SELECT provider,subject,created_at FROM cloud_external_identities WHERE owner_user_id=%s ORDER BY created_at',
                (session['owner_user_id'],)).fetchall()
    except psycopg.Error as exc:
        raise SessionUnavailable('Login storage unavailable') from exc
    proven = session.get('reauthenticated_at')
    remaining = max(0, int((proven + REAUTH_LIFETIME - now).total_seconds())) if proven else 0
    return [LinkedIdentity(row['provider'], row['subject'] if row['provider'] == 'email' else 'WeChat', row['created_at'])
            for row in rows], remaining


def wechat_from_environment():
    """No real WeChat adapter is authorized yet: any WeChat setting fails closed."""
    if any(name.startswith('CLOUD_WECHAT_') and value.strip() for name, value in os.environ.items()):
        raise SessionUnavailable('WeChat login is not authorized for this deployment')
    return None
