"""P8-05B3: adding and changing verified email addresses (sandbox mail only)."""
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

import app as app_module
from services.auth.identity import InvalidAuthentication
from services.auth.sessions import COOKIE_NAME
from test_account_identity import count, reauth, secured
from test_browser_sessions import sessions  # noqa: F401  (fixture)
from test_durable_ai_chunk_candidates import _migration, cloud_candidates  # noqa: F401  (fixture)
from test_email_login import EMAIL, ORIGIN, client, email_login, login, verify  # noqa: F401

NEW = 'learner.new@example.test'
OTHER = 'someone.else@example.test'


def claim_start(browser, email, replaces=None, confirm=None):
    body = {'email': email, 'replaces': replaces, 'confirm': confirm or ('change-email' if replaces else 'add-email')}
    return browser.post('/api/account/email/claim/start', json=body, headers=secured(browser))


def claim_verify(browser, challenge_id, code):
    return browser.post('/api/account/email/claim/verify', json={'challenge_id': challenge_id, 'code': code},
                        headers=secured(browser))


def claim(browser, mailbox, email, replaces=None):
    from services.auth.email_login import normalize_email
    started = claim_start(browser, email, replaces)
    assert started.status_code == 202, started.text
    return claim_verify(browser, started.json()['challenge_id'], mailbox.latest(normalize_email(email)).code)


def emails_of(repo, owner):
    with psycopg.connect(repo.dsn) as connection:
        return [row[0] for row in connection.execute(
            "SELECT subject FROM cloud_external_identities WHERE provider='email' AND owner_user_id=%s ORDER BY created_at, subject",
            (owner,))]


def test_add_a_second_address_that_signs_in_to_the_same_account(email_login):
    repo, clock, mailbox, _ = email_login
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    clock.advance(minutes=1)
    phone, _ = login(mailbox)
    clock.advance(minutes=1)
    added = claim(browser, mailbox, '  Learner.New@Example.TEST ')
    assert added.status_code == 200 and added.json() == {'result': 'added', 'notice': 'sent'}
    assert emails_of(repo, owner) == [EMAIL, NEW]
    assert mailbox.notices == [(EMAIL, 'email-added')]
    assert phone.get('/api/account/me').status_code == 200  # adding does not sign anyone out
    assert browser.get('/api/account/me').json()['email'] == EMAIL  # primary unchanged
    clock.advance(minutes=1)
    _, again = login(mailbox, NEW)
    assert again.json()['user_id'] == owner and again.json()['created_account'] is False
    listed = browser.get('/api/account/identities').json()['identities']
    assert [item['label'] for item in listed if item['provider'] == 'email'] == [EMAIL, NEW]
    # Either address can be used for the fresh-proof re-check.
    clock.advance(minutes=11)
    browser.get('/api/account/me')
    started = browser.post('/api/account/reauth/email/start', json={'email': NEW}, headers=secured(browser))
    assert started.status_code == 202 and started.json()['email'] == NEW
    foreign = browser.post('/api/account/reauth/email/start', json={'email': OTHER}, headers=secured(browser))
    assert foreign.status_code == 422


def test_claims_need_fresh_proof_confirmation_and_a_genuinely_new_address(email_login):
    repo, clock, mailbox, _ = email_login
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    assert claim_start(browser, NEW, confirm='yes').json()['detail']['code'] == 'confirmation_required'
    assert claim_start(browser, NEW, confirm='change-email').json()['detail']['code'] == 'confirmation_required'
    assert claim_start(browser, EMAIL.upper()).json()['detail']['code'] == 'email_already_yours'
    assert claim_start(browser, NEW, replaces=OTHER).json()['detail']['code'] == 'not_linked'
    assert claim_start(browser, 'not-an-address').status_code == 422
    clock.advance(seconds=1)
    assert browser.post('/api/account/email/claim/start', json={'email': NEW, 'confirm': 'add-email'},
                        headers={'Origin': ORIGIN}).status_code == 403  # CSRF
    assert claim(browser, mailbox, NEW).status_code == 200
    clock.advance(minutes=1)
    third = claim_start(browser, 'third@example.test')
    assert third.status_code == 422 and third.json()['detail']['code'] == 'email_limit'

    # The proof must still be fresh when the code is redeemed.
    clock.advance(minutes=1)
    started = claim_start(browser, 'swap@example.test', replaces=NEW)
    assert started.status_code == 202
    clock.advance(minutes=9)
    browser.get('/api/account/me')
    late = claim_verify(browser, started.json()['challenge_id'], mailbox.latest('swap@example.test').code)
    assert late.status_code == 428 and emails_of(repo, owner) == [EMAIL, NEW]
    clock.advance(minutes=2)
    browser.get('/api/account/me')
    assert claim_start(browser, 'swap@example.test', replaces=NEW).status_code == 428


