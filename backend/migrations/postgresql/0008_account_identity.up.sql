BEGIN;
-- P8-05B2: simulated-provider WeChat identities, account linking and
-- account-security hardening. WeChat subjects are "<app id>:<openid>" so a
-- change of WeChat application can never collide with older subjects.
ALTER TABLE cloud_external_identities DROP CONSTRAINT cloud_external_identities_provider_check;
ALTER TABLE cloud_external_identities ADD CONSTRAINT cloud_external_identities_provider_check
 CHECK(provider IN ('email','wechat'));
ALTER TABLE cloud_external_identities ADD CONSTRAINT cloud_external_identities_wechat_subject
 CHECK(provider<>'wechat' OR subject ~ '^wx[0-9a-f]{16}:[A-Za-z0-9_-]{6,64}$');
-- At most one identity per provider per account: no silent second WeChat.
CREATE UNIQUE INDEX cloud_external_identities_one_per_provider ON cloud_external_identities(owner_user_id,provider);
DROP INDEX cloud_external_identities_owner;  -- superseded by the unique index

-- Login codes: 'reauth' codes re-prove the signed-in account's own address
-- and are bound to that session; 'long' codes are issued once an address hit
-- its daily failed-attempt cap and are not brute-forceable.
ALTER TABLE cloud_email_login_challenges
 ADD COLUMN purpose VARCHAR(16) NOT NULL DEFAULT 'login' CHECK(purpose IN ('login','reauth')),
 ADD COLUMN owner_user_id VARCHAR(128) REFERENCES cloud_users(user_id) ON DELETE CASCADE,
 ADD COLUMN code_format VARCHAR(8) NOT NULL DEFAULT 'short' CHECK(code_format IN ('short','long')),
 ADD CONSTRAINT cloud_email_login_challenges_owner CHECK((purpose='reauth') = (owner_user_id IS NOT NULL));
CREATE INDEX cloud_email_login_challenges_created ON cloud_email_login_challenges(created_at);

-- Recent proof of the account's email address (login or explicit re-check).
ALTER TABLE cloud_browser_sessions ADD COLUMN reauthenticated_at TIMESTAMPTZ;

-- OAuth authorization requests. Only a digest of the state is stored; each is
-- bound to the requesting browser and, for linking, to one signed-in session.
CREATE TABLE cloud_oauth_states (
 state_hash CHAR(64) PRIMARY KEY CHECK(state_hash ~ '^[0-9a-f]{64}$'),
 provider VARCHAR(32) NOT NULL CHECK(provider IN ('wechat')),
 purpose VARCHAR(16) NOT NULL CHECK(purpose IN ('login','link')),
 client_binding CHAR(64) NOT NULL CHECK(client_binding ~ '^[0-9a-f]{64}$'),
 owner_user_id VARCHAR(128) REFERENCES cloud_users(user_id) ON DELETE CASCADE,
 session_id UUID REFERENCES cloud_browser_sessions(session_id) ON DELETE CASCADE,
 created_at TIMESTAMPTZ NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 consumed_at TIMESTAMPTZ,
 CHECK(expires_at > created_at),
 CHECK(consumed_at IS NULL OR consumed_at >= created_at),
 CHECK((purpose='link') = (owner_user_id IS NOT NULL AND session_id IS NOT NULL))
);
CREATE INDEX cloud_oauth_states_expires ON cloud_oauth_states(expires_at);
COMMIT;
