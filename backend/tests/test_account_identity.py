"""P8-05B2: simulated WeChat sign-in, account linking/unlinking and auth upkeep."""
import ast
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

import app as app_module
from services.auth.browser import configure_wechat_login
from services.auth.email_login import DirectoryMailbox, EmailLoginRejected, LOGIN_COOKIE_NAME
from services.auth.external_identity import (
    WECHAT_ISSUER, ExternalLoginRejected, WeChatAccountService, WeChatOAuthAdapter, unlink_identity, wechat_from_environment,
)
from services.auth.identity import InvalidAuthentication
from services.auth.maintenance import purge_auth_records
from services.auth.sessions import COOKIE_NAME, SessionPolicy, SessionUnavailable, csrf_token
from simulated_wechat import SimulatedWeChatProvider
from test_browser_sessions import sessions  # noqa: F401  (fixture)
from test_durable_ai_chunk_candidates import _migration, cloud_candidates  # noqa: F401  (fixture)
from test_email_login import CODE_KEY, EMAIL, ORIGIN, client, email_login, login, start, verify  # noqa: F401

APP_ID = 'wx0123456789abcdef'
OPENID = 'oSynthetic_Learner_01'
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def wechat(email_login):  # noqa: F811
    repo, clock, mailbox, service = email_login
    provider = SimulatedWeChatProvider(APP_ID)
    accounts = WeChatAccountService(repo, provider, SessionPolicy(ORIGIN), app_id=APP_ID)
    configure_wechat_login(app_module.app, accounts)
    try:
        yield repo, clock, mailbox, provider
    finally:
        if hasattr(app_module.app.state, 'wechat_account_service'):
            delattr(app_module.app.state, 'wechat_account_service')


def secured(browser):
    return {'Origin': ORIGIN, 'X-CSRF-Token': csrf_token(browser.cookies.get(COOKIE_NAME))}


