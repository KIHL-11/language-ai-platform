BEGIN;

-- Refuse rather than delete learner decisions: 0003 cannot represent an edit.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM cloud_ai_chunk_decisions WHERE action = 'edit') THEN
        RAISE EXCEPTION
            'cloud_ai_chunk_decisions holds edit decisions; resolve them before rolling back 0011';
    END IF;
END $$;

ALTER TABLE cloud_ai_chunk_decisions
    DROP CONSTRAINT cloud_ai_decision_edit_consistency,
    DROP CONSTRAINT cloud_ai_decision_action,
    ADD CONSTRAINT cloud_ai_decision_action
        CHECK (action IN ('accept', 'edit_and_accept', 'reject'));

COMMIT;
