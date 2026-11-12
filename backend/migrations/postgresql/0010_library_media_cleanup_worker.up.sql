BEGIN;

-- B-1 (Issue #58): retry/backoff state for the media cleanup outbox.
-- Numbered after the account stack (0007-0009) so the two lines never collide.
--
-- A row is claimed by pushing next_attempt_at forward and bumping
-- attempt_count in one committed statement; the object is then deleted while
-- the row is held FOR UPDATE, so a claim that outlives its backoff window can
-- never race an upload that adopts the same object key.

ALTER TABLE cloud_media_cleanup_outbox
    ADD COLUMN next_attempt_at TIMESTAMPTZ,
    ADD COLUMN last_error_code VARCHAR(64);

UPDATE cloud_media_cleanup_outbox SET next_attempt_at = updated_at;

ALTER TABLE cloud_media_cleanup_outbox
    ALTER COLUMN next_attempt_at SET NOT NULL,
    DROP CONSTRAINT cloud_media_cleanup_status,
    ADD CONSTRAINT cloud_media_cleanup_status
        CHECK (status IN ('pending', 'dead')),
    ADD CONSTRAINT cloud_media_cleanup_error_code CHECK (
        last_error_code IS NULL OR last_error_code IN (
            'object_delete_failed',
            'namespace_mismatch',
            'object_still_referenced'
        )
    ),
    ADD CONSTRAINT cloud_media_cleanup_dead_has_reason
        CHECK (status <> 'dead' OR last_error_code IS NOT NULL);

DROP INDEX cloud_media_cleanup_pending_idx;

CREATE INDEX cloud_media_cleanup_due_idx
    ON cloud_media_cleanup_outbox (next_attempt_at, cleanup_id)
    WHERE status = 'pending';

COMMIT;