def test_change_signs_out_other_browsers_and_frees_the_old_address(email_login):
    repo, clock, mailbox, _ = email_login
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    clock.advance(minutes=1)
    stolen, _ = login(mailbox)  # e.g. someone else who had the old mailbox
    clock.advance(minutes=1)
    pending = stolen.post('/api/account/reauth/email/start', headers=secured(stolen)).json()
    old_code = mailbox.latest(EMAIL).code
    clock.advance(minutes=1)
    current = browser.cookies.get(COOKIE_NAME)
    changed = claim(browser, mailbox, NEW, replaces=EMAIL)
    assert changed.status_code == 200 and changed.json() == {'result': 'changed', 'notice': 'sent'}
    assert emails_of(repo, owner) == [NEW]
    assert browser.cookies.get(COOKIE_NAME) == current and browser.get('/api/account/me').json()['email'] == NEW
    assert stolen.get('/api/account/me').status_code == 401
    assert count(repo.dsn, 'SELECT closed_at IS NOT NULL FROM cloud_email_login_challenges WHERE challenge_id=%s',
                 pending['challenge_id']) is True
    assert mailbox.notices == [(EMAIL, 'email-changed')]
    assert browser.get('/api/account/identities').json()['reauthenticated_for'] == 600  # the new address was just proven
    assert old_code
    clock.advance(minutes=1)
    _, old = login(mailbox, EMAIL)
    assert old.json()['user_id'] != owner and old.json()['created_account'] is True  # never reaches the account


def test_address_of_another_account_is_only_revealed_after_proof_and_never_moves(email_login):
    repo, clock, mailbox, _ = email_login
    _, other = login(mailbox, OTHER)
    other_owner = other.json()['user_id']
    clock.advance(minutes=1)
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    clock.advance(minutes=1)
    started = claim_start(browser, OTHER)
    assert started.status_code == 202 and set(started.json()) == {'challenge_id', 'expires_in', 'resend_after'}
    taken = claim_verify(browser, started.json()['challenge_id'], mailbox.latest(OTHER).code)
    assert taken.status_code == 409 and taken.json()['detail']['code'] == 'email_in_use'
    clock.advance(minutes=1)
    started = claim_start(browser, OTHER, replaces=EMAIL)
    assert claim_verify(browser, started.json()['challenge_id'], mailbox.latest(OTHER).code).status_code == 409
    assert emails_of(repo, owner) == [EMAIL] and emails_of(repo, other_owner) == [OTHER]
    assert mailbox.notices == []


def test_claim_codes_are_bound_to_the_requesting_session(email_login):
    repo, clock, mailbox, service = email_login
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    clock.advance(minutes=1)
    sibling, _ = login(mailbox)  # same account, other browser, also freshly proven
    clock.advance(minutes=1)
    _, intruder = login(mailbox, OTHER)
    started = claim_start(browser, NEW).json()
    code = mailbox.latest(NEW).code
    assert claim_verify(sibling, started['challenge_id'], code).status_code == 400
    with pytest.raises(Exception):
        service.verify(started['challenge_id'], code, browser.cookies.get(COOKIE_NAME))  # never a login code
    rotated = browser.post('/api/account/session/rotate', headers=secured(browser))
    assert rotated.status_code == 200
    assert claim_verify(browser, started['challenge_id'], code).status_code == 400  # rotation voids it
    assert emails_of(repo, owner) == [EMAIL] and count(
        repo.dsn, 'SELECT failed_attempts FROM cloud_email_login_challenges WHERE challenge_id=%s', started['challenge_id']) == 0
    assert intruder.json()['user_id'] != owner


