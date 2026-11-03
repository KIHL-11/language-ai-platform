BEGIN;
-- Refuses to run while any WeChat identity exists: the 0007 schema cannot hold
-- one, and a rollback must never silently remove a verified sign-in method.
-- Unlink (or export) WeChat identities first. Login codes are kept so their
-- failed attempts still count; re-check codes become unredeemable because no
-- browser holds their session-derived binding. Tolerates a partial schema.
DO $$
BEGIN
 IF to_regclass('cloud_external_identities') IS NOT NULL THEN
  IF EXISTS (SELECT 1 FROM cloud_external_identities WHERE provider<>'email') THEN
   RAISE EXCEPTION 'refusing to roll back 0008: % WeChat identities exist; unlink them first',
    (SELECT count(*) FROM cloud_external_identities WHERE provider<>'email');
  END IF;
 END IF;
END $$;
DROP TABLE IF EXISTS cloud_oauth_states;
ALTER TABLE IF EXISTS cloud_browser_sessions DROP COLUMN IF EXISTS reauthenticated_at;
DROP INDEX IF EXISTS cloud_email_login_challenges_created;
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='cloud_email_login_challenges' AND column_name='purpose') THEN
  ALTER TABLE cloud_email_login_challenges
   DROP CONSTRAINT IF EXISTS cloud_email_login_challenges_owner,
   DROP COLUMN code_format, DROP COLUMN owner_user_id, DROP COLUMN purpose;
 END IF;
 IF to_regclass('cloud_external_identities') IS NOT NULL THEN
  DROP INDEX IF EXISTS cloud_external_identities_one_per_provider;
  CREATE INDEX IF NOT EXISTS cloud_external_identities_owner ON cloud_external_identities(owner_user_id,provider);
  ALTER TABLE cloud_external_identities DROP CONSTRAINT IF EXISTS cloud_external_identities_wechat_subject;
  ALTER TABLE cloud_external_identities DROP CONSTRAINT IF EXISTS cloud_external_identities_provider_check;
  ALTER TABLE cloud_external_identities ADD CONSTRAINT cloud_external_identities_provider_check CHECK(provider IN ('email'));
 END IF;
END $$;
COMMIT;
