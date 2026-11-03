BEGIN;

-- Refuse rather than discard cleanup evidence: a dead row records an object
-- the worker could not remove, and 0001 has no state that can represent it.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM cloud_media_cleanup_outbox WHERE status <> 'pending'
    ) THEN
        RAISE EXCEPTION
            'cloud_media_cleanup_outbox holds dead rows; resolve them before rolling back 0010';
    END IF;
END $$;

DROP INDEX cloud_media_cleanup_due_idx;

ALTER TABLE cloud_media_cleanup_outbox
    DROP CONSTRAINT cloud_media_cleanup_dead_has_reason,
    DROP CONSTRAINT cloud_media_cleanup_error_code,
    DROP CONSTRAINT cloud_media_cleanup_status,
    ADD CONSTRAINT cloud_media_cleanup_status CHECK (status IN ('pending')),
    DROP COLUMN last_error_code,
    DROP COLUMN next_attempt_at;

CREATE INDEX cloud_media_cleanup_pending_idx
    ON cloud_media_cleanup_outbox (status, created_at, cleanup_id);

COMMIT;