def test_concurrent_claims_never_exceed_two_addresses_or_split_one_address(email_login):
    repo, clock, mailbox, service = email_login
    browsers = []
    for index in range(3):
        browser, verified = login(mailbox)
        browsers.append(browser)
        clock.advance(seconds=61)
    owner = verified.json()['user_id']
    attempts = []
    for index, browser in enumerate(browsers):
        address = f'parallel.{index}@example.test'
        started = claim_start(browser, address).json()
        attempts.append((browser, started['challenge_id'], mailbox.latest(address).code))
    with ThreadPoolExecutor(3) as pool:
        statuses = sorted(pool.map(lambda args: claim_verify(*args).status_code, attempts))
    assert statuses == [200, 422, 422] and len(emails_of(repo, owner)) == 2

    # A first login and a claim of the same address: exactly one account gets it.
    clock.advance(minutes=1)
    from services.auth.email_login import new_client_binding
    stranger = new_client_binding()
    login_id = service.start('contested@example.test', stranger).challenge_id
    login_code = mailbox.latest('contested@example.test').code
    clock.advance(seconds=61)
    target, _ = login(mailbox, 'solo@example.test')
    clock.advance(seconds=61)
    started = claim_start(target, 'contested@example.test').json()
    claim_code = mailbox.latest('contested@example.test').code

    def first_login():
        try:
            return service.verify(login_id, login_code, stranger).owner_user_id
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__
    with ThreadPoolExecutor(2) as pool:
        login_result = pool.submit(first_login)
        claimed = pool.submit(claim_verify, target, started['challenge_id'], claim_code)
        outcomes = (login_result.result(), claimed.result().status_code)
    assert count(repo.dsn, "SELECT count(*) FROM cloud_external_identities WHERE subject='contested@example.test'") == 1
    assert outcomes[1] in (200, 409)
    assert (outcomes[1] == 200) != outcomes[0].startswith('u_')


def test_failed_change_rolls_back_everything(email_login):
    repo, clock, mailbox, _ = email_login
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    clock.advance(minutes=1)
    other, _ = login(mailbox)
    clock.advance(minutes=1)
    started = claim_start(browser, NEW, replaces=EMAIL).json()
    code = mailbox.latest(NEW).code
    with psycopg.connect(repo.dsn) as connection:
        connection.execute('ALTER TABLE cloud_browser_sessions ADD CONSTRAINT inject_no_revoke CHECK(revoked_at IS NULL) NOT VALID')
    try:
        failed = claim_verify(browser, started['challenge_id'], code)
    finally:
        with psycopg.connect(repo.dsn) as connection:
            connection.execute('ALTER TABLE cloud_browser_sessions DROP CONSTRAINT inject_no_revoke')
    assert failed.status_code == 503
    assert emails_of(repo, owner) == [EMAIL] and other.get('/api/account/me').status_code == 200
    assert count(repo.dsn, 'SELECT closed_at FROM cloud_email_login_challenges WHERE challenge_id=%s', started['challenge_id']) is None
    assert mailbox.notices == []
    assert claim_verify(browser, started['challenge_id'], code).status_code == 200  # retry succeeds


def test_notice_failure_is_reported_and_logged_not_treated_as_sent(email_login, caplog):
    repo, clock, mailbox, service = email_login
    browser, verified = login(mailbox)
    clock.advance(seconds=1)

    def broken(**_):
        raise OSError('mail down')
    mailbox.send_security_notice = broken
    with caplog.at_level('WARNING', logger='services.auth.email_login'):
        result = claim(browser, mailbox, NEW)
    # The proven change stays (rolling it back would not make anyone safer), but
    # the learner and the operator are told the other address was not notified.
    assert result.status_code == 200 and result.json() == {'result': 'added', 'notice': 'failed'}
    assert 'Security notice delivery failed' in caplog.text and 'email-added' in caplog.text
    assert emails_of(repo, verified.json()['user_id']) == [EMAIL, NEW]
    del mailbox.send_security_notice
    no_adapter = type('NoNotices', (), {'send_login_code': mailbox.send_login_code})()
    service.mailer = no_adapter
    clock.advance(minutes=1)
    with caplog.at_level('WARNING', logger='services.auth.email_login'):
        swapped = claim(browser, mailbox, 'swap@example.test', replaces=NEW)
    assert swapped.json() == {'result': 'changed', 'notice': 'unavailable'} and 'no adapter' in caplog.text


