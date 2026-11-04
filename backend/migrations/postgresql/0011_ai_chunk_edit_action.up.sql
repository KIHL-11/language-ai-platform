BEGIN;

-- B-3B: "edit" saves a learner's changes to a proposed candidate without a
-- decision; it never creates a Study Bank item. Numbered after the account
-- stack (0007-0009) and B-1 (0010).

ALTER TABLE cloud_ai_chunk_decisions
    DROP CONSTRAINT cloud_ai_decision_action,
    ADD CONSTRAINT cloud_ai_decision_action
        CHECK (action IN ('accept', 'edit_and_accept', 'edit', 'reject')),
    ADD CONSTRAINT cloud_ai_decision_edit_consistency
        CHECK (action <> 'edit' OR study_item_id IS NULL);

COMMIT;
