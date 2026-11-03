BEGIN;
-- P8-05B3: an account may hold up to two verified email addresses (enforced
-- under the account lock); WeChat stays one per account.
DROP INDEX cloud_external_identities_one_per_provider;
CREATE UNIQUE INDEX cloud_external_identities_one_wechat ON cloud_external_identities(owner_user_id) WHERE provider='wechat';
CREATE INDEX cloud_external_identities_owner ON cloud_external_identities(owner_user_id,provider,created_at);

-- 'claim' codes prove control of a new address for the signed-in account.
ALTER TABLE cloud_email_login_challenges DROP CONSTRAINT cloud_email_login_challenges_purpose_check;
ALTER TABLE cloud_email_login_challenges ADD CONSTRAINT cloud_email_login_challenges_purpose_check
 CHECK(purpose IN ('login','reauth','claim'));
ALTER TABLE cloud_email_login_challenges DROP CONSTRAINT cloud_email_login_challenges_owner;
ALTER TABLE cloud_email_login_challenges
 ADD COLUMN replaces_subject VARCHAR(254) CHECK(replaces_subject ~ '^[^@[:space:]A-Z]+@[a-z0-9.-]+$'),
 ADD CONSTRAINT cloud_email_login_challenges_owner CHECK((purpose IN ('reauth','claim')) = (owner_user_id IS NOT NULL)),
 ADD CONSTRAINT cloud_email_login_challenges_replaces CHECK(purpose='claim' OR replaces_subject IS NULL);

-- Purge support (maintenance job): find dead sessions without a full scan,
-- and make the cascade to OAuth states an index lookup.
CREATE INDEX cloud_browser_sessions_expires ON cloud_browser_sessions(expires_at);
CREATE INDEX cloud_browser_sessions_last_seen ON cloud_browser_sessions(last_seen_at);
CREATE INDEX cloud_browser_sessions_revoked ON cloud_browser_sessions(revoked_at) WHERE revoked_at IS NOT NULL;
CREATE INDEX cloud_oauth_states_session ON cloud_oauth_states(session_id) WHERE session_id IS NOT NULL;
COMMIT;