def wechat_start(browser):
    response = browser.post('/api/account/wechat/start', json={}, headers={'Origin': ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()['authorization_url']


def complete(browser, code, state):
    return browser.post('/api/account/wechat/complete', json={'code': code, 'state': state}, headers={'Origin': ORIGIN})


def link(browser, provider, openid=OPENID):
    started = browser.post('/api/account/wechat/link/start', json={'confirm': 'link-wechat'}, headers=secured(browser))
    assert started.status_code == 200, started.text
    return complete(browser, *provider.approve(started.json()['authorization_url'], openid))


def wechat_login(provider, openid=OPENID):
    browser = client()
    return browser, complete(browser, *provider.approve(wechat_start(browser), openid))


def reauth(browser, mailbox, email=EMAIL):
    started = browser.post('/api/account/reauth/email/start', headers=secured(browser))
    assert started.status_code == 202, started.text
    assert started.json()['email'] == email
    return browser.post('/api/account/reauth/email/verify', headers=secured(browser),
                        json={'challenge_id': started.json()['challenge_id'], 'code': mailbox.latest(email).code})


def count(dsn, sql, *args):
    with psycopg.connect(dsn) as connection:
        return connection.execute(sql, args or None).fetchone()[0]


def test_unlinked_wechat_creates_no_account_and_link_then_login_reaches_the_same_account(wechat):
    repo, clock, mailbox, provider = wechat
    assert client().get('/api/account/providers').json() == {'email': True, 'wechat': True}
    stranger, refused = wechat_login(provider)
    assert refused.status_code == 409 and refused.json()['detail']['code'] == 'wechat_not_linked'
    assert COOKIE_NAME not in stranger.cookies and count(repo.dsn, "SELECT count(*) FROM cloud_users WHERE user_id LIKE 'u\\_%'") == 0

    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    unconfirmed = browser.post('/api/account/wechat/link/start', json={'confirm': 'yes'}, headers=secured(browser))
    assert unconfirmed.status_code == 422 and unconfirmed.json()['detail']['code'] == 'confirmation_required'
    session = browser.cookies.get(COOKIE_NAME)
    linked = link(browser, provider)
    assert linked.status_code == 200 and linked.json() == {'purpose': 'link'}
    assert browser.cookies.get(COOKIE_NAME) == session  # linking issues no new session
    listed = browser.get('/api/account/identities').json()
    assert [item['provider'] for item in listed['identities']] == ['email', 'wechat']
    assert OPENID not in str(listed) and listed['identities'][1]['label'] == 'WeChat'
    assert count(repo.dsn, "SELECT subject FROM cloud_external_identities WHERE provider='wechat'") == f'{APP_ID}:{OPENID}'

    clock.advance(minutes=1)
    other, signed_in = wechat_login(provider)
    assert signed_in.status_code == 200 and signed_in.json()['purpose'] == 'login' and signed_in.json()['user_id'] == owner
    assert other.get('/api/account/me').json()['email'] == EMAIL
    assert LOGIN_COOKIE_NAME not in other.cookies
    assert other.get('/api/account/identities').json()['reauthenticated_for'] == 0  # WeChat is not an email proof


def test_linking_needs_a_fresh_email_proof_bound_to_the_session(wechat):
    repo, clock, mailbox, provider = wechat
    email_service = app_module.app.state.email_login_service
    browser, _ = login(mailbox)
    clock.advance(minutes=11)
    browser.get('/api/account/me')  # keep the session alive; the proof has aged out
    stale = browser.post('/api/account/wechat/link/start', json={'confirm': 'link-wechat'}, headers=secured(browser))
    assert stale.status_code == 428 and stale.json()['detail']['code'] == 'reauthentication_required'

    started = browser.post('/api/account/reauth/email/start', headers=secured(browser)).json()
    code = mailbox.latest(EMAIL).code
    # Another browser cannot use the re-check code, neither as re-check nor as login.
    clock.advance(minutes=1)
    other, _ = login(mailbox)
    stolen = other.post('/api/account/reauth/email/verify', headers=secured(other),
                        json={'challenge_id': started['challenge_id'], 'code': code})
    assert stolen.status_code == 400
    with pytest.raises(EmailLoginRejected):  # nor as a login code, even from the same browser
        email_service.verify(started['challenge_id'], code, browser.cookies.get(COOKIE_NAME))
    assert count(repo.dsn, 'SELECT failed_attempts FROM cloud_email_login_challenges WHERE challenge_id=%s', started['challenge_id']) == 0
    proven = browser.post('/api/account/reauth/email/verify', headers=secured(browser),
                          json={'challenge_id': started['challenge_id'], 'code': code})
    assert proven.status_code == 200 and proven.json()['reauthenticated_for'] == 600
    assert link(browser, provider).status_code == 200
    unauthenticated = client().post('/api/account/reauth/email/start', headers={'Origin': ORIGIN})
    assert unauthenticated.status_code in (401, 403)


def test_callback_state_is_single_use_browser_bound_and_short_lived(wechat):
    repo, clock, mailbox, provider = wechat
    browser, _ = login(mailbox)
    assert link(browser, provider).status_code == 200

    victim = client()
    code, state = provider.approve(wechat_start(victim), OPENID)
    attacker = client()
    wechat_start(attacker)  # attacker holds their own pre-auth cookie
    exchanges = provider.exchanges
    assert complete(attacker, code, state).status_code == 400  # cannot redeem the victim's state
    assert provider.exchanges == exchanges  # rejected before the code reaches the provider
    assert complete(victim, code, state).status_code == 200  # and cannot burn it for the victim either

    # Login CSRF: the attacker's own code and state planted in the victim's browser.
    code, state = provider.approve(wechat_start(attacker), OPENID)
    planted = client()
    wechat_start(planted)
    assert complete(planted, code, state).status_code == 400 and COOKIE_NAME not in planted.cookies

    # Replays: same state twice, and the provider code under a new state.
    fresh = client()
    code, state = provider.approve(wechat_start(fresh), OPENID)
    assert complete(fresh, code, state).status_code == 200
    replay = client()
    assert complete(replay, code, state).status_code == 400
    _, new_state = provider.approve(wechat_start(replay), OPENID)
    assert complete(replay, code, new_state).status_code == 400  # provider code already used

    late = client()
    code, state = provider.approve(wechat_start(late), OPENID)
    clock.advance(minutes=10)
    assert complete(late, code, state).status_code == 400
    exchanges = provider.exchanges
    assert complete(client(), code, 'A' * 43).status_code == 400
    assert provider.exchanges == exchanges  # an unknown state never reaches the provider
    assert client().post('/api/account/wechat/complete', json={'code': code, 'state': state}).status_code == 403


@pytest.mark.parametrize('assertion', [{'app_id': 'wxfedcba9876543210'}, {'issuer': 'https://open.weixin.example'}])
def test_assertion_from_another_issuer_or_app_is_rejected(wechat, assertion):
    repo, _, mailbox, provider = wechat
    browser, _ = login(mailbox)
    started = browser.post('/api/account/wechat/link/start', json={'confirm': 'link-wechat'}, headers=secured(browser)).json()
    assert complete(browser, *provider.approve(started['authorization_url'], OPENID, **assertion)).status_code == 400
    assert count(repo.dsn, "SELECT count(*) FROM cloud_external_identities WHERE provider='wechat'") == 0


def test_malformed_subject_is_rejected(wechat):
    repo, _, mailbox, provider = wechat
    browser, _ = login(mailbox)
    assert link(browser, provider, openid='bad openid!').status_code == 400
    assert count(repo.dsn, "SELECT count(*) FROM cloud_external_identities WHERE provider='wechat'") == 0


def test_no_cross_account_takeover_or_second_wechat(wechat):
    repo, clock, mailbox, provider = wechat
    alice, verified = login(mailbox)
    alice_id = verified.json()['user_id']
    assert link(alice, provider).status_code == 200
    clock.advance(minutes=1)
    bob, verified = login(mailbox, 'learner.two@example.test')
    taken = link(bob, provider)
    assert taken.status_code == 409 and taken.json()['detail']['code'] == 'identity_conflict'
    assert count(repo.dsn, "SELECT owner_user_id FROM cloud_external_identities WHERE provider='wechat'") == alice_id
    second = alice.post('/api/account/wechat/link/start', json={'confirm': 'link-wechat'}, headers=secured(alice))
    assert second.status_code == 409
    # Bob's attempt left nothing behind, and Bob can still link his own WeChat.
    assert link(bob, provider, openid='oSynthetic_Learner_02').status_code == 200
    _, as_alice = wechat_login(provider)
    assert as_alice.json()['user_id'] == alice_id


def test_link_must_finish_in_the_session_that_started_it(wechat):
    repo, clock, mailbox, provider = wechat
    browser, _ = login(mailbox)
    started = browser.post('/api/account/wechat/link/start', json={'confirm': 'link-wechat'}, headers=secured(browser)).json()
    code, state = provider.approve(started['authorization_url'], OPENID)
    clock.advance(minutes=1)
    browser.post('/api/account/session/rotate', headers=secured(browser))  # new session id, same browser
    assert complete(browser, code, state).status_code == 400

    started = browser.post('/api/account/reauth/email/start', headers=secured(browser)).json()
    browser.post('/api/account/reauth/email/verify', headers=secured(browser),
                 json={'challenge_id': started['challenge_id'], 'code': mailbox.latest(EMAIL).code})
    started = browser.post('/api/account/wechat/link/start', json={'confirm': 'link-wechat'}, headers=secured(browser)).json()
    code, state = provider.approve(started['authorization_url'], OPENID)
    assert browser.post('/api/account/logout', headers=secured(browser)).status_code == 204
    assert complete(browser, code, state).status_code == 400
    assert count(repo.dsn, "SELECT count(*) FROM cloud_external_identities WHERE provider='wechat'") == 0


def test_concurrent_links_of_one_wechat_bind_it_to_exactly_one_account(wechat):
    repo, clock, mailbox, provider = wechat
    browsers = []
    for index in range(4):
        browser, _ = login(mailbox, f'learner.{index}@example.test')
        started = browser.post('/api/account/wechat/link/start', json={'confirm': 'link-wechat'}, headers=secured(browser)).json()
        browsers.append((browser, *provider.approve(started['authorization_url'], OPENID)))
    with ThreadPoolExecutor(4) as pool:
        statuses = sorted(pool.map(lambda args: complete(*args).status_code, browsers))
    assert statuses == [200, 409, 409, 409]
    assert count(repo.dsn, "SELECT count(*) FROM cloud_external_identities WHERE provider='wechat'") == 1


def test_unlink_needs_proof_and_confirmation_keeps_a_login_method_and_signs_out_elsewhere(wechat):
    repo, clock, mailbox, provider = wechat
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    assert link(browser, provider).status_code == 200
    phone, _ = wechat_login(provider)
    unlink = lambda body: browser.post('/api/account/identities/unlink', json=body, headers=secured(browser))  # noqa: E731
    assert browser.post('/api/account/identities/unlink', json={'provider': 'wechat', 'confirm': 'unlink-wechat'},
                        headers={'Origin': ORIGIN}).status_code == 403  # CSRF
    assert unlink({'provider': 'wechat', 'confirm': 'yes'}).json()['detail']['code'] == 'confirmation_required'
    assert unlink({'provider': 'email', 'confirm': 'unlink-email'}).json()['detail']['code'] == 'unlink_unsupported'
    clock.advance(minutes=11)
    browser.get('/api/account/me')
    assert unlink({'provider': 'wechat', 'confirm': 'unlink-wechat'}).status_code == 428
    assert reauth(browser, mailbox).status_code == 200
    phone.get('/api/account/me')
    assert unlink({'provider': 'wechat', 'confirm': 'unlink-wechat'}).status_code == 204
    assert browser.get('/api/account/me').status_code == 200
    assert phone.get('/api/account/me').status_code == 401  # the WeChat-signed-in browser is signed out
    assert unlink({'provider': 'wechat', 'confirm': 'unlink-wechat'}).json()['detail']['code'] == 'not_linked'
    _, after = wechat_login(provider)
    assert after.status_code == 409

    # Last-method guard, for an account whose only remaining method is WeChat.
    assert link(browser, provider).status_code == 200
    with psycopg.connect(repo.dsn) as connection:
        connection.execute("DELETE FROM cloud_external_identities WHERE provider='email' AND owner_user_id=%s", (owner,))
    last = unlink({'provider': 'wechat', 'confirm': 'unlink-wechat'})
    assert last.status_code == 422 and last.json()['detail']['code'] == 'last_login_method'
    assert count(repo.dsn, 'SELECT count(*) FROM cloud_external_identities WHERE owner_user_id=%s', owner) == 1


def test_wechat_and_account_routes_are_unavailable_without_configuration_and_in_personal(email_login, monkeypatch):
    *_, mailbox, _ = email_login
    browser, _ = login(mailbox)
    assert client().get('/api/account/providers').json()['wechat'] is False
    assert client().post('/api/account/wechat/start', json={}, headers={'Origin': ORIGIN}).status_code == 503
    assert browser.get('/api/account/identities').json()['wechat_available'] is False
    monkeypatch.setenv('LANGUAGE_AI_DEPLOYMENT_MODE', 'personal')
    personal = client()
    assert personal.get('/api/account/providers').json() == {'email': False, 'wechat': False}
    bodies = {'/api/account/wechat/start': {}, '/api/account/wechat/complete': {'code': 'c' * 16, 'state': 's' * 43},
              '/api/account/reauth/email/start': None, '/api/account/wechat/link/start': {'confirm': 'link-wechat'},
              '/api/account/identities/unlink': {'provider': 'wechat', 'confirm': 'unlink-wechat'}}
    for path, body in bodies.items():
        assert personal.post(path, json=body).status_code == 503, path
    assert personal.get('/api/account/identities').status_code == 503


def test_simulated_provider_cannot_be_enabled_outside_tests(monkeypatch):
    for module in (BACKEND / 'services').rglob('*.py'):
        tree = ast.parse(module.read_text())
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not any('simulated' in name for name in imported), module
    assert wechat_from_environment() is None
    monkeypatch.setenv('CLOUD_WECHAT_APP_ID', APP_ID)
    with pytest.raises(SessionUnavailable):
        wechat_from_environment()
    with pytest.raises(ValueError):
        configure_wechat_login(app_module.app, object())


def test_cloud_bootstrap_requires_a_code_key_and_refuses_wechat_settings(tmp_path, monkeypatch):
    from services.auth import browser
    from starlette.datastructures import State

    class Stub:
        state = State()
    sandbox = tmp_path / 'outbox'
    monkeypatch.setenv('CLOUD_SESSION_DATABASE_URL', 'postgresql://unused.invalid/db')
    monkeypatch.setenv('CLOUD_SESSION_ORIGIN', 'https://app.localhost')
    monkeypatch.setenv('CLOUD_EMAIL_DELIVERY', 'sandbox-directory')
    monkeypatch.setenv('CLOUD_EMAIL_SANDBOX_DIR', str(sandbox))
    monkeypatch.setenv('LANGUAGE_AI_STAGING', '1')
    monkeypatch.delenv('CLOUD_AUTH_CODE_KEY', raising=False)
    with pytest.raises(SessionUnavailable):
        browser._bootstrap(Stub())
    monkeypatch.setenv('CLOUD_AUTH_CODE_KEY', 'ab' * 16)  # 16 bytes: too short
    with pytest.raises(SessionUnavailable):
        browser._bootstrap(Stub())
    monkeypatch.setenv('CLOUD_AUTH_CODE_KEY', 'ab' * 32)
    monkeypatch.setenv('CLOUD_WECHAT_SECRET', 'synthetic')
    with pytest.raises(SessionUnavailable):
        browser._bootstrap(Stub())
    monkeypatch.delenv('CLOUD_WECHAT_SECRET')
    stub = Stub()
    browser._bootstrap(stub)
    assert stub.state.email_login_service._code_key == bytes.fromhex('ab' * 32)
    assert not hasattr(stub.state, 'wechat_account_service')


def test_real_protocol_adapter_parses_wechat_responses_without_network():
    calls = []

    def transport(url, params):
        calls.append((url, params))
        return responses.pop(0)
    adapter = WeChatOAuthAdapter(APP_ID, 'synthetic-secret', transport)
    url = adapter.authorization_url(state='s' * 43, redirect_uri='https://learn.example/?oauth=wechat')
    assert url.startswith('https://open.weixin.qq.com/connect/qrconnect?') and url.endswith('#wechat_redirect')
    assert 'scope=snsapi_login' in url and f'appid={APP_ID}' in url and 'secret' not in url
    responses = [{'access_token': 'synthetic', 'openid': OPENID, 'scope': 'snsapi_login', 'expires_in': 7200},
                 {'errcode': 40029, 'errmsg': 'invalid code'}, {'access_token': 'x', 'openid': OPENID, 'scope': 'snsapi_userinfo'},
                 {'access_token': 'x', 'scope': 'snsapi_login'}]
    assertion = adapter.exchange(code='c' * 32, redirect_uri='unused')
    assert (assertion.issuer, assertion.app_id, assertion.openid) == (WECHAT_ISSUER, APP_ID, OPENID)
    assert calls[0][0] == 'https://api.weixin.qq.com/sns/oauth2/access_token'
    assert calls[0][1]['grant_type'] == 'authorization_code'
    for _ in range(3):
        with pytest.raises(ExternalLoginRejected):
            adapter.exchange(code='c' * 32, redirect_uri='unused')

    def offline(url, params):
        raise OSError('no network in tests')
    with pytest.raises(SessionUnavailable):
        WeChatOAuthAdapter(APP_ID, 'synthetic-secret', offline).exchange(code='c' * 32, redirect_uri='unused')
    for bad in (('wx123', 's', transport), (APP_ID, '', transport), (APP_ID, 's', None)):
        with pytest.raises(ValueError):
            WeChatOAuthAdapter(*bad)


def test_purge_is_bounded_and_keeps_rows_that_still_feed_throttles(wechat):
    repo, clock, mailbox, provider = wechat
    for index in range(7):
        start(client(), f'old.{index}@example.test')
    wechat_start(client())
    clock.advance(days=1, hours=2)
    start(client(), 'recent@example.test')
    wechat_start(client())
    now = clock()
    first = purge_auth_records(repo.dsn, now, batch=3, max_batches=2)
    assert first == {'email_challenges': 6, 'oauth_states': 1}
    second = purge_auth_records(repo.dsn, now, batch=3, max_batches=2)
    assert second == {'email_challenges': 1, 'oauth_states': 0}
    assert count(repo.dsn, 'SELECT count(*) FROM cloud_email_login_challenges') == 1
    assert count(repo.dsn, 'SELECT count(*) FROM cloud_oauth_states') == 1
    with pytest.raises(ValueError):
        purge_auth_records(repo.dsn, now, batch=0)


def test_outbox_purge_only_removes_expired_adapter_files(tmp_path):
    outbox = tmp_path / 'outbox'
    mailbox = DirectoryMailbox(outbox)
    now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    for minutes in (-180, -120, -90, 30):
        mailbox.send_login_code(email=EMAIL, code='123456', expires_at=now + timedelta(minutes=minutes))
    (outbox / 'notes.txt').write_text('keep')
    victim = tmp_path / 'elsewhere.txt'
    victim.write_text('keep')
    os.symlink(victim, outbox / '20000101T000000Z-' f'{"0" * 32}.txt')
    assert mailbox.purge(now, limit=2) == 2
    assert mailbox.purge(now) == 1
    remaining = sorted(path.name for path in outbox.iterdir())
    assert len(remaining) == 3 and 'notes.txt' in remaining and victim.read_text() == 'keep'
    os.chmod(outbox, 0o755)
    with pytest.raises(OSError):
        mailbox.purge(now)
    assert DirectoryMailbox(tmp_path / 'missing').purge(now) == 0


def test_least_privilege_role_links_signs_in_and_purges(wechat):
    repo, clock, mailbox, provider = wechat
    from psycopg.conninfo import make_conninfo
    from services.auth.email_login import EmailLoginService
    from services.auth.sessions import BrowserSessionRepository
    with psycopg.connect(repo.dsn) as connection:
        connection.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='account_auth_test') THEN "
                           "CREATE ROLE account_auth_test LOGIN PASSWORD 'synthetic-test' NOSUPERUSER NOBYPASSRLS; END IF; END $$")
        connection.execute('GRANT USAGE ON SCHEMA public TO account_auth_test')
        connection.execute('GRANT SELECT,INSERT,UPDATE ON cloud_browser_sessions, cloud_email_login_challenges, '
                           'cloud_external_identities, cloud_oauth_states TO account_auth_test')
        connection.execute('GRANT DELETE ON cloud_external_identities, cloud_email_login_challenges, cloud_oauth_states TO account_auth_test')
        connection.execute('GRANT INSERT ON cloud_users TO account_auth_test')
    dsn = make_conninfo(repo.dsn, user='account_auth_test', password='synthetic-test')
    runtime = BrowserSessionRepository(dsn, repo.clock)
    emails = EmailLoginService(runtime, mailbox, code_key=CODE_KEY)
    accounts = WeChatAccountService(runtime, provider, SessionPolicy(ORIGIN), app_id=APP_ID)
    from services.auth.email_login import new_client_binding
    binding = new_client_binding()
    signed_in = emails.verify(emails.start(EMAIL, binding).challenge_id, mailbox.latest(EMAIL).code, binding)
    url = accounts.start_link(signed_in.session_token, binding, 'link-wechat')
    assert accounts.complete(*provider.approve(url, OPENID), binding, signed_in.session_token) is None
    url = accounts.start_login(binding)
    assert accounts.complete(*provider.approve(url, OPENID), binding).owner_user_id == signed_in.owner_user_id
    accounts.unlink(signed_in.session_token, 'wechat', 'unlink-wechat')
    clock.advance(days=2)
    assert purge_auth_records(dsn, clock())['email_challenges'] == 1
    with runtime._connect() as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute('SELECT * FROM cloud_study_items')
    with pytest.raises(InvalidAuthentication):
        accounts.unlink('x' * 43, 'wechat', 'unlink-wechat')


