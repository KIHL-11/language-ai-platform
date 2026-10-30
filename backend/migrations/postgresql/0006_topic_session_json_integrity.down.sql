BEGIN;
ALTER TABLE cloud_topic_sessions DROP CONSTRAINT IF EXISTS cloud_topic_json_identity_required;
COMMIT;