def test_rollback_refuses_while_an_account_holds_two_addresses_and_otherwise_loses_nothing(email_login):
    repo, clock, mailbox, _ = email_login
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    clock.advance(seconds=1)
    assert claim(browser, mailbox, NEW).status_code == 200
    clock.advance(minutes=1)
    pending = claim_start(browser, 'never.finished@example.test', replaces=NEW).json()
    claim_verify(browser, pending['challenge_id'], '000000')  # one failed attempt to preserve

    def snapshot():
        with psycopg.connect(repo.dsn) as connection:
            return (connection.execute('SELECT * FROM cloud_external_identities ORDER BY subject').fetchall(),
                    connection.execute('SELECT challenge_id, email, failed_attempts FROM cloud_email_login_challenges '
                                       'ORDER BY challenge_id').fetchall())
    before = snapshot()
    with pytest.raises(psycopg.errors.RaiseException, match='1 accounts hold two email addresses'):
        _migration(repo.dsn, '0009_email_management.down.sql')
    assert snapshot() == before  # refused atomically: nothing changed, both addresses still sign in
    clock.advance(minutes=1)
    _, again = login(mailbox, NEW)
    assert again.json()['user_id'] == owner

    # One address per account (after a change) rolls back with no loss.
    with psycopg.connect(repo.dsn) as connection:  # synthetic: the learner chose to give up the second address
        connection.execute("DELETE FROM cloud_external_identities WHERE subject=%s", (NEW,))
    clock.advance(minutes=1)
    browser2, verified2 = login(mailbox, OTHER)
    clock.advance(seconds=1)
    assert claim(browser2, mailbox, 'other.changed@example.test', replaces=OTHER).status_code == 200
    identities, challenges = snapshot()
    _migration(repo.dsn, '0009_email_management.down.sql')
    after_identities, after_challenges = snapshot()
    assert after_identities == identities  # every verified identity kept, changed address included
    assert after_challenges == challenges  # every code row kept, failed attempts included
    assert count(repo.dsn, "SELECT count(*) FROM cloud_email_login_challenges WHERE closed_at IS NULL AND owner_user_id IS NOT NULL "
                 "AND challenge_id=%s", pending['challenge_id']) == 0
    with psycopg.connect(repo.dsn) as connection:
        assert connection.execute("SELECT to_regclass('cloud_external_identities_one_per_provider')").fetchone()[0]
        assert connection.execute("SELECT to_regclass('cloud_external_identities_owner')").fetchone()[0] is None  # as 0008 left it
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute("INSERT INTO cloud_external_identities VALUES('email','x2@example.test',%s,now(),now())", (owner,))
    assert count(repo.dsn, "SELECT owner_user_id FROM cloud_external_identities WHERE subject='other.changed@example.test'") \
        == verified2.json()['user_id']  # the 0008 schema maps the changed address to the same account
    _migration(repo.dsn, '0009_email_management.up.sql')
    clock.advance(minutes=1)
    assert claim(browser, mailbox, NEW).status_code == 200


def test_codes_and_sessions_from_before_a_change_cannot_restore_the_old_address(email_login):
    repo, clock, mailbox, service = email_login
    from services.auth.email_login import EmailLoginRejected, new_client_binding
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    clock.advance(minutes=1)
    old_browser, _ = login(mailbox)
    old_session = old_browser.cookies.get(COOKIE_NAME)
    clock.advance(minutes=1)
    elsewhere = new_client_binding()
    open_login = service.start(EMAIL, elsewhere).challenge_id  # unexpired login code in someone's inbox
    open_code = mailbox.latest(EMAIL).code
    clock.advance(minutes=1)
    assert claim(browser, mailbox, NEW, replaces=EMAIL).status_code == 200
    with pytest.raises(EmailLoginRejected):
        service.verify(open_login, open_code, elsewhere)  # closed by the change
    with pytest.raises(InvalidAuthentication):
        repo.verify(old_session)
    assert old_browser.get('/api/account/me').status_code == 401
    assert old_browser.post('/api/account/session/rotate', headers=secured(old_browser)).status_code in (401, 403)
    clock.advance(minutes=1)
    _, fresh = login(mailbox, EMAIL)  # a new code for the old address reaches a new, empty account only
    assert fresh.json()['user_id'] != owner and emails_of(repo, owner) == [NEW]
    assert count(repo.dsn, "SELECT count(*) FROM cloud_external_identities WHERE provider='email' AND owner_user_id=%s", owner) == 1


