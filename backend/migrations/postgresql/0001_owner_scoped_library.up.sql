BEGIN;

CREATE TABLE cloud_users (
    user_id VARCHAR(128) PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT cloud_users_id_format CHECK (
        user_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
    )
);

CREATE TABLE cloud_library_items (
    owner_user_id VARCHAR(128) NOT NULL,
    item_id UUID NOT NULL,
    source_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    lesson_json JSONB NOT NULL,
    progress_json JSONB NOT NULL,
    listening_progress_json JSONB NOT NULL,
    shadowing_progress_json JSONB NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY (owner_user_id, item_id),
    CONSTRAINT cloud_library_owner_fk
        FOREIGN KEY (owner_user_id) REFERENCES cloud_users(user_id)
        ON DELETE CASCADE,
    CONSTRAINT cloud_library_source_key_size
        CHECK (octet_length(source_key) BETWEEN 1 AND 4096),
    CONSTRAINT cloud_library_revision_positive CHECK (revision >= 1),
    CONSTRAINT cloud_library_timestamp_order CHECK (updated_at >= created_at),
    CONSTRAINT cloud_library_owner_source_unique
        UNIQUE (owner_user_id, source_key)
);

CREATE INDEX cloud_library_items_owner_updated_idx
    ON cloud_library_items (owner_user_id, updated_at DESC, item_id);

CREATE TABLE cloud_library_media_assets (
    owner_user_id VARCHAR(128) NOT NULL,
    media_id UUID NOT NULL,
    library_item_id UUID NOT NULL,
    asset_kind VARCHAR(32) NOT NULL,
    object_key TEXT NOT NULL,
    filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(255) NOT NULL,
    byte_size BIGINT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (owner_user_id, media_id),
    CONSTRAINT cloud_library_media_item_fk
        FOREIGN KEY (owner_user_id, library_item_id)
        REFERENCES cloud_library_items(owner_user_id, item_id)
        ON DELETE CASCADE,
    CONSTRAINT cloud_library_media_owner_kind_unique
        UNIQUE (owner_user_id, library_item_id, asset_kind),
    CONSTRAINT cloud_library_media_object_unique UNIQUE (object_key),
    CONSTRAINT cloud_library_media_kind CHECK (asset_kind IN ('audio')),
    CONSTRAINT cloud_library_media_status CHECK (status IN ('ready')),
    CONSTRAINT cloud_library_media_size CHECK (byte_size > 0),
    CONSTRAINT cloud_library_media_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT cloud_library_media_object_key CHECK (
        object_key ~ '^owners/[0-9a-f]{64}/library/[0-9a-f-]{36}/audio-[0-9a-f]{32}[.][a-z0-9]+$'
        AND object_key LIKE '%/library/' || library_item_id::text || '/audio-%'
    ),
    CONSTRAINT cloud_library_media_timestamp_order CHECK (updated_at >= created_at)
);

CREATE INDEX cloud_library_media_owner_item_idx
    ON cloud_library_media_assets (owner_user_id, library_item_id);

CREATE TABLE cloud_media_cleanup_outbox (
    cleanup_id UUID PRIMARY KEY,
    owner_user_id VARCHAR(128) NOT NULL,
    library_item_id UUID NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT cloud_media_cleanup_owner_fk
        FOREIGN KEY (owner_user_id) REFERENCES cloud_users(user_id)
        ON DELETE RESTRICT,
    CONSTRAINT cloud_media_cleanup_status CHECK (status IN ('pending')),
    CONSTRAINT cloud_media_cleanup_attempts CHECK (attempt_count >= 0),
    CONSTRAINT cloud_media_cleanup_object_key CHECK (
        object_key ~ '^owners/[0-9a-f]{64}/library/[0-9a-f-]{36}/audio-[0-9a-f]{32}[.][a-z0-9]+$'
        AND object_key LIKE '%/library/' || library_item_id::text || '/audio-%'
    ),
    CONSTRAINT cloud_media_cleanup_timestamp_order CHECK (updated_at >= created_at)
);

CREATE INDEX cloud_media_cleanup_pending_idx
    ON cloud_media_cleanup_outbox (status, created_at, cleanup_id);

COMMIT;
