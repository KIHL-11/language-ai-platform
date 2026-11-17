from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.auth.browser import configure_email_login
from services.auth.email_login import (
    DirectoryMailbox, EmailLoginRejected, EmailLoginService, InvalidEmailAddress, SandboxMailbox,
    LOGIN_COOKIE_NAME, mailer_from_environment, new_client_binding, normalize_email,
)
from services.auth.identity import InvalidAuthentication
from services.auth.sessions import COOKIE_NAME, BrowserSessionRepository, SessionUnavailable, csrf_token
from test_browser_sessions import sessions  # noqa: F401  (fixture)
from test_durable_ai_chunk_candidates import _migration, cloud_candidates  # noqa: F401  (fixture)

ORIGIN = 'https://learn.example'
EMAIL = 'learner.one@example.test'
CODE_KEY = bytes(range(32))  # synthetic; production reads CLOUD_AUTH_CODE_KEY


def _reset_identities(dsn):
    # Test isolation only: migrations refuse to drop verified identities, and
    # every test starts from an empty synthetic schema anyway.
    with psycopg.connect(dsn) as connection:
        if connection.execute("SELECT to_regclass('cloud_external_identities')").fetchone()[0]:
            connection.execute('DELETE FROM cloud_external_identities')


@pytest.fixture
def email_login(sessions):  # noqa: F811
    repo, clock, *_ = sessions
    _reset_identities(repo.dsn)
    _migration(repo.dsn, '0009_email_management.down.sql')
    _migration(repo.dsn, '0008_account_identity.down.sql')
    _migration(repo.dsn, '0007_email_login.down.sql')
    _migration(repo.dsn, '0007_email_login.up.sql')
    _migration(repo.dsn, '0008_account_identity.up.sql')
    _migration(repo.dsn, '0009_email_management.up.sql')
    mailbox = SandboxMailbox()
    service = EmailLoginService(repo, mailbox, code_key=CODE_KEY)
    configure_email_login(app_module.app, service)
    try:
        yield repo, clock, mailbox, service
    finally:
        if hasattr(app_module.app.state, 'email_login_service'):
            delattr(app_module.app.state, 'email_login_service')
        _reset_identities(repo.dsn)
        _migration(repo.dsn, '0009_email_management.down.sql')
        _migration(repo.dsn, '0008_account_identity.down.sql')
        _migration(repo.dsn, '0007_email_login.down.sql')


def client():
    return TestClient(app_module.app, base_url=ORIGIN)


def start(browser, email=EMAIL, **headers):
    return browser.post('/api/account/email/start', json={'email': email}, headers={'Origin': ORIGIN, **headers})


def verify(browser, challenge_id, code):
    return browser.post('/api/account/email/verify', json={'challenge_id': challenge_id, 'code': code},
                        headers={'Origin': ORIGIN})


def login(mailbox, email=EMAIL):
    browser = client()
    started = start(browser, email)
    assert started.status_code == 202
    code = mailbox.latest(normalize_email(email)).code
    return browser, verify(browser, started.json()['challenge_id'], code)


def wrong(code):
    return f'{(int(code) + 1) % 1_000_000:06d}'


