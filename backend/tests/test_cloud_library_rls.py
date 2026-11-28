"""Cloud Phase 1.5 Task C: database-level owner isolation for the Library tables.

Migration 0014 puts cloud_library_items, cloud_library_media_assets and
cloud_media_cleanup_outbox under ENABLE + FORCE row level security with the
same owner policy as 0002-0004. These tests run every check as the real
least-privilege runtime role (NOSUPERUSER, NOBYPASSRLS) in a throwaway
database, with synthetic data only. Test IDs: LRLS-*.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from services.cloud import migrations, preflight, schema
from services.library.postgres import FilesystemMediaObjectStore, PostgresOwnerScopedLibraryRepository
from test_cloud_db_foundation import cloud_db  # noqa: F401  (shared throwaway-database fixture)
from test_owner_scoped_library import lesson

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)
OWNER_A = "cloud-p15-owner-a"
OWNER_B = "cloud-p15-owner-b"
AUDIO = b"ID3" + bytes(range(256)) * 4


@pytest.fixture
def library(cloud_db, tmp_path):  # noqa: F811
    runtime_dsn = cloud_db.runtime()
    assert "0014" in migrations.plan(cloud_db.admin_dsn).applied
    for owner in (OWNER_A, OWNER_B):
        cloud_db.execute("INSERT INTO cloud_users (user_id, created_at) VALUES (%s, %s)", (owner, NOW))
    repository = PostgresOwnerScopedLibraryRepository(
        runtime_dsn, FilesystemMediaObjectStore(tmp_path / "cloud-media"), clock=lambda: NOW)

    class Fixture:
        db = cloud_db
        dsn = runtime_dsn
        repo = repository
        media = tmp_path / "cloud-media"

    return Fixture()


def _as(dsn: str, owner: str | None, statement: str, params=()):
    """Run one statement as the runtime role in a transaction bound to ``owner``."""
    with psycopg.connect(dsn) as connection:
        if owner is not None:
            connection.execute("SELECT set_config('app.current_owner', %s, true)", (owner,))
        cursor = connection.execute(statement, params)
        return cursor.fetchall() if cursor.description else cursor.rowcount


# LRLS-1: 0014 enables and forces RLS with exactly the pinned owner policy, and
# the unchanged PR-R preflight accepts it (structural OID-bound check included).
def test_lrls_1_library_tables_are_forced_rls_and_preflight_accepts(library):
    for table in schema.LIBRARY_RLS_TABLES:
        enabled, forced = library.db.scalar(
            "SELECT ARRAY[relrowsecurity, relforcerowsecurity] FROM pg_class "
            "WHERE oid = %s::regclass", (f"public.{table}",))
        assert enabled and forced, table
        names = library.db.scalar(
            "SELECT array_agg(policyname) FROM pg_policies WHERE schemaname='public' AND tablename=%s",
            (table,))
        assert names == [schema.POLICY_NAMES[table]]
    report = preflight.run_preflight(library.dsn, minimum_version="0014")
    assert report.ok, report.failures
    rolbypassrls, rolsuper = library.db.scalar(
        "SELECT ARRAY[rolbypassrls, rolsuper] FROM pg_roles WHERE rolname = %s", (library.db.runtime_role,))
    assert not rolbypassrls and not rolsuper


# LRLS-2: every repository operation touches only its own owner's rows.
def test_lrls_2_repository_owner_correctness_under_runtime_role(library):
    repo = library.repo
    a = repo.create_for_owner(OWNER_A, lesson(id="lesson-a"))
    b = repo.create_for_owner(OWNER_B, lesson(id="lesson-b", source_url="https://example.test/b"))
    repo.put_audio_for_owner(OWNER_A, a.id, filename="a.mp3", content_type="audio/mpeg", content=AUDIO)
    assert [i.id for i in repo.list_for_owner(OWNER_A)] == [a.id]
    assert [i.id for i in repo.list_for_owner(OWNER_B)] == [b.id]
    for call in (lambda: repo.get_for_owner(OWNER_B, a.id),
                 lambda: repo.audio_for_owner(OWNER_B, a.id),
                 lambda: repo.delete_for_owner(OWNER_B, a.id),
                 lambda: repo.update_progress_for_owner(OWNER_B, a.id, {})):
        with pytest.raises(KeyError):
            call()
    assert repo.audio_for_owner(OWNER_A, a.id).path.read_bytes() == AUDIO
    repo.delete_for_owner(OWNER_A, a.id)
    assert repo.list_for_owner(OWNER_A) == []
    assert [i.id for i in repo.list_for_owner(OWNER_B)] == [b.id]  # B untouched
    assert library.db.scalar("SELECT count(*) FROM cloud_library_items WHERE owner_user_id=%s", (OWNER_B,)) == 1


# LRLS-3: even SQL that forgets the owner predicate cannot cross owners.
def test_lrls_3_database_blocks_cross_owner_sql_without_predicates(library):
    a = library.repo.create_for_owner(OWNER_A, lesson(id="lesson-a"))
    library.repo.put_audio_for_owner(OWNER_A, a.id, filename="a.mp3", content_type="audio/mpeg", content=AUDIO)
    dsn = library.dsn
    for table in schema.LIBRARY_RLS_TABLES:
        assert _as(dsn, OWNER_B, f"SELECT owner_user_id FROM {table}") == []
    assert len(_as(dsn, OWNER_A, "SELECT item_id FROM cloud_library_items")) == 1
    assert _as(dsn, OWNER_B, "UPDATE cloud_library_items SET revision = revision + 1") == 0
    assert _as(dsn, OWNER_B, "DELETE FROM cloud_library_items") == 0
    assert _as(dsn, OWNER_B, "UPDATE cloud_library_media_assets SET status = 'ready'") == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        _as(dsn, OWNER_B,
            "INSERT INTO cloud_library_items (owner_user_id, item_id, source_key, created_at, updated_at,"
            " lesson_json, progress_json, listening_progress_json, shadowing_progress_json, revision)"
            " VALUES (%s, gen_random_uuid(), 'forged', now(), now(), '{}'::jsonb, '{}'::jsonb,"
            " '{}'::jsonb, '{}'::jsonb, 1)",
            (OWNER_A,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        _as(dsn, OWNER_B,
            "INSERT INTO cloud_media_cleanup_outbox (cleanup_id, owner_user_id, library_item_id, object_key,"
            " status, attempt_count, created_at, updated_at) VALUES (gen_random_uuid(), %s, %s,"
            " %s, 'pending', 0, now(), now())",
            (OWNER_A, a.id, f"owners/{'0' * 64}/library/{a.id}/audio-{'0' * 32}.mp3"))
    assert library.db.scalar("SELECT count(*) FROM cloud_library_items") == 1


# LRLS-4: without an owner context nothing is visible or writable (fail closed),
# and a transaction-local context never leaks into the next transaction.
def test_lrls_4_missing_context_fails_closed_and_context_does_not_leak(library):
    library.repo.create_for_owner(OWNER_A, lesson(id="lesson-a"))
    assert _as(library.dsn, None, "SELECT item_id FROM cloud_library_items") == []
    with psycopg.connect(library.dsn) as connection:
        with connection.transaction():
            connection.execute("SELECT set_config('app.current_owner', %s, true)", (OWNER_A,))
            assert len(connection.execute("SELECT 1 FROM cloud_library_items").fetchall()) == 1
        with connection.transaction():
            assert connection.execute("SELECT current_setting('app.current_owner', true)").fetchone()[0] in ("", None)
            assert connection.execute("SELECT 1 FROM cloud_library_items").fetchall() == []


# LRLS-5: preflight refuses a weakened, missing or unforced Library policy.
@pytest.mark.parametrize("tamper, expected", [
    ("ALTER POLICY cloud_library_items_owner_policy ON cloud_library_items USING (true) WITH CHECK (true)",
     "cloud_library_items"),
    ("DROP POLICY cloud_library_media_owner_policy ON cloud_library_media_assets",
     "cloud_library_media_owner_policy"),
    ("ALTER TABLE cloud_media_cleanup_outbox NO FORCE ROW LEVEL SECURITY", "cloud_media_cleanup_outbox"),
    ("ALTER TABLE cloud_library_items DISABLE ROW LEVEL SECURITY", "cloud_library_items"),
    ("CREATE POLICY cloud_library_extra ON cloud_library_items AS PERMISSIVE FOR SELECT USING (true)",
     "cloud_library_extra"),
])
def test_lrls_5_preflight_refuses_tampered_library_rls(library, tamper, expected):
    library.db.execute(tamper)
    report = preflight.run_preflight(library.dsn, minimum_version="0014")
    assert not report.ok
    assert any(expected in failure for failure in report.failures), report.failures


# LRLS-6: numbering. 0010-0013 stay reserved and refused; 0014 is accepted only
# after 0009; anything out of order or unknown is refused before any write.
@pytest.mark.parametrize("line, message", [
    ("0010_cleanup.up.sql", "reserved for the frozen B-series"),
    ("0013_anything.up.sql", "reserved for the frozen B-series"),
    ("0015_unknown.up.sql", "not an approved migration"),
])
def test_lrls_6_reserved_and_unknown_versions_are_refused(tmp_path, line, message):
    import shutil
    target = tmp_path / "migrations"
    shutil.copytree(schema.MIGRATIONS_DIR, target)
    manifest = target / schema.MANIFEST_NAME
    manifest.write_text(manifest.read_text() + "0" * 64 + "  " + line + "\n")
    with pytest.raises(schema.SchemaDefinitionError, match=message):
        schema.load_manifest(target)


def test_lrls_6b_order_and_targets(tmp_path, cloud_db):  # noqa: F811
    import shutil
    target = tmp_path / "migrations"
    shutil.copytree(schema.MIGRATIONS_DIR, target)
    manifest = target / schema.MANIFEST_NAME
    lines = manifest.read_text().splitlines()
    rls = [line for line in lines if "0014_" in line]
    reordered = [line for line in lines if "0014_" not in line and "0009_" not in line]
    reordered += rls + [line for line in lines if "0009_" in line]
    manifest.write_text("\n".join(reordered) + "\n")
    with pytest.raises(schema.SchemaDefinitionError, match="approved order from 0001 without gaps"):
        schema.load_manifest(target)
    for reserved in ("0010", "0011", "0012", "0013"):
        with pytest.raises(migrations.MigrationRefused, match="reserved for the frozen B-series"):
            migrations.apply(cloud_db.admin_dsn, target=reserved)
    assert cloud_db.tables() == set()  # refused before any write
    assert schema.validate_version("0014") == "0014"
    for bad in ("0010", "0013", "0015"):
        with pytest.raises(ValueError):
            schema.validate_version(bad)
    assert migrations.apply(cloud_db.admin_dsn, target="0009")[-1] == "0009"
    assert migrations.apply(cloud_db.admin_dsn, target="0014") == ["0014"]


# LRLS-7: the runtime grants did not grow; RLS is enforced without new privileges.
def test_lrls_7_runtime_grants_unchanged_for_library(library):
    assert schema.RUNTIME_GRANTS["cloud_library_items"] == {"SELECT", "INSERT", "UPDATE", "DELETE"}
    assert schema.RUNTIME_GRANTS["cloud_library_media_assets"] == {"SELECT", "INSERT", "UPDATE"}
    assert schema.RUNTIME_GRANTS["cloud_media_cleanup_outbox"] == {"SELECT", "INSERT", "UPDATE", "DELETE"}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _as(library.dsn, OWNER_A, "ALTER TABLE cloud_library_items DISABLE ROW LEVEL SECURITY")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _as(library.dsn, OWNER_A, "DROP POLICY cloud_library_items_owner_policy ON cloud_library_items")


# LRLS-8 (documented trust boundary): the database trusts the application to set
# app.current_owner from the authenticated identity. A runtime-role session can
# set any value, so RLS protects against query bugs (missing or wrong owner
# predicates), not against SQL execution by a compromised application.
def test_lrls_8_trust_boundary_is_the_application_owner_context(library):
    a = library.repo.create_for_owner(OWNER_A, lesson(id="lesson-a"))
    rows = _as(library.dsn, OWNER_A, "SELECT item_id::text FROM cloud_library_items")
    assert rows == [(a.id,)]  # a session that claims OWNER_A sees OWNER_A's rows


# LRLS-9: applying the real 0014 is atomic and auditable. A lock conflict aborts it
# with no RLS change and no ledger row; once released, a retry applies it exactly
# once and the ledger records file name, pinned digest and the applying role.
def test_lrls_9_real_0014_failure_leaves_nothing_and_retry_is_auditable(cloud_db, monkeypatch):  # noqa: F811
    assert migrations.apply(cloud_db.admin_dsn, target="0009")[-1] == "0009"
    monkeypatch.setattr(migrations, "LOCK_TIMEOUT", "300ms")

    def forced(table):
        return cloud_db.scalar(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid = %s::regclass", (table,))

    holder = psycopg.connect(cloud_db.admin_dsn)
    try:
        holder.execute("LOCK TABLE cloud_media_cleanup_outbox IN ACCESS EXCLUSIVE MODE")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            migrations.apply(cloud_db.admin_dsn, target="0014")
        assert [forced(t) for t in schema.LIBRARY_RLS_TABLES] == [False, False, False]
        assert cloud_db.scalar("SELECT count(*) FROM pg_policies WHERE tablename = ANY(%s)",
                               (list(schema.LIBRARY_RLS_TABLES),)) == 0
        assert cloud_db.scalar("SELECT max(version) FROM cloud_schema_migrations") == "0009"
    finally:
        holder.rollback()
        holder.close()
    assert migrations.apply(cloud_db.admin_dsn, target="0014") == ["0014"]
    assert migrations.apply(cloud_db.admin_dsn, target="0014") == []
    assert [forced(t) for t in schema.LIBRARY_RLS_TABLES] == [True, True, True]
    with psycopg.connect(cloud_db.admin_dsn) as connection:
        row = connection.execute(
            "SELECT filename, sha256, applied_by = current_user FROM cloud_schema_migrations WHERE version = '0014'"
        ).fetchone()
    assert row == ("0014_library_row_level_security.up.sql", schema.PHASE15_ALLOWED["0014"][1], True)


# LRLS-10: the owner context is cleared at the end of every transaction, also on
# rollback and when a repository call fails part-way; nothing carries over.
def test_lrls_10_context_is_cleared_on_rollback_and_failed_calls(library):
    library.repo.create_for_owner(OWNER_A, lesson(id="lesson-a"))
    with psycopg.connect(library.dsn) as connection:
        with pytest.raises(RuntimeError):
            with connection.transaction():
                connection.execute("SELECT set_config('app.current_owner', %s, true)", (OWNER_A,))
                assert len(connection.execute("SELECT 1 FROM cloud_library_items").fetchall()) == 1
                raise RuntimeError("synthetic failure inside the owner transaction")
        with connection.transaction():
            assert connection.execute("SELECT current_setting('app.current_owner', true)").fetchone()[0] in ("", None)
            assert connection.execute("SELECT 1 FROM cloud_library_items").fetchall() == []
    with pytest.raises(KeyError):  # a failed call for owner B (unknown item) ...
        library.repo.get_for_owner(OWNER_B, "00000000-0000-4000-8000-000000000000")
    # ... leaves no context behind: a context-free session still sees nothing.
    assert _as(library.dsn, None, "SELECT item_id FROM cloud_library_items") == []


# LRLS-11: two concurrent transactions bound to different owners each read and write
# only their own rows, even when both are open at the same time.
def test_lrls_11_concurrent_sessions_are_isolated(library):
    a = library.repo.create_for_owner(OWNER_A, lesson(id="lesson-a"))
    b = library.repo.create_for_owner(OWNER_B, lesson(id="lesson-b", source_url="https://youtu.be/lrls_b"))
    with psycopg.connect(library.dsn) as conn_a, psycopg.connect(library.dsn) as conn_b:
        with conn_a.transaction(), conn_b.transaction():
            conn_a.execute("SELECT set_config('app.current_owner', %s, true)", (OWNER_A,))
            conn_b.execute("SELECT set_config('app.current_owner', %s, true)", (OWNER_B,))
            seen_a = [r[0] for r in conn_a.execute("SELECT item_id::text FROM cloud_library_items")]
            seen_b = [r[0] for r in conn_b.execute("SELECT item_id::text FROM cloud_library_items")]
            assert seen_a == [a.id] and seen_b == [b.id]
            assert conn_a.execute("UPDATE cloud_library_items SET updated_at = updated_at WHERE item_id = %s",
                                  (b.id,)).rowcount == 0
            assert conn_b.execute("DELETE FROM cloud_library_items WHERE item_id = %s", (a.id,)).rowcount == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
                conn_a.execute(
                    "UPDATE cloud_library_items SET owner_user_id = %s WHERE item_id = %s", (OWNER_B, a.id))
    assert library.repo.get_for_owner(OWNER_A, a.id).id == a.id
    assert library.repo.get_for_owner(OWNER_B, b.id).id == b.id


# LRLS-12 (PR-D on RLS): a deleted or replaced audio object leaves a persisted
# 'pending' outbox row bound to its owner; only that owner's context sees it, and
# the synthetic file stays on disk (no synchronous unlink, no cleanup worker).
def test_lrls_12_pending_outbox_rows_are_owner_bound_under_rls(library):
    item = library.repo.create_for_owner(OWNER_A, lesson(id="lesson-a"))
    library.repo.put_audio_for_owner(OWNER_A, item.id, filename="first.mp3",
                                     content_type="audio/mpeg", content=b"synthetic-first")
    first = library.repo.audio_for_owner(OWNER_A, item.id).path
    library.repo.put_audio_for_owner(OWNER_A, item.id, filename="second.mp3",
                                     content_type="audio/mpeg", content=b"synthetic-second")
    second = library.repo.audio_for_owner(OWNER_A, item.id).path
    library.repo.delete_for_owner(OWNER_A, item.id)

    root = library.repo.media_objects.root.resolve()
    expected = sorted([
        (OWNER_A, item.id, first.relative_to(root).as_posix(), "pending", 0),
        (OWNER_A, item.id, second.relative_to(root).as_posix(), "pending", 0),
    ])
    query = ("SELECT owner_user_id, library_item_id::text, object_key, status, attempt_count"
             " FROM cloud_media_cleanup_outbox ORDER BY object_key")
    assert sorted(_as(library.dsn, OWNER_A, query)) == expected
    assert _as(library.dsn, OWNER_B, query) == []
    assert _as(library.dsn, None, query) == []
    assert first.read_bytes() == b"synthetic-first" and second.read_bytes() == b"synthetic-second"
    with pytest.raises(KeyError):
        library.repo.audio_for_owner(OWNER_A, item.id)


# LRLS-13 (PR-D remediation, Codex Medium): two concurrent first attaches run as
# the runtime role under 0014. They serialize on the item's parent row (the
# FOR UPDATE stays within the existing grants and the owner policy), exactly one
# object is READY, the replaced one is a pending outbox row visible only to its
# owner, and both synthetic files stay on disk.
def test_lrls_13_concurrent_first_attaches_serialize_under_rls(library, monkeypatch):
    from test_library_audio_attach_safety import (
        MEDIA_INSERT, PauseFirst, assert_one_ready_and_rest_pending, record_physical_delete,
        run_concurrent_attaches)

    item = library.repo.create_for_owner(OWNER_A, lesson(id="lesson-a"))
    deletes = record_physical_delete(monkeypatch, library.repo)
    workers, observed = run_concurrent_attaches(
        monkeypatch, library.repo, library.db.admin_dsn, item.id, PauseFirst(MEDIA_INSERT), owner=OWNER_A)

    assert [w.error for w in workers] == [None, None]
    ready, files = assert_one_ready_and_rest_pending(library.repo, library.db.admin_dsn, item.id, owner=OWNER_A)
    assert len(files) == 2 and deletes == [] and observed == "lock-wait"
    query = "SELECT object_key FROM cloud_media_cleanup_outbox"
    assert [r[0] for r in _as(library.dsn, OWNER_A, query)] == sorted(files - {ready})
    assert _as(library.dsn, OWNER_B, query) == [] and _as(library.dsn, None, query) == []