def test_rollback_refuses_while_wechat_links_exist_and_loses_nothing(wechat):
    repo, clock, mailbox, provider = wechat
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    assert link(browser, provider).status_code == 200
    with psycopg.connect(repo.dsn) as connection:
        identities = connection.execute('SELECT * FROM cloud_external_identities ORDER BY provider').fetchall()
        challenges = connection.execute('SELECT count(*) FROM cloud_email_login_challenges').fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException, match='WeChat identities exist'):
        _migration(repo.dsn, '0008_account_identity.down.sql')
    with psycopg.connect(repo.dsn) as connection:  # the refused rollback changed nothing
        assert connection.execute('SELECT * FROM cloud_external_identities ORDER BY provider').fetchall() == identities
        assert connection.execute("SELECT count(*) FROM cloud_oauth_states").fetchone()[0] >= 1
    _, still = wechat_login(provider)
    assert still.json()['user_id'] == owner

    assert browser.post('/api/account/identities/unlink', json={'provider': 'wechat', 'confirm': 'unlink-wechat'},
                        headers=secured(browser)).status_code == 204
    _migration(repo.dsn, '0008_account_identity.down.sql')
    with psycopg.connect(repo.dsn) as connection:
        assert connection.execute("SELECT subject FROM cloud_external_identities").fetchall() == [(EMAIL,)]
        assert connection.execute('SELECT count(*) FROM cloud_email_login_challenges').fetchone()[0] == challenges
        assert connection.execute("SELECT to_regclass('cloud_external_identities_owner')").fetchone()[0] is not None
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute("INSERT INTO cloud_external_identities SELECT 'wechat',subject,owner_user_id,verified_at,created_at "
                               'FROM cloud_external_identities')
    _migration(repo.dsn, '0008_account_identity.up.sql')
    again = browser.post('/api/account/wechat/link/start', json={'confirm': 'link-wechat'}, headers=secured(browser))
    assert again.status_code == 428  # the fresh-proof marker did not survive the rollback