def test_verified_email_login_maps_one_account_and_issues_session(email_login):
    repo, clock, mailbox, _ = email_login
    browser, verified = login(mailbox)
    assert verified.status_code == 200
    body = verified.json()
    owner = body['user_id']
    assert body['created_account'] is True and owner.startswith('u_')
    cookie = verified.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie and 'Domain=' not in cookie
    assert verified.headers['cache-control'] == 'no-store'
    assert body['csrf_token'] == csrf_token(browser.cookies.get(COOKIE_NAME))
    me = browser.get('/api/account/me').json()
    assert me['user_id'] == owner and me['email'] == EMAIL
    clock.advance(minutes=2)
    again, second = login(mailbox, '  Learner.One@EXAMPLE.test ')
    assert second.json()['user_id'] == owner and second.json()['created_account'] is False
    clock.advance(minutes=2)
    _, other = login(mailbox, 'learner.two@example.test')
    assert other.json()['user_id'] != owner
    with psycopg.connect(repo.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM cloud_external_identities WHERE subject=%s", (EMAIL,)).fetchone()[0] == 1
        hashes = [row[0] for row in connection.execute('SELECT code_hash FROM cloud_email_login_challenges')]
    assert all(len(value) == 64 for value in hashes)
    assert not {message.code for message in mailbox.messages} & set(hashes)


def test_code_is_single_use_expires_and_locks_after_failed_attempts(email_login):
    _, clock, mailbox, _ = email_login
    browser = client()
    challenge = start(browser).json()['challenge_id']
    code = mailbox.latest(EMAIL).code
    binding = browser.cookies.get(LOGIN_COOKIE_NAME)
    assert verify(browser, challenge, code).status_code == 200
    assert LOGIN_COOKIE_NAME not in browser.cookies
    with pytest.raises(EmailLoginRejected):
        email_login[3].verify(challenge, code, binding)

    clock.advance(minutes=2)
    challenge = start(browser).json()['challenge_id']
    code = mailbox.latest(EMAIL).code
    for _ in range(5):
        assert verify(browser, challenge, wrong(code)).json()['detail']['code'] == 'email_code_invalid'
    assert verify(browser, challenge, code).status_code == 400

    clock.advance(minutes=2)
    challenge = start(browser).json()['challenge_id']
    code = mailbox.latest(EMAIL).code
    clock.advance(minutes=10)
    assert verify(browser, challenge, code).status_code == 400
    assert verify(browser, '00000000-0000-4000-8000-000000000000', code).status_code == 400


def test_resend_throttle_supersedes_older_codes_and_caps_hourly_volume(email_login):
    _, clock, mailbox, _ = email_login
    browser = client()
    first = start(browser).json()
    assert first['resend_after'] == 60 and first['expires_in'] == 600
    first_code = mailbox.latest(EMAIL).code
    throttled = start(browser, 'LEARNER.ONE@example.test')
    assert throttled.status_code == 429 and int(throttled.headers['retry-after']) >= 1
    assert len(mailbox.messages) == 1
    clock.advance(seconds=61)
    second = start(browser).json()
    assert verify(browser, first['challenge_id'], first_code).status_code == 400
    for _ in range(3):
        clock.advance(seconds=61)
        assert start(browser).status_code == 202
    clock.advance(seconds=61)
    capped = start(browser)
    assert capped.status_code == 429 and capped.json()['detail']['retry_after'] > 60
    assert start(browser, 'someone.else@example.test').status_code == 202
    assert second['challenge_id'] != first['challenge_id']


def test_login_endpoints_require_same_origin_and_accept_no_identity_claims(email_login):
    *_, mailbox, _ = email_login
    browser = client()
    assert browser.post('/api/account/email/start', json={'email': EMAIL}).status_code == 403
    assert start(browser, Origin='https://evil.example').status_code == 403
    assert start(browser, **{'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    insecure = TestClient(app_module.app, base_url='http://learn.example')
    assert insecure.post('/api/account/email/start', json={'email': EMAIL}, headers={'Origin': ORIGIN}).status_code == 403
    assert browser.post('/api/account/email/start', json={'email': EMAIL, 'user_id': 'owner'},
                        headers={'Origin': ORIGIN}).status_code == 422
    assert start(browser, 'not-an-address').json()['detail']['code'] == 'email_invalid'
    assert browser.post('/api/account/login', json={'user_id': 'candidate-cloud-a'}, headers={'Origin': ORIGIN}).status_code == 503
    assert mailbox.messages == []
    challenge = start(browser).json()['challenge_id']
    code = mailbox.latest(EMAIL).code
    cross = client().post('/api/account/email/verify', json={'challenge_id': challenge, 'code': code},
                          headers={'Origin': 'https://evil.example'})
    assert cross.status_code == 403 and COOKIE_NAME not in cross.cookies
    assert verify(browser, challenge, code).status_code == 200
    # The CSRF exemption covers exactly the two login paths: a signed-in browser
    # without a CSRF token is refused everywhere else.
    for path in ('/api/account/email/start/', '/api/account/email', '/api/account/logout', '/api/account/sessions/revoke-all'):
        refused = browser.post(path, json={'email': EMAIL}, headers={'Origin': ORIGIN})
        assert refused.status_code == 403 and refused.json()['detail']['code'] == 'csrf_invalid'
    assert browser.get('/api/account/me').status_code == 200


def test_provider_state_when_email_delivery_is_not_configured(sessions):  # noqa: F811
    browser = client()
    assert browser.get('/api/account/providers').json() == {'email': False, 'wechat': False}
    response = start(browser)
    assert response.status_code == 503 and response.json()['detail']['code'] == 'login_provider_not_configured'


def test_login_replaces_pre_login_session_and_revoke_all_signs_out_everywhere(email_login):
    repo, clock, mailbox, _ = email_login
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    old = browser.cookies.get(COOKIE_NAME)
    clock.advance(minutes=2)
    challenge = start(browser).json()['challenge_id']
    assert verify(browser, challenge, mailbox.latest(EMAIL).code).status_code == 200
    with pytest.raises(InvalidAuthentication):
        repo.verify(old)
    current = browser.cookies.get(COOKIE_NAME)
    clock.advance(minutes=2)
    other, _ = login(mailbox)
    assert client().get('/api/account/providers').json() == {'email': True, 'wechat': False}
    assert browser.post('/api/account/sessions/revoke-all', headers={'Origin': ORIGIN}).status_code == 403
    assert browser.post('/api/account/sessions/revoke-all',
                        headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf_token(current)}).status_code == 204
    assert other.get('/api/account/me').status_code == 401
    assert browser.get('/api/account/me').status_code == 401
    with psycopg.connect(repo.dsn) as connection:
        assert connection.execute('SELECT count(*) FROM cloud_browser_sessions WHERE owner_user_id=%s AND revoked_at IS NULL',
                                  (owner,)).fetchone()[0] == 0


def test_concurrent_verification_consumes_once_and_creates_one_account(email_login):
    repo, clock, mailbox, service = email_login
    first, second = new_client_binding(), new_client_binding()
    a = service.start(EMAIL, first).challenge_id
    code_a = mailbox.latest(EMAIL).code
    clock.advance(seconds=61)
    b = service.start(EMAIL, second).challenge_id
    code_b = mailbox.latest(EMAIL).code
    attempts = [(a, code_a, first), (b, code_b, second)] * 3

    def attempt(args):
        try:
            return service.verify(*args)
        except EmailLoginRejected:
            return None
    with ThreadPoolExecutor(6) as pool:
        results = [result for result in pool.map(attempt, attempts) if result is not None]
    assert len(results) == 2 and len({result.owner_user_id for result in results}) == 1
    assert sorted(result.created_account for result in results) == [False, True]
    with psycopg.connect(repo.dsn) as connection:
        assert connection.execute("SELECT count(*) FROM cloud_users WHERE user_id LIKE 'u\\_%'").fetchone()[0] == 1


def test_concurrent_wrong_guesses_are_all_counted(email_login):
    repo, _, mailbox, service = email_login
    binding = new_client_binding()
    challenge = service.start(EMAIL, binding).challenge_id
    code = mailbox.latest(EMAIL).code

    def guess(_):
        with pytest.raises(EmailLoginRejected):
            service.verify(challenge, wrong(code), binding)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(guess, range(8)))
    with psycopg.connect(repo.dsn) as connection:
        failed, closed = connection.execute('SELECT failed_attempts, closed_at FROM cloud_email_login_challenges').fetchone()
    assert failed == 5 and closed is not None
    with pytest.raises(EmailLoginRejected):
        service.verify(challenge, code, binding)


def test_code_is_bound_to_the_requesting_browser(email_login):
    repo, clock, mailbox, service = email_login
    victim = client()
    challenge = start(victim).json()['challenge_id']
    code = mailbox.latest(EMAIL).code
    attacker = client()
    clock.advance(seconds=61)
    assert start(attacker).status_code == 202  # does not supersede the victim's code
    stolen = attacker.post('/api/account/email/verify', json={'challenge_id': challenge, 'code': code},
                           headers={'Origin': ORIGIN})
    assert stolen.status_code == 400 and COOKIE_NAME not in attacker.cookies
    with psycopg.connect(repo.dsn) as connection:
        assert connection.execute('SELECT failed_attempts FROM cloud_email_login_challenges WHERE challenge_id=%s',
                                  (challenge,)).fetchone()[0] == 0
    with pytest.raises(EmailLoginRejected):
        service.verify(challenge, code, new_client_binding())  # well-formed, but not the victim's cookie
    assert verify(victim, challenge, code).status_code == 200


def test_daily_cap_also_stops_guessing_on_codes_issued_earlier(email_login):
    repo, clock, mailbox, service = email_login
    issued = []
    for _ in range(5):  # one fresh browser per code: nothing supersedes anything
        binding = new_client_binding()
        challenge = service.start(EMAIL, binding).challenge_id
        issued.append((challenge, mailbox.latest(EMAIL).code, binding))
        clock.advance(seconds=61)
    for challenge, code, binding in issued:
        for _ in range(5):
            with pytest.raises(EmailLoginRejected):
                service.verify(challenge, wrong(code), binding)
    challenge, code, binding = issued[-1]
    with pytest.raises(EmailLoginRejected):
        service.verify(challenge, code, binding)  # even the right code, once the address is capped
    with psycopg.connect(repo.dsn) as connection:
        assert connection.execute('SELECT sum(failed_attempts) FROM cloud_email_login_challenges').fetchone()[0] == 10
        first_failure = connection.execute('SELECT min(created_at) FROM cloud_email_login_challenges').fetchone()[0]
    assert first_failure is not None
    clock.advance(hours=1, seconds=1)
    service.start(EMAIL, new_client_binding())
    assert len(mailbox.latest(EMAIL).code) == 12  # capped: only an unguessable code is offered


def test_capped_address_gets_an_unguessable_code_instead_of_a_lockout(email_login):
    _, clock, mailbox, service = email_login
    attacker = new_client_binding()
    for _ in range(2):  # an attacker spends the address's daily failures with their own codes
        challenge = service.start(EMAIL, attacker).challenge_id
        code = mailbox.latest(EMAIL).code
        for _ in range(5):
            with pytest.raises(EmailLoginRejected):
                service.verify(challenge, wrong(code), attacker)
        clock.advance(hours=1, seconds=1)
    guessed = service.start(EMAIL, attacker).challenge_id
    assert len(mailbox.latest(EMAIL).code) == 12
    for guess in ('000000', 'AAAAAAAAAAAA', '222222222222', 'ZZZZ-ZZZZ-ZZZZ', 'abcdefghjkmn'):
        with pytest.raises(EmailLoginRejected):
            service.verify(guessed, guess, attacker)
    clock.advance(seconds=61)
    learner = client()
    started = start(learner)
    assert started.status_code == 202  # no 24 h lockout for the learner
    code = mailbox.latest(EMAIL).code
    assert len(code) == 12 and code.isalnum() and code.isupper()
    typed = '-'.join(code[i:i + 4] for i in range(0, 12, 4)).lower()
    assert verify(learner, started.json()['challenge_id'], typed).status_code == 200
    clock.advance(days=1, hours=1)
    service.start(EMAIL, new_client_binding())
    assert len(mailbox.latest(EMAIL).code) == 6  # back to six digits once failures leave the window


def test_failed_pre_login_revocation_issues_no_session(email_login):
    repo, clock, mailbox, _ = email_login
    browser, _ = login(mailbox)
    old = browser.cookies.get(COOKIE_NAME)
    clock.advance(minutes=2)
    challenge = start(browser).json()['challenge_id']
    with psycopg.connect(repo.dsn) as connection:
        connection.execute('ALTER TABLE cloud_browser_sessions ADD CONSTRAINT inject_no_revoke CHECK(revoked_at IS NULL) NOT VALID')
    try:
        failed = verify(browser, challenge, mailbox.latest(EMAIL).code)
    finally:
        with psycopg.connect(repo.dsn) as connection:
            connection.execute('ALTER TABLE cloud_browser_sessions DROP CONSTRAINT inject_no_revoke')
    assert failed.status_code == 503 and browser.cookies.get(COOKIE_NAME) == old
    with psycopg.connect(repo.dsn) as connection:
        assert connection.execute('SELECT count(*) FROM cloud_browser_sessions').fetchone()[0] == 1
        assert connection.execute('SELECT closed_at FROM cloud_email_login_challenges WHERE challenge_id=%s',
                                  (challenge,)).fetchone()[0] is None  # the code can be retried
    assert verify(browser, challenge, mailbox.latest(EMAIL).code).status_code == 200
    with pytest.raises(InvalidAuthentication):
        repo.verify(old)


def test_codes_are_keyed_digests_not_plain_hashes(email_login):
    repo, _, mailbox, service = email_login
    import hashlib
    binding = new_client_binding()
    challenge = service.start(EMAIL, binding).challenge_id
    code = mailbox.latest(EMAIL).code
    with psycopg.connect(repo.dsn) as connection:
        stored = connection.execute('SELECT code_hash FROM cloud_email_login_challenges').fetchone()[0]
    for prefix in ('language-ai-email-code-v1', 'language-ai-email-code-v2'):
        assert stored != hashlib.sha256(f'{prefix}:{challenge}:{code}'.encode()).hexdigest()
    other_key = EmailLoginService(repo, mailbox, code_key=bytes(32))
    with pytest.raises(EmailLoginRejected):
        other_key.verify(challenge, code, binding)  # a database copy without the key cannot check guesses
    assert service.verify(challenge, code, binding).owner_user_id
    with pytest.raises(ValueError):
        EmailLoginService(repo, mailbox, code_key=b'short')


def test_delivery_failure_closes_challenge_and_storage_failure_issues_nothing(email_login):
    repo, clock, mailbox, _ = email_login

    class BrokenMailer:
        def send_login_code(self, **_):
            raise OSError('smtp down')
    binding = new_client_binding()
    service = EmailLoginService(repo, mailbox, code_key=CODE_KEY)
    kept = service.start(EMAIL, binding).challenge_id
    clock.advance(seconds=61)
    broken = EmailLoginService(repo, BrokenMailer(), code_key=CODE_KEY)
    with pytest.raises(SessionUnavailable):
        broken.start(EMAIL, binding)
    with psycopg.connect(repo.dsn) as connection:
        open_ids = [str(row[0]) for row in connection.execute(
            'SELECT challenge_id FROM cloud_email_login_challenges WHERE closed_at IS NULL')]
    assert open_ids == [kept]  # the code already in the inbox still works

    clock.advance(minutes=2)
    challenge = service.start(EMAIL, binding).challenge_id
    with psycopg.connect(repo.dsn) as connection:
        connection.execute('ALTER TABLE cloud_browser_sessions ADD CONSTRAINT inject_no_session CHECK(false) NOT VALID')
    with pytest.raises(SessionUnavailable):
        service.verify(challenge, mailbox.latest(EMAIL).code, binding)
    with psycopg.connect(repo.dsn) as connection:
        assert connection.execute('SELECT count(*) FROM cloud_external_identities').fetchone()[0] == 0
        assert connection.execute('SELECT closed_at FROM cloud_email_login_challenges WHERE challenge_id=%s',
                                  (challenge,)).fetchone()[0] is None


def test_minimum_privilege_auth_role_can_log_in_without_learning_data(email_login):
    repo, _, mailbox, _ = email_login
    from psycopg.conninfo import make_conninfo
    with psycopg.connect(repo.dsn) as connection:
        connection.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='email_auth_test') THEN "
                           "CREATE ROLE email_auth_test LOGIN PASSWORD 'synthetic-test' NOSUPERUSER NOBYPASSRLS; END IF; END $$")
        connection.execute('GRANT USAGE ON SCHEMA public TO email_auth_test')
        connection.execute('GRANT SELECT,INSERT,UPDATE ON cloud_browser_sessions, cloud_email_login_challenges, '
                           'cloud_external_identities TO email_auth_test')
        connection.execute('GRANT INSERT ON cloud_users TO email_auth_test')
    runtime = BrowserSessionRepository(make_conninfo(repo.dsn, user='email_auth_test', password='synthetic-test'), repo.clock)
    service = EmailLoginService(runtime, mailbox, code_key=CODE_KEY)
    binding = new_client_binding()
    login_result = service.verify(service.start(EMAIL, binding).challenge_id, mailbox.latest(EMAIL).code, binding)
    assert runtime.verify(login_result.session_token).user_id == login_result.owner_user_id
    assert service.email_for(login_result.owner_user_id) == EMAIL
    with runtime._connect() as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute('SELECT * FROM cloud_study_items')


