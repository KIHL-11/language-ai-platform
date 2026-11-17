BEGIN;
-- Cloud Phase 1.5: database-level owner isolation for the three Library tables
-- created by 0001. Same policy shape as 0002-0004: rows are visible and
-- writable only when owner_user_id equals the transaction-local
-- app.current_owner, which the repository sets from the authenticated owner.
-- FORCE also binds the table owner. Superusers and BYPASSRLS roles are not
-- bound; the runtime role has neither (enforced by preflight).
-- 0010-0013 are reserved for the frozen B-series and are never reused.
ALTER TABLE cloud_library_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_library_items FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_library_items_owner_policy ON cloud_library_items
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

ALTER TABLE cloud_library_media_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_library_media_assets FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_library_media_owner_policy ON cloud_library_media_assets
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));

ALTER TABLE cloud_media_cleanup_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_media_cleanup_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY cloud_media_cleanup_owner_policy ON cloud_media_cleanup_outbox
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));
COMMIT;
