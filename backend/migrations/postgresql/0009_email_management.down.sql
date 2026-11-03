BEGIN;
-- Refuses to run while any account holds more than one email address: the
-- 0008 schema allows only one, and nobody but the learner can say which
-- address to keep. A rollback must never silently remove a verified sign-in
-- method. Changed addresses (one per account) roll back losslessly.
-- Open add/change codes are closed (not deleted) so failed-attempt counts
-- keep feeding the daily cap. Tolerates a partially migrated schema.
DO $$
BEGIN
 IF to_regclass('cloud_external_identities') IS NOT NULL THEN
  IF EXISTS (SELECT 1 FROM cloud_external_identities WHERE provider='email'
             GROUP BY owner_user_id HAVING count(*) > 1) THEN
   RAISE EXCEPTION 'refusing to roll back 0009: % accounts hold two email addresses; resolve them with the learners first',
    (SELECT count(*) FROM (SELECT 1 FROM cloud_external_identities WHERE provider='email'
                           GROUP BY owner_user_id HAVING count(*) > 1) multi);
  END IF;
 END IF;
 IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema()
            AND table_name='cloud_email_login_challenges' AND column_name='replaces_subject') THEN
  ALTER TABLE cloud_email_login_challenges
   DROP CONSTRAINT IF EXISTS cloud_email_login_challenges_replaces,
   DROP CONSTRAINT IF EXISTS cloud_email_login_challenges_owner,
   DROP CONSTRAINT IF EXISTS cloud_email_login_challenges_purpose_check;
  -- A closed 'reauth' row can never be redeemed; its failed attempts still count.
  UPDATE cloud_email_login_challenges SET purpose='reauth', closed_at=COALESCE(closed_at, GREATEST(now(), created_at)) WHERE purpose='claim';
  ALTER TABLE cloud_email_login_challenges DROP COLUMN replaces_subject;
  ALTER TABLE cloud_email_login_challenges ADD CONSTRAINT cloud_email_login_challenges_owner
   CHECK((purpose='reauth') = (owner_user_id IS NOT NULL));
  ALTER TABLE cloud_email_login_challenges ADD CONSTRAINT cloud_email_login_challenges_purpose_check
   CHECK(purpose IN ('login','reauth'));
 END IF;
 IF to_regclass('cloud_external_identities_one_wechat') IS NOT NULL THEN
  DROP INDEX cloud_external_identities_owner;
  DROP INDEX cloud_external_identities_one_wechat;
  CREATE UNIQUE INDEX cloud_external_identities_one_per_provider ON cloud_external_identities(owner_user_id,provider);
 END IF;
END $$;
COMMIT;