def test_state_is_single_use_even_if_the_provider_issued_a_second_code(wechat):
    _, _, mailbox, provider = wechat
    browser, _ = login(mailbox)
    assert link(browser, provider).status_code == 200
    replayed = client()
    url = wechat_start(replayed)
    binding = replayed.cookies.get(LOGIN_COOKIE_NAME)
    assert complete(replayed, *provider.approve(url, OPENID)).status_code == 200
    replayed.cookies.set(LOGIN_COOKIE_NAME, binding, domain='learn.example', path='/')  # keep the original binding
    code, state = provider.approve(url, OPENID)  # a second valid provider code for the old state
    exchanges = provider.exchanges
    assert complete(replayed, code, state).status_code == 400
    assert provider.exchanges == exchanges


def test_reauth_codes_are_purpose_bound_and_follow_the_current_email(wechat):
    repo, clock, mailbox, _ = wechat
    service = app_module.app.state.email_login_service
    from services.auth.email_login import _binding_digest, new_client_binding
    browser, _ = login(mailbox)
    clock.advance(seconds=61)
    started = browser.post('/api/account/reauth/email/start', headers=secured(browser)).json()
    code = mailbox.latest(EMAIL).code
    binding = new_client_binding()
    with psycopg.connect(repo.dsn) as connection:  # even with a matching browser binding
        connection.execute('UPDATE cloud_email_login_challenges SET client_binding=%s WHERE challenge_id=%s',
                           (_binding_digest(binding), started['challenge_id']))
    with pytest.raises(EmailLoginRejected):
        service.verify(started['challenge_id'], code, binding)  # a re-check code is never a login code

    started = browser.post('/api/account/reauth/email/start', headers=secured(browser))
    assert started.status_code == 429  # shares the address's resend throttle
    clock.advance(seconds=61)
    started = browser.post('/api/account/reauth/email/start', headers=secured(browser)).json()
    code = mailbox.latest(EMAIL).code
    with psycopg.connect(repo.dsn) as connection:
        connection.execute("UPDATE cloud_external_identities SET subject='moved@example.test' WHERE subject=%s", (EMAIL,))
    assert browser.post('/api/account/reauth/email/verify', headers=secured(browser),
                        json={'challenge_id': started['challenge_id'], 'code': code}).status_code == 400
    assert browser.get('/api/account/identities').json()['reauthenticated_for'] > 0  # only the login's own proof


