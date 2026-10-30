BEGIN;
CREATE TABLE cloud_topic_sessions (
 owner_user_id VARCHAR(128) NOT NULL REFERENCES cloud_users(user_id) ON DELETE CASCADE,
 session_id UUID NOT NULL,
 topic_id VARCHAR(100) NOT NULL,
 topic_version INTEGER NOT NULL CHECK(topic_version > 0),
 event_count INTEGER NOT NULL DEFAULT 0 CHECK(event_count BETWEEN 0 AND 41),
 status VARCHAR(16) NOT NULL CHECK(status IN ('active','completed','interrupted','failed')),
 session_json JSONB NOT NULL CHECK(jsonb_typeof(session_json)='object'),
 feedback_json JSONB CHECK(feedback_json IS NULL OR jsonb_typeof(feedback_json)='object'),
 created_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(owner_user_id,session_id),
 CHECK(session_json->>'user_id'=owner_user_id),
 CHECK(session_json->>'id'=session_id::text),
 CHECK(session_json->>'provider'='topic-mock'),
 CHECK(session_json->>'status'=status),
 CHECK((session_json->>'event_count')::integer=event_count),
 CHECK(octet_length(session_json::text) <= 300000),
 CHECK(octet_length(feedback_json::text) <= 100000)
);
CREATE INDEX cloud_topic_recent ON cloud_topic_sessions(owner_user_id,created_at DESC,session_id);
CREATE TABLE cloud_topic_targets (
 owner_user_id VARCHAR(128) NOT NULL,
 session_id UUID NOT NULL,
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 4),
 study_item_id UUID,
 snapshot JSONB NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
 PRIMARY KEY(owner_user_id,session_id,ordinal),
 FOREIGN KEY(owner_user_id,session_id) REFERENCES cloud_topic_sessions(owner_user_id,session_id) ON DELETE CASCADE,
 FOREIGN KEY(owner_user_id,study_item_id) REFERENCES cloud_study_items(owner_user_id,item_id) ON DELETE SET NULL(study_item_id)
);
CREATE TABLE cloud_topic_requests (
 owner_user_id VARCHAR(128) NOT NULL REFERENCES cloud_users(user_id) ON DELETE CASCADE,
 request_key VARCHAR(128) NOT NULL,
 session_id UUID NOT NULL,
 fingerprint CHAR(64) NOT NULL,
 response_json JSONB NOT NULL,
 PRIMARY KEY(owner_user_id,request_key),
 FOREIGN KEY(owner_user_id,session_id) REFERENCES cloud_topic_sessions(owner_user_id,session_id) ON DELETE CASCADE,
 CHECK(octet_length(response_json::text) <= 400000)
);
ALTER TABLE cloud_topic_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_topic_sessions FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_topic_sessions_owner ON cloud_topic_sessions USING(owner_user_id=current_setting('app.current_owner',true)) WITH CHECK(owner_user_id=current_setting('app.current_owner',true));
ALTER TABLE cloud_topic_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_topic_targets FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_topic_targets_owner ON cloud_topic_targets USING(owner_user_id=current_setting('app.current_owner',true)) WITH CHECK(owner_user_id=current_setting('app.current_owner',true));
ALTER TABLE cloud_topic_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_topic_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_topic_requests_owner ON cloud_topic_requests USING(owner_user_id=current_setting('app.current_owner',true)) WITH CHECK(owner_user_id=current_setting('app.current_owner',true));
COMMIT;
