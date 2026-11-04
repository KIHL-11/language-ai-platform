BEGIN;

-- B-5 (owner decision 2026-11-04): an account-wide daily hard quota of new
-- cards, counted per learner-local calendar day in a stored IANA time zone.
-- The ledger has no foreign key to cloud_study_items, so deleting, archiving
-- or restoring a card never refunds a day's quota. No SRS, ReviewState,
-- due_at or history column changes.

CREATE TABLE cloud_review_settings (
    owner_user_id VARCHAR(128) PRIMARY KEY,
    time_zone TEXT NOT NULL DEFAULT 'UTC',
    configured BOOLEAN NOT NULL DEFAULT FALSE,
    pending_time_zone TEXT,
    pending_effective_at TIMESTAMPTZ,
    daily_new_limit INTEGER NOT NULL DEFAULT 10,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT cloud_review_settings_owner_fk
        FOREIGN KEY (owner_user_id) REFERENCES cloud_users(user_id) ON DELETE CASCADE,
    CONSTRAINT cloud_review_settings_zone CHECK (char_length(time_zone) BETWEEN 1 AND 64),
    CONSTRAINT cloud_review_settings_pending CHECK (
        (pending_time_zone IS NULL) = (pending_effective_at IS NULL)
        AND (pending_time_zone IS NULL OR char_length(pending_time_zone) BETWEEN 1 AND 64)
    ),
    CONSTRAINT cloud_review_settings_limit CHECK (daily_new_limit BETWEEN 0 AND 100)
);

CREATE TABLE cloud_review_new_card_ledger (
    owner_user_id VARCHAR(128) NOT NULL,
    study_item_id UUID NOT NULL,
    quota_day DATE NOT NULL,
    time_zone TEXT NOT NULL,
    consumed_at TIMESTAMPTZ NOT NULL,
    -- One consumption per card, ever: a retry or replay cannot add a second.
    PRIMARY KEY (owner_user_id, study_item_id),
    CONSTRAINT cloud_review_ledger_owner_fk
        FOREIGN KEY (owner_user_id) REFERENCES cloud_users(user_id) ON DELETE CASCADE
);

CREATE INDEX cloud_review_ledger_day_idx
    ON cloud_review_new_card_ledger (owner_user_id, quota_day);

ALTER TABLE cloud_review_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_review_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_review_settings_owner_policy ON cloud_review_settings
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

ALTER TABLE cloud_review_new_card_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_review_new_card_ledger FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_review_ledger_owner_policy ON cloud_review_new_card_ledger
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

COMMIT;