def test_concurrent_changes_of_the_only_address_keep_exactly_one(email_login):
    repo, clock, mailbox, _ = email_login
    browsers = []
    for _ in range(2):
        browser, verified = login(mailbox)
        browsers.append(browser)
        clock.advance(seconds=61)
    owner = verified.json()['user_id']
    attempts = []
    for index, browser in enumerate(browsers):
        address = f'replacement.{index}@example.test'
        started = claim_start(browser, address, replaces=EMAIL).json()
        attempts.append((browser, started['challenge_id'], mailbox.latest(address).code))
    with ThreadPoolExecutor(2) as pool:
        statuses = sorted(pool.map(lambda args: claim_verify(*args).status_code, attempts))
    # The winner signs the other browser out; the loser finds its address gone.
    assert statuses[0] == 200 and statuses[1] in (400, 401, 403, 422)
    assert len(emails_of(repo, owner)) == 1 and emails_of(repo, owner)[0].startswith('replacement.')


def test_email_management_is_unavailable_in_personal(email_login, monkeypatch):
    monkeypatch.setenv('LANGUAGE_AI_DEPLOYMENT_MODE', 'personal')
    personal = client()
    assert personal.post('/api/account/email/claim/start', json={'email': NEW, 'confirm': 'add-email'}).status_code == 503
    assert personal.post('/api/account/email/claim/verify',
                         json={'challenge_id': '0' * 8 + '-0000-4000-8000-' + '0' * 12, 'code': '123456'}).status_code == 503


def test_service_refuses_claims_without_a_valid_session(email_login):
    _, _, _, service = email_login
    with pytest.raises(InvalidAuthentication):
        service.start_claim('x' * 43, NEW, None, 'add-email')
    assert app_module.app.state.email_login_service is service


