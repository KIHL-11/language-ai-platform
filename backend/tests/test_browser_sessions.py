from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.auth.browser import configure_browser_sessions
from services.auth.sessions import BrowserSessionRepository, SessionPolicy, COOKIE_NAME, csrf_token, SessionUnavailable
from services.auth.identity import InvalidAuthentication
from test_durable_ai_chunk_candidates import cloud_candidates, _migration, OWNER_A, OWNER_B, _manual, _service
from test_durable_ai_chunk_candidates import _lesson
from services.topics.postgres import PostgresTopicRepository


@pytest.fixture
def sessions(cloud_candidates,monkeypatch):
    dsn,clock,study,library,candidates=cloud_candidates
    _migration(dsn,'0004_owner_scoped_speaking.down.sql')
    _migration(dsn,'0004_owner_scoped_speaking.up.sql')
    _migration(dsn,'0005_browser_sessions.down.sql')
    _migration(dsn,'0005_browser_sessions.up.sql')
    names=('browser_session_repository','browser_session_policy','identity_verifier','cloud_library_repository','cloud_study_bank_repository','cloud_ai_chunk_repository','cloud_topic_repository')
    previous={name:getattr(app_module.app.state,name,None) for name in names}
    repo=BrowserSessionRepository(dsn,clock)
    configure_browser_sessions(app_module.app,repo,SessionPolicy('https://learn.example'))
    app_module.app.state.cloud_library_repository=library
    app_module.app.state.cloud_study_bank_repository=study
    app_module.app.state.cloud_ai_chunk_repository=candidates
    app_module.app.state.cloud_topic_repository=PostgresTopicRepository(dsn,clock)
    monkeypatch.setenv('LANGUAGE_AI_DEPLOYMENT_MODE','cloud')
    try: yield repo,clock,study,library,candidates
    finally:
        for name,value in previous.items():
            if value is None:
                if hasattr(app_module.app.state,name): delattr(app_module.app.state,name)
            else: setattr(app_module.app.state,name,value)
        _migration(dsn,'0005_browser_sessions.down.sql')
        _migration(dsn,'0004_owner_scoped_speaking.down.sql')


def browser(repo,owner):
    token=repo.issue_for_verified_account(owner)
    client=TestClient(app_module.app,base_url='https://learn.example')
    client.cookies.set(COOKIE_NAME,token,domain='learn.example',path='/')
    return client,token


def headers(token): return {'Origin':'https://learn.example','X-CSRF-Token':csrf_token(token)}


def test_hashed_durable_revocation_expiry_and_rotation(sessions):
    repo,clock,*_=sessions
    token=repo.issue_for_verified_account(OWNER_A)
    with psycopg.connect(repo.dsn) as connection:
        row=connection.execute('SELECT token_hash FROM cloud_browser_sessions').fetchone()
        assert row[0]!=token and len(row[0])==64
    assert BrowserSessionRepository(repo.dsn,clock).verify(token).user_id==OWNER_A
    rotated=repo.rotate(token)
    with pytest.raises(InvalidAuthentication): repo.verify(token)
    assert repo.verify(rotated).user_id==OWNER_A
    repo.revoke_all(OWNER_A)
    with pytest.raises(InvalidAuthentication): repo.verify(rotated)
    idle=repo.issue_for_verified_account(OWNER_B)
    clock.advance(minutes=31)
    with pytest.raises(InvalidAuthentication): repo.verify(idle)


def test_cookie_csrf_rotation_logout(sessions):
    repo,*_=sessions
    client,token=browser(repo,OWNER_A)
    assert client.get('/api/account/me').json()['user_id']==OWNER_A
    assert client.post('/api/account/session/rotate').status_code==403
    assert client.post('/api/account/session/rotate',headers={'Origin':'https://evil.example','X-CSRF-Token':csrf_token(token)}).status_code==403
    rotated=client.post('/api/account/session/rotate',headers=headers(token))
    assert rotated.status_code==200
    cookie=rotated.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie and 'Domain=' not in cookie
    fresh=client.cookies.get(COOKIE_NAME)
    assert fresh!=token
    assert client.post('/api/account/logout',headers=headers(token)).status_code==403
    assert client.post('/api/account/logout',headers=headers(fresh)).status_code==204
    assert client.get('/api/account/me').status_code==401


def test_authentication_and_origin_bypass_rejected(sessions):
    repo,*_=sessions
    client,token=browser(repo,OWNER_A)
    assert client.post('/api/account/session/rotate',headers={'Origin':'null','X-CSRF-Token':csrf_token(token)}).status_code==403
    assert client.post('/api/account/session/rotate',headers={**headers(token),'Sec-Fetch-Site':'cross-site'}).status_code==403
    insecure=TestClient(app_module.app,base_url='http://learn.example')
    assert insecure.get('/api/account/me',headers={'X-Forwarded-Proto':'https'}).status_code==403
    anonymous=TestClient(app_module.app,base_url='https://learn.example')
    assert anonymous.get('/api/account/me',headers={'X-User-Id':OWNER_A,'Authorization':'Bearer unsigned'}).status_code==401
    assert anonymous.post('/api/account/login',json={'user_id':OWNER_A}).status_code==503
    assert client.post('/api/speaking/gemini/client-token',headers=headers(token)).status_code==503


