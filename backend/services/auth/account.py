"""Shared rules for changing an account's sign-in methods (P8-05B2/B3)."""
from services.auth.sessions import REAUTH_LIFETIME, account_lock  # noqa: F401  (re-exported)


class IdentityConflict(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class ReauthenticationRequired(RuntimeError):
    pass


def require_fresh_proof(session, now):
    proven = session.get('reauthenticated_at')
    if proven is None or proven + REAUTH_LIFETIME <= now:
        raise ReauthenticationRequired('Confirm your email address first')


def advisory_lock(connection, key):
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