def test_session_purge_removes_only_dead_sessions_with_a_select_delete_role(email_login):
    """P8-05B3 task C: bounded purge on an isolated maintenance role."""
    from psycopg.conninfo import make_conninfo
    from services.auth.maintenance import PurgeAlreadyRunning, RUN_LOCK, purge_auth_records
    repo, clock, mailbox, _ = email_login
    with psycopg.connect(repo.dsn) as connection:
        connection.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='auth_maintenance_test') THEN "
                           "CREATE ROLE auth_maintenance_test LOGIN PASSWORD 'synthetic-test' NOSUPERUSER NOBYPASSRLS; END IF; END $$")
        connection.execute('GRANT USAGE ON SCHEMA public TO auth_maintenance_test')
        connection.execute('GRANT SELECT, DELETE ON cloud_browser_sessions, cloud_email_login_challenges, cloud_oauth_states '
                           'TO auth_maintenance_test')
    dsn = make_conninfo(repo.dsn, user='auth_maintenance_test', password='synthetic-test')

    revoked, _ = login(mailbox)
    old_token = revoked.cookies.get(COOKIE_NAME)
    assert revoked.post('/api/account/logout', headers=secured(revoked)).status_code == 204
    clock.advance(minutes=1)
    login(mailbox)  # idle: never used again
    clock.advance(hours=12)
    login(mailbox)  # will be idle-expired, but for less than the retention day
    clock.advance(hours=13)
    fresh, _ = login(mailbox)
    clock.advance(seconds=61)
    recent, _ = login(mailbox)
    assert recent.post('/api/account/logout', headers=secured(recent)).status_code == 204
    clock.advance(minutes=5)
    assert fresh.get('/api/account/me').status_code == 200

    now = clock()
    before = count(repo.dsn, 'SELECT count(*) FROM cloud_browser_sessions')
    assert before == 5
    assert purge_auth_records(dsn, now, batch=1, max_batches=1)['browser_sessions'] == 1  # bounded
    assert purge_auth_records(dsn, now, batch=50, max_batches=5)['browser_sessions'] == 1
    assert purge_auth_records(dsn, now)['browser_sessions'] == 0  # idempotent rerun
    assert count(repo.dsn, 'SELECT count(*) FROM cloud_browser_sessions') == 3
    assert fresh.get('/api/account/me').status_code == 200  # the active session is untouched
    with pytest.raises(InvalidAuthentication):
        repo.verify(old_token)
    clock.advance(days=2)
    purge_auth_records(dsn, clock())
    assert count(repo.dsn, 'SELECT count(*) FROM cloud_browser_sessions') == 0

    with psycopg.connect(dsn) as connection:
        for sql in ('SELECT * FROM cloud_study_items', 'UPDATE cloud_browser_sessions SET revoked_at=now()',
                    "INSERT INTO cloud_users(user_id,created_at) VALUES('u_x',now())"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(sql)
            connection.rollback()
    with psycopg.connect(repo.dsn, autocommit=True) as holder:
        holder.execute('SELECT pg_advisory_lock(hashtextextended(%s, 0))', (RUN_LOCK,))
        with pytest.raises(PurgeAlreadyRunning):
            purge_auth_records(dsn, clock())
        holder.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', (RUN_LOCK,))


def test_purge_cli_reports_failures_and_concurrent_runs(email_login, monkeypatch, capsys):
    from services.auth import maintenance
    repo, *_ = email_login
    monkeypatch.setenv('CLOUD_SESSION_DATABASE_URL', repo.dsn)
    assert maintenance.main(['--batch', '10']) == 0
    assert 'browser_sessions=0' in capsys.readouterr().out
    with psycopg.connect(repo.dsn, autocommit=True) as holder:
        holder.execute('SELECT pg_advisory_lock(hashtextextended(%s, 0))', (maintenance.RUN_LOCK,))
        assert maintenance.main([]) == 2
        holder.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', (maintenance.RUN_LOCK,))
    monkeypatch.setenv('CLOUD_SESSION_DATABASE_URL', 'postgresql://nobody@127.0.0.1:1/none')
    assert maintenance.main([]) == 1


def test_a_stale_change_of_an_address_already_replaced_changes_nothing(email_login):
    repo, clock, mailbox, _ = email_login
    browser, verified = login(mailbox)
    owner = verified.json()['user_id']
    clock.advance(seconds=1)
    first = claim_start(browser, 'first.choice@example.test', replaces=EMAIL).json()
    first_code = mailbox.latest('first.choice@example.test').code
    clock.advance(seconds=61)
    assert claim(browser, mailbox, 'second.choice@example.test', replaces=EMAIL).status_code == 200
    stale = claim_verify(browser, first['challenge_id'], first_code)
    assert stale.status_code == 422 and stale.json()['detail']['code'] == 'not_linked'
    assert emails_of(repo, owner) == ['second.choice@example.test']


def test_session_purge_retention_boundary(email_login):
    from datetime import timedelta
    from services.auth.maintenance import purge_auth_records
    repo, clock, mailbox, _ = email_login
    keep, _ = login(mailbox)
    clock.advance(seconds=61)
    drop, _ = login(mailbox)
    now = clock()
    with psycopg.connect(repo.dsn) as connection:
        for browser, idle_for in ((keep, timedelta(days=1, minutes=25)), (drop, timedelta(days=1, minutes=31))):
            # dead (idle) for just under / just over the one-day retention
            connection.execute('UPDATE cloud_browser_sessions SET created_at=%s, last_seen_at=%s WHERE token_hash=%s',
                               (now - idle_for, now - idle_for,
                                __import__('services.auth.sessions', fromlist=['digest']).digest(browser.cookies.get(COOKIE_NAME))))
    assert purge_auth_records(repo.dsn, now)['browser_sessions'] == 1
    assert count(repo.dsn, 'SELECT count(*) FROM cloud_browser_sessions') == 1