def test_wechat_login_racing_an_unlink_gets_no_session(wechat):
    """Unlink commits while a WeChat callback is mid-flight: the callback must lose."""
    import threading
    import time
    from services.auth.email_login import new_client_binding
    from services.auth.external_identity import IdentityNotLinked
    repo, _, mailbox, provider = wechat
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    assert link(browser, provider).status_code == 200
    accounts = app_module.app.state.wechat_account_service
    binding = new_client_binding()
    code, state = provider.approve(accounts.start_login(binding), OPENID)
    outcome = {}
    with psycopg.connect(repo.dsn) as unlinking:  # plays unlink: account lock + delete, not yet committed
        unlinking.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f'language-ai-account:{owner}',))
        unlinking.execute("DELETE FROM cloud_external_identities WHERE provider='wechat' AND owner_user_id=%s", (owner,))

        def callback():
            try:
                outcome['result'] = accounts.complete(code, state, binding)
            except Exception as exc:  # noqa: BLE001
                outcome['result'] = exc
        worker = threading.Thread(target=callback)
        worker.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not count(
                repo.dsn, "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND datname=current_database()"):
            time.sleep(0.02)
        assert worker.is_alive()  # the callback is waiting on the account lock
    worker.join(5)
    assert isinstance(outcome['result'], IdentityNotLinked)
    assert count(repo.dsn, 'SELECT count(*) FROM cloud_browser_sessions WHERE owner_user_id=%s AND revoked_at IS NULL', owner) == 1


