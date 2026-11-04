BEGIN;

-- Refuse rather than drop learning history recorded since 0012.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM cloud_study_items WHERE first_reviewed_at IS NOT NULL) THEN
        RAISE EXCEPTION
            'cloud_study_items records first reviews; resolve them before rolling back 0012';
    END IF;
END $$;

DROP INDEX cloud_study_items_first_reviewed_idx;
ALTER TABLE cloud_study_items
    DROP CONSTRAINT cloud_study_first_review_consistency,
    DROP COLUMN first_reviewed_at;

COMMIT;
