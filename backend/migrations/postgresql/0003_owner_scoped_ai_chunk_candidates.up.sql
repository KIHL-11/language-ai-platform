BEGIN;

CREATE TABLE cloud_ai_chunk_batches (
    owner_user_id VARCHAR(128) NOT NULL,
    batch_id UUID NOT NULL,
    source_language VARCHAR(8) NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    source_label VARCHAR(1000) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ready',
    PRIMARY KEY (owner_user_id, batch_id),
    CONSTRAINT cloud_ai_batch_owner_fk
        FOREIGN KEY (owner_user_id) REFERENCES cloud_users(user_id) ON DELETE CASCADE,
    CONSTRAINT cloud_ai_batch_language CHECK (source_language IN ('en', 'de')),
    CONSTRAINT cloud_ai_batch_source_type CHECK (source_type IN ('manual_text', 'library_passage')),
    CONSTRAINT cloud_ai_batch_status CHECK (status IN ('ready', 'expired')),
    CONSTRAINT cloud_ai_batch_expiry CHECK (expires_at > created_at)
);

CREATE INDEX cloud_ai_batch_owner_created_idx
    ON cloud_ai_chunk_batches (owner_user_id, created_at DESC, batch_id);
CREATE INDEX cloud_ai_batch_expiry_idx
    ON cloud_ai_chunk_batches (expires_at, owner_user_id, batch_id);

CREATE TABLE cloud_ai_chunk_candidates (
    owner_user_id VARCHAR(128) NOT NULL,
    batch_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    ordinal INTEGER NOT NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'proposed',
    version BIGINT NOT NULL DEFAULT 1,
    source_language VARCHAR(8) NOT NULL,
    text VARCHAR(500) NOT NULL,
    normalized_identity VARCHAR(500) NOT NULL,
    suggested_meaning_zh VARCHAR(1000) NOT NULL DEFAULT '',
    usage_note VARCHAR(2000) NOT NULL DEFAULT '',
    example VARCHAR(2000) NOT NULL DEFAULT '',
    explanation_of_value VARCHAR(1000) NOT NULL DEFAULT '',
    quality_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    proposal_model VARCHAR(100) NOT NULL,
    proposal_version VARCHAR(50) NOT NULL,
    existing_study_item_id UUID,
    existing_study_item_active BOOLEAN,
    accepted_study_item_id UUID,
    PRIMARY KEY (owner_user_id, candidate_id),
    CONSTRAINT cloud_ai_candidate_batch_fk
        FOREIGN KEY (owner_user_id, batch_id)
        REFERENCES cloud_ai_chunk_batches(owner_user_id, batch_id) ON DELETE CASCADE,
    CONSTRAINT cloud_ai_candidate_existing_study_fk
        FOREIGN KEY (owner_user_id, existing_study_item_id)
        REFERENCES cloud_study_items(owner_user_id, item_id) ON DELETE SET NULL (existing_study_item_id),
    CONSTRAINT cloud_ai_candidate_accepted_study_fk
        FOREIGN KEY (owner_user_id, accepted_study_item_id)
        REFERENCES cloud_study_items(owner_user_id, item_id) ON DELETE SET NULL (accepted_study_item_id),
    CONSTRAINT cloud_ai_candidate_ordinal_unique UNIQUE (owner_user_id, batch_id, ordinal),
    CONSTRAINT cloud_ai_candidate_state CHECK (
        state IN ('proposed', 'accepted', 'edited_and_accepted', 'rejected', 'expired')
    ),
    CONSTRAINT cloud_ai_candidate_version CHECK (version >= 1),
    CONSTRAINT cloud_ai_candidate_language CHECK (source_language IN ('en', 'de')),
    CONSTRAINT cloud_ai_candidate_quality_flags CHECK (
        jsonb_typeof(quality_flags) = 'array' AND jsonb_array_length(quality_flags) <= 20
    ),
    CONSTRAINT cloud_ai_candidate_unaccepted_consistency CHECK (
        state IN ('accepted', 'edited_and_accepted') OR accepted_study_item_id IS NULL
    )
);