def test_unlink_works_after_wechat_sign_in_is_switched_off(wechat):
    _, _, mailbox, provider = wechat
    browser, _ = login(mailbox)
    assert link(browser, provider).status_code == 200
    delattr(app_module.app.state, 'wechat_account_service')
    assert client().get('/api/account/providers').json()['wechat'] is False
    assert browser.post('/api/account/identities/unlink', json={'provider': 'wechat', 'confirm': 'unlink-wechat'},
                        headers=secured(browser)).status_code == 204


def _blocked_on_lock(dsn, timeout=5):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if count(dsn, "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND datname=current_database()"):
            return True
        time.sleep(0.02)
    return False


@pytest.mark.parametrize('operation', ['rotate', 'revoke_all'])
def test_rotation_and_revoke_all_wait_for_the_account_lock(wechat, operation):
    """Unlink and revoke-all hold the account lock; rotation must not slip a new session past them."""
    import threading
    repo, _, mailbox, provider = wechat
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    token = browser.cookies.get(COOKIE_NAME)
    done = {}
    with psycopg.connect(repo.dsn) as holder:
        holder.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f'language-ai-account:{owner}',))
        worker = threading.Thread(target=lambda: done.setdefault(
            'r', repo.rotate(token) if operation == 'rotate' else repo.revoke_all(owner)))
        worker.start()
        assert _blocked_on_lock(repo.dsn) and worker.is_alive()
    worker.join(5)
    assert 'r' in done


