"""Durable owner-scoped mock topic attempts. Never accesses Personal JSON."""
import hashlib
import json
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from services.schemas.speaking import SpeakingSession, SpeakingTarget, SpeakingTurn, TargetUsage
from services.speaking.matching import record_target_usage
from services.speaking.store import SpeakingConflictError
from services.topics.catalog import get_topic
from services.topics.service import MOCK_MODEL, TOPIC_MARKER, TopicCurriculumService, TopicStorageUnavailable


class PostgresTopicRepository:
    def __init__(self, dsn, clock=lambda: datetime.now(timezone.utc)):
        self.dsn, self.clock = dsn, clock

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    @staticmethod
    def _owner(connection, owner):
        connection.execute("SELECT set_config('app.current_owner',%s,true)", (owner,))
        if not connection.execute("SELECT 1 FROM cloud_users WHERE user_id=%s", (owner,)).fetchone():
            raise TopicStorageUnavailable("Cloud account is not provisioned")

    @staticmethod
    def _topic(topic_id, version):
        topic = get_topic(topic_id)
        if topic.version != version:
            raise SpeakingConflictError("Topic version changed; reload the preview")
        return topic

    def _run(self, owner, key, payload, operation):
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        try:
            with self._connect() as connection:
                self._owner(connection, owner)
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"topic:{owner}:{key}",))
                prior = connection.execute("SELECT fingerprint,response_json FROM cloud_topic_requests WHERE owner_user_id=%s AND request_key=%s", (owner,key)).fetchone()
                if prior:
                    if prior['fingerprint'] != fingerprint:
                        raise SpeakingConflictError("Idempotency key payload changed")
                    return prior['response_json']
                session_id, result = operation(connection)
                connection.execute("INSERT INTO cloud_topic_requests(owner_user_id,request_key,session_id,fingerprint,response_json) VALUES(%s,%s,%s,%s,%s)", (owner,key,session_id,fingerprint,Jsonb(result)))
                return result
        except psycopg.Error as exc:
            raise TopicStorageUnavailable("Cloud topic storage is unavailable") from exc

    def start(self, owner, topic_id, version, target_ids, key):
        topic = self._topic(topic_id, version)
        def operation(connection):
            targets = []
            for item_id in dict.fromkeys(target_ids):
                try: uuid.UUID(item_id)
                except ValueError: raise KeyError(item_id)
                row = connection.execute("SELECT text,meaning FROM cloud_study_items WHERE owner_user_id=%s AND item_id=%s AND active AND kind='chunk' AND source_language=%s FOR SHARE", (owner,item_id,topic.language)).fetchone()
                if row is None: raise KeyError(item_id)
                targets.append(SpeakingTarget(study_item_id=item_id, text=row['text'], meaning=row['meaning']))
            now = self.clock()
            session = SpeakingSession(id=str(uuid.uuid4()), user_id=owner, language=topic.language, level=topic.level, mode='custom', provider='topic-mock', model=MOCK_MODEL, requested_duration_seconds=180, started_at=now, target_chunks=targets, support_words=[], target_usage=[TargetUsage(**t.model_dump()) for t in targets], goal=f'{TOPIC_MARKER}{topic_id}@{version} {topic.goal}')
            connection.execute("INSERT INTO cloud_topic_sessions(owner_user_id,session_id,topic_id,topic_version,status,session_json,created_at) VALUES(%s,%s,%s,%s,'active',%s,%s)", (owner,session.id,topic_id,version,Jsonb(session.model_dump(mode='json')),now))
            for ordinal,target in enumerate(targets):
                connection.execute("INSERT INTO cloud_topic_targets VALUES(%s,%s,%s,%s,%s)", (owner,session.id,ordinal,target.study_item_id,Jsonb(target.model_dump())))
            return session.id, self._result(session,topic)
        return self._run(owner,key,dict(action='start',topic=topic_id,version=version,targets=target_ids),operation)

    @staticmethod
    def _result(session, topic, feedback=None):
        prompt = topic.guided_prompts[min(session.learner_turn_count,len(topic.guided_prompts)-1)]
        return {'state': TopicCurriculumService._state(session,topic,'live' if session.status=='active' else 'saved',prompt if session.status=='active' else None).model_dump(mode='json'), 'turns':[t.model_dump(mode='json') for t in session.turns], 'feedback':feedback, 'status':session.status}

    def _load(self, connection, owner, topic_id, session_id):
        try: uuid.UUID(session_id)
        except ValueError: raise KeyError(session_id)
        row = connection.execute("SELECT * FROM cloud_topic_sessions WHERE owner_user_id=%s AND session_id=%s AND topic_id=%s FOR UPDATE", (owner,session_id,topic_id)).fetchone()
        if row is None: raise KeyError(session_id)
        return row, SpeakingSession.model_validate(row['session_json'])

    def mutate(self, owner, topic_id, session_id, version, expected, key, *, text=None, interrupted=False, finish=False):
        topic = self._topic(topic_id,version)
        def operation(connection):
            row,session = self._load(connection,owner,topic_id,session_id)
            if row['topic_version'] != version or session.status != 'active' or session.event_count != expected:
                raise SpeakingConflictError('Topic attempt state is stale')
            feedback = None
            if finish:
                session = session.model_copy(update={'status':'interrupted' if interrupted else 'completed','ended_at':self.clock(),'end_reason':'user','event_count':expected+1})
                feedback = TopicCurriculumService._feedback(topic,session,interrupted).model_dump(mode='json')
            else:
                text_clean = ' '.join(text.split())
                reply = topic.guided_prompts[min(session.learner_turn_count+1,len(topic.guided_prompts)-1)]
                if len(session.turns)+2 > 40 or sum(len(t.text) for t in session.turns)+len(text_clean)+len(reply)>30000:
                    raise ValueError('Topic transcript limit reached')
                sequence=len(session.turns)+1
                turns=[*session.turns,SpeakingTurn(sequence=sequence,role='learner',text=text_clean,created_at=self.clock()),SpeakingTurn(sequence=sequence+1,role='agent',text=reply,created_at=self.clock())]
                session=session.model_copy(update={'turns':turns,'event_count':expected+2,'learner_turn_count':session.learner_turn_count+1,'agent_turn_count':session.agent_turn_count+1,'target_usage':record_target_usage(session.target_usage,text_clean,sequence)})
            connection.execute("UPDATE cloud_topic_sessions SET session_json=%s,event_count=%s,status=%s,feedback_json=%s WHERE owner_user_id=%s AND session_id=%s", (Jsonb(session.model_dump(mode='json')),session.event_count,session.status,Jsonb(feedback) if feedback else None,owner,session_id))
            return session_id,self._result(session,topic,feedback)
        return self._run(owner,key,dict(action='finish' if finish else 'turn',topic=topic_id,session=session_id,version=version,expected=expected,text=text,interrupted=interrupted),operation)

    def get(self,owner,topic_id,session_id):
        try:
            with self._connect() as connection:
                self._owner(connection,owner)
                row,session=self._load(connection,owner,topic_id,session_id)
                topic=self._topic(topic_id,row['topic_version'])
                return self._result(session,topic,row['feedback_json'])
        except psycopg.Error as exc: raise TopicStorageUnavailable('Cloud topic storage is unavailable') from exc

    def list(self,owner,topic_id):
        get_topic(topic_id)
        try:
            with self._connect() as connection:
                self._owner(connection,owner)
                rows=connection.execute("SELECT session_id FROM cloud_topic_sessions WHERE owner_user_id=%s AND topic_id=%s ORDER BY created_at DESC,session_id LIMIT 10",(owner,topic_id)).fetchall()
                return [str(r['session_id']) for r in rows]
        except psycopg.Error as exc: raise TopicStorageUnavailable('Cloud topic storage is unavailable') from exc