CREATE INDEX cloud_ai_candidate_batch_idx
    ON cloud_ai_chunk_candidates (owner_user_id, batch_id, ordinal);

CREATE TABLE cloud_ai_chunk_evidence (
    owner_user_id VARCHAR(128) NOT NULL,
    candidate_id UUID NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    quote VARCHAR(2000) NOT NULL,
    source_label VARCHAR(1000) NOT NULL,
    library_item_id_snapshot VARCHAR(128),
    library_item_fk UUID,
    lesson_id VARCHAR(128),
    sentence_id INTEGER,
    source_url VARCHAR(2048),
    sentence_translation VARCHAR(2000) NOT NULL DEFAULT '',
    start_seconds DOUBLE PRECISION,
    end_seconds DOUBLE PRECISION,
    evidence_start INTEGER NOT NULL,
    evidence_end INTEGER NOT NULL,
    PRIMARY KEY (owner_user_id, candidate_id),
    CONSTRAINT cloud_ai_evidence_candidate_fk
        FOREIGN KEY (owner_user_id, candidate_id)
        REFERENCES cloud_ai_chunk_candidates(owner_user_id, candidate_id) ON DELETE CASCADE,
    CONSTRAINT cloud_ai_evidence_library_fk
        FOREIGN KEY (owner_user_id, library_item_fk)
        REFERENCES cloud_library_items(owner_user_id, item_id) ON DELETE SET NULL (library_item_fk),
    CONSTRAINT cloud_ai_evidence_type CHECK (source_type IN ('manual_text', 'library_sentence')),
    CONSTRAINT cloud_ai_evidence_range CHECK (
        evidence_start >= 0 AND evidence_end > evidence_start
        AND evidence_end <= char_length(quote)
    ),
    CONSTRAINT cloud_ai_evidence_time CHECK (
        (start_seconds IS NULL AND end_seconds IS NULL)
        OR (start_seconds >= 0 AND end_seconds >= start_seconds)
    ),
    CONSTRAINT cloud_ai_evidence_source_consistency CHECK (
        (source_type = 'manual_text' AND library_item_id_snapshot IS NULL
            AND library_item_fk IS NULL AND lesson_id IS NULL AND sentence_id IS NULL
            AND source_url IS NULL AND start_seconds IS NULL AND end_seconds IS NULL)
        OR
        (source_type = 'library_sentence' AND library_item_id_snapshot IS NOT NULL
            AND lesson_id IS NOT NULL AND sentence_id > 0 AND source_url IS NOT NULL
            AND start_seconds IS NOT NULL AND end_seconds IS NOT NULL)
    )
);

