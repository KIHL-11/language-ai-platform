BEGIN;

-- Refuse rather than silently delete quota consumption or a learner's time
-- zone. Run as the table owner with BYPASSRLS (or a superuser): both tables
-- force row-level security.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM cloud_review_new_card_ledger)
       OR EXISTS (SELECT 1 FROM cloud_review_settings) THEN
        RAISE EXCEPTION
            'review quota ledger or settings hold data; resolve them before rolling back 0012';
    END IF;
END $$;

DROP TABLE cloud_review_new_card_ledger;
DROP TABLE cloud_review_settings;

COMMIT;
