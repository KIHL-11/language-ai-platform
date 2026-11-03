BEGIN;
-- Rolling back removes WeChat links, re-check codes and long codes; email
-- identities and accounts remain. Tolerates a partially migrated schema.
DROP TABLE IF EXISTS cloud_oauth_states;
ALTER TABLE IF EXISTS cloud_browser_sessions DROP COLUMN IF EXISTS reauthenticated_at;
DROP INDEX IF EXISTS cloud_email_login_challenges_created;
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='cloud_email_login_challenges' AND column_name='purpose') THEN
  DELETE FROM cloud_email_login_challenges WHERE purpose='reauth' OR code_format='long';
  ALTER TABLE cloud_email_login_challenges
   DROP CONSTRAINT IF EXISTS cloud_email_login_challenges_owner,
   DROP COLUMN code_format, DROP COLUMN owner_user_id, DROP COLUMN purpose;
 END IF;
 IF to_regclass('cloud_external_identities') IS NOT NULL THEN
  DROP INDEX IF EXISTS cloud_external_identities_one_per_provider;
  DELETE FROM cloud_external_identities WHERE provider<>'email';
  ALTER TABLE cloud_external_identities DROP CONSTRAINT IF EXISTS cloud_external_identities_wechat_subject;
  ALTER TABLE cloud_external_identities DROP CONSTRAINT IF EXISTS cloud_external_identities_provider_check;
  ALTER TABLE cloud_external_identities ADD CONSTRAINT cloud_external_identities_provider_check CHECK(provider IN ('email'));
 END IF;
END $$;
COMMIT;