def test_real_session_tenant_routes(sessions):
    repo,clock,study,library,candidates=sessions
    service=_service(clock,study,library,candidates)
    batch=service.suggest(OWNER_A,_manual())
    a,token_a=browser(repo,OWNER_A)
    b,token_b=browser(repo,OWNER_B)
    assert a.get(f'/api/ai/chunk-suggestions/{batch.batch_id}',params={'user_id':OWNER_A}).status_code==200
    assert b.get(f'/api/ai/chunk-suggestions/{batch.batch_id}',params={'user_id':OWNER_B}).status_code==404
    assert b.get('/api/study-bank/items',params={'user_id':OWNER_A}).status_code==403
    assert a.get('/api/review/queue',params={'user_id':OWNER_A}).status_code==200
    assert b.get('/api/library/items').status_code==200
    assert b.post('/api/topics/en-friends-reunion/preview',json={'user_id':OWNER_B},headers=headers(token_b)).status_code==200
    item=library.create_for_owner(OWNER_A,_lesson())
    assert b.get(f'/api/library/items/{item.id}').status_code==404
    created=a.post('/api/topics/en-friends-reunion/attempts',json={'user_id':OWNER_A,'topic_version':1,'target_ids':[],'idempotency_key':'browser-topic-001'},headers=headers(token_a))
    assert created.status_code==200
    sid=created.json()['session_id']
    assert b.get(f'/api/topics/en-friends-reunion/attempts/{sid}',params={'user_id':OWNER_B}).status_code==404


def test_deployment_metadata_and_private_response_identity_binding(sessions):
    repo,*_=sessions
    client,token=browser(repo,OWNER_A)
    config=client.get('/api/deployment')
    assert config.json()=={'mode':'cloud'}
    assert config.headers['cache-control']=='no-store'
    assert client.get('/api/library/items').headers['x-authenticated-user']==OWNER_A
    token_b=repo.issue_for_verified_account(OWNER_B)
    client.cookies.set(COOKIE_NAME,token_b,domain='learn.example',path='/')
    response=client.get('/api/library/items')
    assert response.status_code==200
    assert response.headers['x-authenticated-user']==OWNER_B


def test_concurrent_rotation_and_cloud_cors(sessions):
    from concurrent.futures import ThreadPoolExecutor
    repo,*_=sessions
    token=repo.issue_for_verified_account(OWNER_A)
    def rotate(_):
        try: return repo.rotate(token)
        except InvalidAuthentication: return None
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(rotate,range(2)))
    assert sum(result is not None for result in results)==1
    client=TestClient(app_module.app,base_url='https://learn.example')
    response=client.options('/api/account/logout',headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'POST'})
    assert response.status_code==403
    assert 'access-control-allow-origin' not in response.headers


def test_unknown_account_and_transaction_failure(sessions):
    repo,*_=sessions
    with pytest.raises(SessionUnavailable): repo.issue_for_verified_account('browser-generated-unowned-id')
    token=repo.issue_for_verified_account(OWNER_A)
    with psycopg.connect(repo.dsn) as connection:
        connection.execute("ALTER TABLE cloud_browser_sessions ADD CONSTRAINT inject_no_new_session CHECK(revoked_at IS NULL)")
    with pytest.raises(SessionUnavailable): repo.rotate(token)
    assert repo.verify(token).user_id==OWNER_A


def test_absolute_expiry_and_session_fixation(sessions):
    repo,clock,*_=sessions
    token=repo.issue_for_verified_account(OWNER_A)
    for _ in range(24):
        clock.advance(minutes=29)
        repo.verify(token)
    clock.advance(minutes=24)
    with pytest.raises(InvalidAuthentication): repo.verify(token)
    with pytest.raises(InvalidAuthentication): repo.verify('a'*43)


def test_minimum_privilege_authentication_role(sessions):
    repo,*_=sessions
    from psycopg.conninfo import make_conninfo
    with psycopg.connect(repo.dsn) as connection:
        connection.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='browser_auth_test') THEN CREATE ROLE browser_auth_test LOGIN PASSWORD 'synthetic-test' NOSUPERUSER NOBYPASSRLS; END IF; END $$")
        connection.execute('GRANT USAGE ON SCHEMA public TO browser_auth_test')
        connection.execute('GRANT SELECT,INSERT,UPDATE ON cloud_browser_sessions TO browser_auth_test')
    runtime=BrowserSessionRepository(make_conninfo(repo.dsn,user='browser_auth_test',password='synthetic-test'),repo.clock)
    token=runtime.issue_for_verified_account(OWNER_A)
    assert runtime.verify(token).user_id==OWNER_A
    rotated=runtime.rotate(token)
    runtime.revoke(rotated)
    with pytest.raises(InvalidAuthentication): runtime.verify(rotated)
    with runtime._connect() as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege): connection.execute('SELECT * FROM cloud_study_items')


@pytest.mark.parametrize('origin',['http://learn.example','https://learn.example/','https://user@learn.example','https://learn.example/path','null'])
def test_invalid_policy(origin):
    with pytest.raises(ValueError): SessionPolicy(origin)
