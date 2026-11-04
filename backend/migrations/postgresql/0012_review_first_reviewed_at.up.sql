BEGIN;

-- B-5: when a card was first reviewed, so the daily new-card quota can be
-- counted per learner-local day. Set once by the first rating; existing
-- reviewed cards stay NULL (their first day is unknown) and never count.
-- No SRS, ReviewState, due_at or history column changes.

ALTER TABLE cloud_study_items
    ADD COLUMN first_reviewed_at TIMESTAMPTZ,
    ADD CONSTRAINT cloud_study_first_review_consistency
        CHECK (first_reviewed_at IS NULL OR review_count >= 1);

CREATE INDEX cloud_study_items_first_reviewed_idx
    ON cloud_study_items (owner_user_id, first_reviewed_at)
    WHERE first_reviewed_at IS NOT NULL;

COMMIT;