def test_concurrent_rotation_never_outlives_unlink_signout(wechat):
    import threading
    repo, clock, mailbox, provider = wechat
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    for round_ in range(4):
        assert link(browser, provider).status_code == 200
        phone, _ = wechat_login(provider)
        phone_token = phone.cookies.get(COOKIE_NAME)
        results = {}
        with psycopg.connect(repo.dsn) as holder:  # release both at once
            holder.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f'language-ai-account:{owner}',))

            def rotate():
                try:
                    results['rotated'] = repo.rotate(phone_token)
                except InvalidAuthentication:
                    results['rotated'] = None
            workers = [threading.Thread(target=rotate), threading.Thread(
                target=lambda: unlink_identity(repo, browser.cookies.get(COOKIE_NAME), 'wechat', 'unlink-wechat'))]
            for worker in (workers if round_ % 2 else reversed(workers)):
                worker.start()
            assert _blocked_on_lock(repo.dsn)
        for worker in workers:
            worker.join(5)
        assert count(repo.dsn, 'SELECT count(*) FROM cloud_browser_sessions WHERE owner_user_id=%s AND revoked_at IS NULL',
                     owner) == 1  # only the browser that unlinked
        assert results['rotated'] is None or count(
            repo.dsn, 'SELECT revoked_at IS NOT NULL FROM cloud_browser_sessions WHERE token_hash=%s',
            __import__('services.auth.sessions', fromlist=['digest']).digest(results['rotated'])) is True
        clock.advance(minutes=1)
        assert reauth(browser, mailbox).status_code == 200
        clock.advance(minutes=1)


