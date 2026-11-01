BEGIN;
-- Missing keys and JSON null must not pass SQL CHECK's nullable semantics.
-- Validation intentionally fails on corrupt existing rows; never rewrite them.
ALTER TABLE cloud_topic_sessions ADD CONSTRAINT cloud_topic_json_identity_required
CHECK ((
 jsonb_typeof(session_json->'user_id')='string'
 AND session_json->>'user_id'=owner_user_id
 AND jsonb_typeof(session_json->'id')='string'
 AND session_json->>'id'=session_id::text
 AND jsonb_typeof(session_json->'provider')='string'
 AND session_json->>'provider'='topic-mock'
 AND jsonb_typeof(session_json->'status')='string'
 AND session_json->>'status'=status
 AND jsonb_typeof(session_json->'event_count')='number'
 AND session_json->'event_count'=to_jsonb(event_count)
) IS TRUE);
COMMIT;
