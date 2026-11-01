BEGIN;
CREATE TABLE cloud_browser_sessions (
 session_id UUID PRIMARY KEY,
 owner_user_id VARCHAR(128) NOT NULL REFERENCES cloud_users(user_id) ON DELETE CASCADE,
 token_hash CHAR(64) NOT NULL UNIQUE CHECK(token_hash ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 last_seen_at TIMESTAMPTZ NOT NULL,
 revoked_at TIMESTAMPTZ,
 CHECK(expires_at > created_at),
 CHECK(last_seen_at >= created_at)
);
CREATE INDEX cloud_browser_owner ON cloud_browser_sessions(owner_user_id,session_id);
COMMIT;
