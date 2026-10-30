from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from test_durable_ai_chunk_candidates import cloud_candidates, _migration, _manual, _service, _decision, OWNER_A, OWNER_B
from test_durable_ai_chunk_candidates import _as_owner, client
import app as app_module
from services.topics.postgres import PostgresTopicRepository
from services.speaking.store import SpeakingConflictError
from services.topics.service import TopicStorageUnavailable
from services.schemas.study_bank import StudyItemPatch


@pytest.fixture
def repository(cloud_candidates):
    dsn,clock,study,library,candidates=cloud_candidates
    _migration(dsn,'0004_owner_scoped_speaking.down.sql')
    _migration(dsn,'0004_owner_scoped_speaking.up.sql')
    _migration(dsn,'0006_topic_session_json_integrity.up.sql')
    try:
        yield PostgresTopicRepository(dsn,clock), cloud_candidates
    finally:
        _migration(dsn,'0004_owner_scoped_speaking.down.sql')


def test_restart_retry_evidence_feedback_and_no_srs_mutation(repository):
    repo,(_,clock,study,library,candidates)=repository
    service=_service(clock,study,library,candidates)
    batch=service.suggest(OWNER_A,_manual())
    item=service.decide(OWNER_A,batch.batch_id,_decision(batch.candidates[0])).study_item
    before=study.get_item(OWNER_A,item.id)
    started=repo.start(OWNER_A,'en-friends-reunion',1,[item.id],'start-key-001')
    assert repo.start(OWNER_A,'en-friends-reunion',1,[item.id],'start-key-001')==started
    sid=started['state']['session_id']
    restarted=PostgresTopicRepository(repo.dsn,clock)
    turn=restarted.mutate(OWNER_A,'en-friends-reunion',sid,1,0,'turn-key-001',text=item.text)
    assert restarted.mutate(OWNER_A,'en-friends-reunion',sid,1,0,'turn-key-001',text=item.text)==turn
    assert turn['state']['target_usage'][0]['use_count']==1
    result=repo.mutate(OWNER_A,'en-friends-reunion',sid,1,2,'finish-key-001',finish=True,interrupted=True)
    assert result['status']=='interrupted'
    assert repo.get(OWNER_A,'en-friends-reunion',sid)['feedback']==result['feedback']
    assert study.get_item(OWNER_A,item.id)==before


def test_cross_tenant_and_concurrent_turns(repository):
    repo,_=repository
    sid=repo.start(OWNER_A,'de-friends-reunion',1,[],'start-key-001')['state']['session_id']
    assert repo.list(OWNER_B,'de-friends-reunion')==[]
    with pytest.raises(KeyError): repo.get(OWNER_B,'de-friends-reunion',sid)
    with pytest.raises(KeyError): repo.mutate(OWNER_B,'de-friends-reunion',sid,1,0,'evil-key-001',text='Hallo')
    def write(index):
        try: return repo.mutate(OWNER_A,'de-friends-reunion',sid,1,0,f'turn-key-{index:03}',text='Lass uns treffen.')
        except SpeakingConflictError: return None
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(write,range(2)))
    assert sum(result is not None for result in results)==1
    assert len(repo.get(OWNER_A,'de-friends-reunion',sid)['turns'])==2


def test_versions_and_conflicting_retries(repository):
    repo,_=repository
    with pytest.raises(SpeakingConflictError): repo.start(OWNER_A,'en-friends-reunion',99,[],'start-key-001')
    repo.start(OWNER_A,'en-friends-reunion',1,[],'start-key-001')
    with pytest.raises(SpeakingConflictError): repo.start(OWNER_A,'de-friends-reunion',1,[],'start-key-001')


@pytest.mark.parametrize('key',['id','user_id','provider','status','event_count'])
@pytest.mark.parametrize('operation',['missing','null'])
def test_direct_sql_cannot_remove_required_session_identity(repository,key,operation):
    repo,(dsn,*_)=repository
    sid=repo.start(OWNER_A,'en-friends-reunion',1,[],'start-key-001')['state']['session_id']
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(dsn) as connection:
            if operation=='missing':
                connection.execute('UPDATE cloud_topic_sessions SET session_json=session_json - %s WHERE owner_user_id=%s AND session_id=%s',(key,OWNER_A,sid))
            else:
                connection.execute("UPDATE cloud_topic_sessions SET session_json=jsonb_set(session_json,ARRAY[%s],'null'::jsonb) WHERE owner_user_id=%s AND session_id=%s",(key,OWNER_A,sid))
    assert repo.get(OWNER_A,'en-friends-reunion',sid)['state']['event_count']==0


