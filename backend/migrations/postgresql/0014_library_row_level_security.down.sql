BEGIN;
-- Not used by the forward-only runner; documents the inverse for a manual,
-- reviewed restore-from-backup procedure only.
DROP POLICY IF EXISTS cloud_media_cleanup_owner_policy ON cloud_media_cleanup_outbox;
ALTER TABLE cloud_media_cleanup_outbox NO FORCE ROW LEVEL SECURITY;
ALTER TABLE cloud_media_cleanup_outbox DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cloud_library_media_owner_policy ON cloud_library_media_assets;
ALTER TABLE cloud_library_media_assets NO FORCE ROW LEVEL SECURITY;
ALTER TABLE cloud_library_media_assets DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cloud_library_items_owner_policy ON cloud_library_items;
ALTER TABLE cloud_library_items NO FORCE ROW LEVEL SECURITY;
ALTER TABLE cloud_library_items DISABLE ROW LEVEL SECURITY;
COMMIT;
