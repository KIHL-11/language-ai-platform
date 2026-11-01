BEGIN;

DROP TABLE IF EXISTS cloud_media_cleanup_outbox;
DROP TABLE IF EXISTS cloud_library_media_assets;
DROP TABLE IF EXISTS cloud_library_items;
DROP TABLE IF EXISTS cloud_users;

COMMIT;