CREATE TABLE cloud_ai_chunk_generation_requests (
    owner_user_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_fingerprint CHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    batch_id UUID,
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (owner_user_id, idempotency_key),
    CONSTRAINT cloud_ai_request_owner_fk
        FOREIGN KEY (owner_user_id) REFERENCES cloud_users(user_id) ON DELETE CASCADE,
    CONSTRAINT cloud_ai_request_batch_fk
        FOREIGN KEY (owner_user_id, batch_id)
        REFERENCES cloud_ai_chunk_batches(owner_user_id, batch_id) ON DELETE RESTRICT,
    CONSTRAINT cloud_ai_request_status CHECK (status IN ('pending', 'completed')),
    CONSTRAINT cloud_ai_request_fingerprint CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT cloud_ai_request_state CHECK (
        (status = 'pending' AND batch_id IS NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (status = 'completed' AND batch_id IS NOT NULL AND lease_token IS NULL AND lease_expires_at IS NULL)
    ),
    CONSTRAINT cloud_ai_request_timestamps CHECK (
        updated_at >= created_at AND expires_at > created_at
    )
);

CREATE INDEX cloud_ai_request_expiry_idx
    ON cloud_ai_chunk_generation_requests (expires_at, owner_user_id, idempotency_key);

CREATE TABLE cloud_ai_chunk_decisions (
    owner_user_id VARCHAR(128) NOT NULL,
    decision_id UUID NOT NULL,
    batch_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    action VARCHAR(32) NOT NULL,
    candidate_version_before BIGINT NOT NULL,
    candidate_version_after BIGINT NOT NULL,
    study_item_id UUID,
    response_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (owner_user_id, decision_id),
    CONSTRAINT cloud_ai_decision_candidate_fk
        FOREIGN KEY (owner_user_id, candidate_id)
        REFERENCES cloud_ai_chunk_candidates(owner_user_id, candidate_id) ON DELETE RESTRICT,
    CONSTRAINT cloud_ai_decision_batch_fk
        FOREIGN KEY (owner_user_id, batch_id)
        REFERENCES cloud_ai_chunk_batches(owner_user_id, batch_id) ON DELETE RESTRICT,
    CONSTRAINT cloud_ai_decision_study_fk
        FOREIGN KEY (owner_user_id, study_item_id)
        REFERENCES cloud_study_items(owner_user_id, item_id) ON DELETE SET NULL (study_item_id),
    CONSTRAINT cloud_ai_decision_action CHECK (action IN ('accept', 'edit_and_accept', 'reject')),
    CONSTRAINT cloud_ai_decision_versions CHECK (
        candidate_version_before >= 1 AND candidate_version_after = candidate_version_before + 1
    ),
    CONSTRAINT cloud_ai_decision_response_object CHECK (jsonb_typeof(response_json) = 'object'),
    CONSTRAINT cloud_ai_decision_reject_consistency CHECK (
        action <> 'reject' OR study_item_id IS NULL
    )
);

CREATE TABLE cloud_ai_chunk_decision_keys (
    owner_user_id VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    decision_fingerprint CHAR(64) NOT NULL,
    decision_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (owner_user_id, idempotency_key),
    CONSTRAINT cloud_ai_decision_key_owner_fk
        FOREIGN KEY (owner_user_id) REFERENCES cloud_users(user_id) ON DELETE CASCADE,
    CONSTRAINT cloud_ai_decision_key_decision_fk
        FOREIGN KEY (owner_user_id, decision_id)
        REFERENCES cloud_ai_chunk_decisions(owner_user_id, decision_id) ON DELETE RESTRICT,
    CONSTRAINT cloud_ai_decision_key_fingerprint CHECK (decision_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT cloud_ai_decision_key_expiry CHECK (expires_at > created_at)
);

CREATE INDEX cloud_ai_decision_key_expiry_idx
    ON cloud_ai_chunk_decision_keys (expires_at, owner_user_id, idempotency_key);

ALTER TABLE cloud_ai_chunk_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_ai_chunk_batches FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_ai_batch_owner_policy ON cloud_ai_chunk_batches
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

ALTER TABLE cloud_ai_chunk_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_ai_chunk_candidates FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_ai_candidate_owner_policy ON cloud_ai_chunk_candidates
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

ALTER TABLE cloud_ai_chunk_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_ai_chunk_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_ai_evidence_owner_policy ON cloud_ai_chunk_evidence
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

ALTER TABLE cloud_ai_chunk_generation_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_ai_chunk_generation_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_ai_request_owner_policy ON cloud_ai_chunk_generation_requests
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

ALTER TABLE cloud_ai_chunk_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_ai_chunk_decisions FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_ai_decision_owner_policy ON cloud_ai_chunk_decisions
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

ALTER TABLE cloud_ai_chunk_decision_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_ai_chunk_decision_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_ai_decision_key_owner_policy ON cloud_ai_chunk_decision_keys
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

COMMIT;
