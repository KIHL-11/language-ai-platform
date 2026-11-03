"""Fully simulated WeChat provider for isolated tests only.

Lives in the test tree on purpose: no service module imports it and no
environment variable or HTTP route can install it, so it can never become a
production authentication bypass. ``approve`` stands in for the learner
scanning the QR code and consenting on WeChat.
"""
import secrets
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

from services.auth.external_identity import WECHAT_ISSUER, ExternalAssertion, ExternalLoginRejected


class SimulatedWeChatProvider:
    def __init__(self, app_id='wx0123456789abcdef'):
        self.issuer, self.app_id = WECHAT_ISSUER, app_id
        self._lock = threading.Lock()
        self._codes = {}
        self.exchanges = 0

    def authorization_url(self, *, state, redirect_uri):
        # .invalid never resolves (RFC 6761); nothing here contacts WeChat.
        return 'https://wechat.simulated.invalid/authorize?' + urlencode({'appid': self.app_id, 'state': state, 'redirect_uri': redirect_uri})

    def approve(self, authorization_url, openid, *, app_id=None, issuer=None):
        """Return (code, state) as WeChat would append them to the redirect URI."""
        query = parse_qs(urlsplit(authorization_url).query)
        code = secrets.token_urlsafe(24)
        with self._lock:
            self._codes[code] = (openid, query['redirect_uri'][0], app_id or self.app_id, issuer or self.issuer)
        return code, query['state'][0]

    def exchange(self, *, code, redirect_uri):
        with self._lock:
            self.exchanges += 1
            grant = self._codes.pop(code, None)  # single use, like WeChat
        if grant is None or grant[1] != redirect_uri:
            raise ExternalLoginRejected('Unknown or reused code')
        openid, _, app_id, issuer = grant
        return ExternalAssertion(issuer, app_id, openid)