def test_transaction_failure_rolls_back_exchange(repository):
    repo,(dsn,clock,study,library,candidates)=repository
    service=_service(clock,study,library,candidates)
    batch=service.suggest(OWNER_A,_manual())
    item=service.decide(OWNER_A,batch.batch_id,_decision(batch.candidates[0])).study_item
    sid=repo.start(OWNER_A,'en-friends-reunion',1,[],'start-key-001')['state']['session_id']
    with psycopg.connect(dsn) as connection:
        connection.execute("ALTER TABLE cloud_topic_requests ADD CONSTRAINT injected_failure CHECK(request_key NOT LIKE 'fail%')")
    with pytest.raises(TopicStorageUnavailable):
        repo.mutate(OWNER_A,'en-friends-reunion',sid,1,0,'fail-key-001',text='Recently I moved.')
    assert repo.get(OWNER_A,'en-friends-reunion',sid)['state']['event_count']==0


def test_targets_are_active_owned_snapshots_and_survive_deletion(repository):
    repo,(_,clock,study,library,candidates)=repository
    service=_service(clock,study,library,candidates)
    batch=service.suggest(OWNER_A,_manual())
    item=service.decide(OWNER_A,batch.batch_id,_decision(batch.candidates[0])).study_item
    with pytest.raises(KeyError): repo.start(OWNER_B,'en-friends-reunion',1,[item.id],'start-key-evil')
    started=repo.start(OWNER_A,'en-friends-reunion',1,[item.id],'start-key-001')
    archived=study.update_item(OWNER_A,item.id,StudyItemPatch(active=False,expected_revision=item.revision))
    with pytest.raises(KeyError): repo.start(OWNER_A,'en-friends-reunion',1,[item.id],'start-key-002')
    study.delete_item(OWNER_A,item.id,archived.revision)
    assert repo.get(OWNER_A,'en-friends-reunion',started['state']['session_id'])['state']['target_usage'][0]['text']==item.text


def test_runtime_nobypassrls_role_and_direct_rls(repository):
    repo,(dsn,clock,study,library,candidates)=repository
    service=_service(clock,study,library,candidates)
    batch=service.suggest(OWNER_A,_manual())
    item=service.decide(OWNER_A,batch.batch_id,_decision(batch.candidates[0])).study_item
    repo.start(OWNER_A,'en-friends-reunion',1,[item.id],'start-key-001')
    with psycopg.connect(dsn) as connection:
        connection.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='topic_runtime_test') THEN CREATE ROLE topic_runtime_test LOGIN PASSWORD 'synthetic-test' NOSUPERUSER NOBYPASSRLS; END IF; END $$")
        connection.execute('GRANT USAGE ON SCHEMA public TO topic_runtime_test')
        connection.execute('GRANT SELECT ON cloud_users,cloud_study_items TO topic_runtime_test')
        connection.execute('GRANT UPDATE ON cloud_study_items TO topic_runtime_test')
        connection.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON cloud_topic_sessions,cloud_topic_targets,cloud_topic_requests TO topic_runtime_test')
        connection.execute('SET ROLE topic_runtime_test')
        connection.execute("SELECT set_config('app.current_owner',%s,true)",(OWNER_B,))
        assert connection.execute('SELECT count(*) FROM cloud_topic_sessions').fetchone()[0]==0
        assert connection.execute('SELECT count(*) FROM cloud_topic_targets').fetchone()[0]==0
        assert connection.execute('SELECT count(*) FROM cloud_topic_requests').fetchone()[0]==0
    from psycopg.conninfo import make_conninfo
    runtime=PostgresTopicRepository(make_conninfo(dsn,user='topic_runtime_test',password='synthetic-test'))
    result=runtime.start(OWNER_B,'de-friends-reunion',1,[],'runtime-key-001')
    assert runtime.get(OWNER_B,'de-friends-reunion',result['state']['session_id'])['status']=='active'
    target_attempt=runtime.start(OWNER_A,'en-friends-reunion',1,[item.id],'runtime-target-001')
    sid=target_attempt['state']['session_id']
    runtime.mutate(OWNER_A,'en-friends-reunion',sid,1,0,'runtime-turn-001',text=item.text)
    done=runtime.mutate(OWNER_A,'en-friends-reunion',sid,1,2,'runtime-finish-001',finish=True)
    assert done['feedback']['target_usage'][0]['use_count']==1


def test_api_cloud_lifecycle_and_legacy_routes_remain_closed(repository):
    repo,(_,_,study,library,candidates)=repository
    app_module.app.state.cloud_topic_repository=repo
    try:
        with _as_owner(OWNER_A,candidates,study,library):
            start=client.post('/api/topics/en-friends-reunion/attempts',json={'user_id':OWNER_A,'target_ids':[],'topic_version':1,'idempotency_key':'api-start-001'})
            assert start.status_code==200
            sid=start.json()['session_id']
            assert client.get(f'/api/topics/en-friends-reunion/attempts/{sid}',params={'user_id':OWNER_A}).status_code==200
            assert client.get('/api/speaking/sessions',params={'user_id':OWNER_A}).status_code==503
            assert client.post('/api/speaking/gemini/client-token').status_code==503
        with _as_owner(OWNER_B,candidates,study,library):
            assert client.get(f'/api/topics/en-friends-reunion/attempts/{sid}',params={'user_id':OWNER_B}).status_code==404
    finally:
        delattr(app_module.app.state,'cloud_topic_repository')