def test_database_rejects_unnormalized_subjects(email_login):
    repo, *_ = email_login
    with psycopg.connect(repo.dsn) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute("INSERT INTO cloud_email_login_challenges(challenge_id,email,client_binding,code_hash,created_at,expires_at) "
                               "VALUES(gen_random_uuid(),'Upper@example.test',repeat('a',64),repeat('a',64),now(),now()+interval '1 minute')")


@pytest.mark.parametrize('value', ['', 'plain', 'a@b', 'a b@example.test', 'ünï@example.test', 'a@-bad.test',
                                   'x' * 65 + '@example.test', None, 7])
def test_normalize_email_rejects(value):
    with pytest.raises(InvalidEmailAddress):
        normalize_email(value)


def test_normalize_email_folds_case_and_whitespace_only():
    assert normalize_email('  First.Last+Tag@Example.TEST ') == 'first.last+tag@example.test'


def test_sandbox_directory_delivery_is_local_only(tmp_path, monkeypatch):
    monkeypatch.delenv('CLOUD_EMAIL_DELIVERY', raising=False)
    assert mailer_from_environment('https://learn.example') is None
    monkeypatch.setenv('CLOUD_EMAIL_DELIVERY', 'smtp')
    with pytest.raises(SessionUnavailable):
        mailer_from_environment('https://localhost')
    monkeypatch.setenv('CLOUD_EMAIL_DELIVERY', 'sandbox-directory')
    monkeypatch.setenv('CLOUD_EMAIL_SANDBOX_DIR', str(tmp_path / 'outbox'))
    monkeypatch.delenv('LANGUAGE_AI_STAGING', raising=False)
    with pytest.raises(SessionUnavailable):
        mailer_from_environment('https://localhost')
    monkeypatch.setenv('LANGUAGE_AI_STAGING', '1')
    with pytest.raises(SessionUnavailable):
        mailer_from_environment('https://learn.example')
    mailer = mailer_from_environment('https://app.localhost:8443')
    assert isinstance(mailer, DirectoryMailbox)
    from datetime import datetime, timezone
    mailer.send_login_code(email=EMAIL, code='123456', expires_at=datetime(2026, 11, 3, tzinfo=timezone.utc))
    [written] = (tmp_path / 'outbox').iterdir()
    assert written.stat().st_mode & 0o777 == 0o600 and 'Code: 123456' in written.read_text()
    (tmp_path / 'outbox').chmod(0o755)
    with pytest.raises(OSError):
        mailer.send_login_code(email=EMAIL, code='123456', expires_at=datetime(2026, 11, 3, tzinfo=timezone.utc))
    monkeypatch.setenv('CLOUD_EMAIL_SANDBOX_DIR', 'relative/outbox')
    with pytest.raises(SessionUnavailable):
        mailer_from_environment('https://localhost')


def test_personal_desktop_exposes_no_cloud_login(monkeypatch):
    monkeypatch.setenv('LANGUAGE_AI_DEPLOYMENT_MODE', 'personal')
    local = TestClient(app_module.app)
    assert local.get('/api/account/providers').json() == {'email': False, 'wechat': False}
    assert local.post('/api/account/email/start', json={'email': EMAIL}).status_code == 503
    assert local.post('/api/account/email/verify', json={'challenge_id': '0' * 36, 'code': '123456'}).status_code == 503
    assert local.post('/api/account/sessions/revoke-all').status_code == 503
    assert local.get('/api/deployment').json() == {'mode': 'personal'}
    assert LOGIN_COOKIE_NAME not in local.cookies and COOKIE_NAME not in local.cookies
