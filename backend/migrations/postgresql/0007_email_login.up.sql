BEGIN;
-- Verified external subjects. Provider-neutral so later providers (WeChat)
-- map to the same internal account table; P8-05B ships only 'email'.
CREATE TABLE cloud_external_identities (
 provider VARCHAR(32) NOT NULL CHECK(provider IN ('email')),
 subject VARCHAR(254) NOT NULL CHECK(octet_length(subject) BETWEEN 3 AND 254),
 owner_user_id VARCHAR(128) NOT NULL REFERENCES cloud_users(user_id) ON DELETE CASCADE,
 verified_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(provider,subject),
 CHECK(provider<>'email' OR subject ~ '^[^@[:space:]A-Z]+@[a-z0-9.-]+$'),
 CHECK(verified_at >= created_at)
);
CREATE INDEX cloud_external_identities_owner ON cloud_external_identities(owner_user_id,provider);

-- One-time login codes. Only a digest of the code is stored; a challenge is
-- closed when consumed, superseded by a resend, or locked by failed attempts.
CREATE TABLE cloud_email_login_challenges (
 challenge_id UUID PRIMARY KEY,
 email VARCHAR(254) NOT NULL CHECK(email ~ '^[^@[:space:]A-Z]+@[a-z0-9.-]+$'),
 code_hash CHAR(64) NOT NULL CHECK(code_hash ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 failed_attempts SMALLINT NOT NULL DEFAULT 0 CHECK(failed_attempts BETWEEN 0 AND 5),
 closed_at TIMESTAMPTZ,
 CHECK(expires_at > created_at),
 CHECK(closed_at IS NULL OR closed_at >= created_at)
);
CREATE INDEX cloud_email_login_challenges_email ON cloud_email_login_challenges(email,created_at DESC);
COMMIT;