def test_email_relogin_during_unlink_in_the_same_browser_does_not_deadlock(wechat):
    import threading
    from services.auth.email_login import new_client_binding
    from services.auth.sessions import digest
    repo, clock, mailbox, provider = wechat
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    assert link(browser, provider).status_code == 200
    s1 = browser.cookies.get(COOKIE_NAME)
    service = app_module.app.state.email_login_service
    clock.advance(minutes=2)
    binding = new_client_binding()
    challenge = service.start(EMAIL, binding).challenge_id
    code = mailbox.latest(EMAIL).code
    outcome = {}
    with psycopg.connect(repo.dsn) as unlinking:  # unlink's lock order: account lock, own session row, identities
        unlinking.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f'language-ai-account:{owner}',))
        unlinking.execute('SELECT * FROM cloud_browser_sessions WHERE token_hash=%s FOR UPDATE', (digest(s1),))
        worker = threading.Thread(target=lambda: outcome.setdefault('login', service.verify(challenge, code, binding, replaces=s1)))
        worker.start()
        assert _blocked_on_lock(repo.dsn)
        unlinking.execute('SELECT provider FROM cloud_external_identities WHERE owner_user_id=%s FOR UPDATE', (owner,))
    worker.join(5)
    assert outcome['login'].owner_user_id == owner
    with pytest.raises(InvalidAuthentication):
        repo.verify(s1)


def test_email_relogin_in_one_browser_while_another_unlinks_does_not_deadlock(wechat):
    """Browser 1 signs in again by email (replacing its live session) while browser 2 unlinks WeChat."""
    import threading
    import time
    from services.auth.email_login import new_client_binding
    repo, clock, mailbox, provider = wechat
    first, verified = login(mailbox)
    owner = verified.json()['user_id']
    assert link(first, provider).status_code == 200
    clock.advance(seconds=61)
    second, _ = login(mailbox)
    clock.advance(seconds=61)
    service = app_module.app.state.email_login_service
    binding = new_client_binding()
    challenge = service.start(EMAIL, binding).challenge_id
    code = mailbox.latest(EMAIL).code
    entered, go = threading.Event(), threading.Event()
    original = service._map_identity

    def paused(*args):  # hold the login mid-transaction, after its locks
        entered.set()
        go.wait(10)
        return original(*args)
    service._map_identity = paused
    outcome = {}

    def relogin():
        try:
            outcome['login'] = service.verify(challenge, code, binding, replaces=first.cookies.get(COOKIE_NAME)).owner_user_id
        except Exception as exc:  # noqa: BLE001
            outcome['login'] = exc

    def unlink():
        try:
            unlink_identity(repo, second.cookies.get(COOKIE_NAME), 'wechat', 'unlink-wechat')
            outcome['unlink'] = 'ok'
        except Exception as exc:  # noqa: BLE001
            outcome['unlink'] = exc
    workers = [threading.Thread(target=relogin), threading.Thread(target=unlink)]
    workers[0].start()
    assert entered.wait(5)
    workers[1].start()
    time.sleep(0.5)
    go.set()
    for worker in workers:
        worker.join(15)
    service._map_identity = original
    assert outcome == {'login': owner, 'unlink': 'ok'}, outcome
