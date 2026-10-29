BEGIN;

CREATE TABLE cloud_study_items (
    owner_user_id VARCHAR(128) NOT NULL,
    item_id UUID NOT NULL,
    kind VARCHAR(16) NOT NULL,
    source_language VARCHAR(8) NOT NULL,
    target_language VARCHAR(64) NOT NULL,
    text VARCHAR(500) NOT NULL,
    normalized_text VARCHAR(500) NOT NULL,
    meaning VARCHAR(1000) NOT NULL DEFAULT '',
    lemma VARCHAR(500),
    part_of_speech VARCHAR(128),
    example VARCHAR(2000),
    usage VARCHAR(2000),
    note VARCHAR(2000),
    provenance_json JSONB NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    interval_seconds BIGINT NOT NULL DEFAULT 0,
    ease_factor DOUBLE PRECISION NOT NULL DEFAULT 2.5,
    repetition_count BIGINT NOT NULL DEFAULT 0,
    lapse_count BIGINT NOT NULL DEFAULT 0,
    review_count BIGINT NOT NULL DEFAULT 0,
    again_count BIGINT NOT NULL DEFAULT 0,
    hard_count BIGINT NOT NULL DEFAULT 0,
    good_count BIGINT NOT NULL DEFAULT 0,
    easy_count BIGINT NOT NULL DEFAULT 0,
    last_reviewed_at TIMESTAMPTZ,
    last_rating VARCHAR(16),
    revision BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY (owner_user_id, item_id),
    CONSTRAINT cloud_study_owner_fk
        FOREIGN KEY (owner_user_id) REFERENCES cloud_users(user_id)
        ON DELETE CASCADE,
    CONSTRAINT cloud_study_owner_identity_unique
        UNIQUE (owner_user_id, kind, source_language, target_language, normalized_text),
    CONSTRAINT cloud_study_kind CHECK (kind IN ('word', 'chunk')),
    CONSTRAINT cloud_study_source_language CHECK (source_language IN ('en', 'de')),
    CONSTRAINT cloud_study_target_language_size
        CHECK (octet_length(target_language) BETWEEN 1 AND 64),
    CONSTRAINT cloud_study_text_size CHECK (octet_length(text) BETWEEN 1 AND 2000),
    CONSTRAINT cloud_study_normalized_size
        CHECK (octet_length(normalized_text) BETWEEN 1 AND 2000),
    CONSTRAINT cloud_study_provenance_array
        CHECK (jsonb_typeof(provenance_json) = 'array' AND jsonb_array_length(provenance_json) BETWEEN 1 AND 1000),
    CONSTRAINT cloud_study_timestamp_order CHECK (updated_at >= created_at),
    CONSTRAINT cloud_study_review_numbers CHECK (
        interval_seconds >= 0 AND repetition_count >= 0 AND lapse_count >= 0
        AND review_count >= 0 AND again_count >= 0 AND hard_count >= 0
        AND good_count >= 0 AND easy_count >= 0 AND revision >= 1
    ),
    CONSTRAINT cloud_study_ease CHECK (ease_factor BETWEEN 1.3 AND 3.0),
    CONSTRAINT cloud_study_rating CHECK (
        last_rating IS NULL OR last_rating IN ('again', 'hard', 'good', 'easy')
    ),
    CONSTRAINT cloud_study_review_counter_consistency CHECK (
        review_count = again_count + hard_count + good_count + easy_count
    ),
    CONSTRAINT cloud_study_review_history_consistency CHECK (
        (review_count = 0 AND last_reviewed_at IS NULL AND last_rating IS NULL)
        OR (review_count > 0 AND last_reviewed_at IS NOT NULL AND last_rating IS NOT NULL)
    )
);

CREATE INDEX cloud_study_owner_updated_idx
    ON cloud_study_items (owner_user_id, updated_at DESC, item_id);
CREATE INDEX cloud_study_owner_review_queue_idx
    ON cloud_study_items (owner_user_id, active, review_count, due_at, created_at, item_id);

ALTER TABLE cloud_study_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_study_items FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_study_owner_policy ON cloud_study_items
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

CREATE TABLE cloud_study_media_provenance (
    owner_user_id VARCHAR(128) NOT NULL,
    study_item_id UUID NOT NULL,
    provenance_index INTEGER NOT NULL,
    library_item_id UUID,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (owner_user_id, study_item_id, provenance_index),
    CONSTRAINT cloud_study_media_study_fk
        FOREIGN KEY (owner_user_id, study_item_id)
        REFERENCES cloud_study_items(owner_user_id, item_id)
        ON DELETE CASCADE,
    CONSTRAINT cloud_study_media_library_fk
        FOREIGN KEY (owner_user_id, library_item_id)
        REFERENCES cloud_library_items(owner_user_id, item_id)
        ON DELETE SET NULL (library_item_id),
    CONSTRAINT cloud_study_media_index_nonnegative CHECK (provenance_index >= 0)
);

CREATE INDEX cloud_study_media_library_idx
    ON cloud_study_media_provenance (owner_user_id, library_item_id);
ALTER TABLE cloud_study_media_provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_study_media_provenance FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_study_media_owner_policy ON cloud_study_media_provenance
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

COMMIT;
