"""Cloud PostgreSQL backup / restore: role separation, integrity checks and the drill.

Database tests need TEST_POSTGRES_DSN pointing at a disposable PostgreSQL 16
server on loopback whose user is a superuser (the CI service container or a
local scratch cluster). Tests that run pg_dump/pg_restore also need client
tools of the server's major version; without them they skip, unless
CLOUD_BACKUP_REQUIRE_PG_TOOLS=1, which turns the skip into a failure (the
dedicated backup drill workflow sets it). Only synthetic data is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from services.cloud import backup, backup_drill, migrations, roles
from services.cloud.schema import PHASE1_PINNED, approved_migrations

OWNER = "lapbk-owner-a"


def _base_dsn() -> str:
    value = os.getenv("TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("TEST_POSTGRES_DSN is required for the Cloud backup tests")
    return value


def _require_tools(dsn: str) -> None:
    try:
        with psycopg.connect(dsn) as connection:
            server = int(connection.execute("SHOW server_version_num").fetchone()[0]) // 10000
        majors = {backup.tool_major("pg_dump"), backup.tool_major("pg_restore")}
    except (backup.BackupRefused, OSError, subprocess.CalledProcessError) as exc:
        majors, server, reason = set(), None, str(exc)
    else:
        reason = f"pg_dump/pg_restore {sorted(majors)} vs server {server}"
    if majors != {server}:
        if os.getenv("CLOUD_BACKUP_REQUIRE_PG_TOOLS") == "1":
            pytest.fail(f"CLOUD_BACKUP_REQUIRE_PG_TOOLS=1 but tools do not match: {reason}")
        pytest.skip(f"PostgreSQL client tools of the server's major version are required ({reason})")


# ----------------------------------------------------------------- pure checks


def test_client_env_never_inherits_pgoptions_or_puts_the_dsn_in_argv(monkeypatch):
    monkeypatch.setenv("PGOPTIONS", "-c row_security=on")
    monkeypatch.setenv("PGPASSWORD", "inherited-should-not-pass")
    env = backup._client_env("postgresql://bk:synthetic-secret@127.0.0.1:5432/db")
    assert "PGOPTIONS" not in env
    assert env["PGPASSWORD"] == "synthetic-secret"
    assert env["PGUSER"] == "bk" and env["PGDATABASE"] == "db"
    calls = []
    monkeypatch.setattr(backup.subprocess, "run",
                        lambda args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    backup._run_tool(["pg_dump", "--format=custom"], "postgresql://bk:synthetic-secret@127.0.0.1/db", timeout=5)
    assert not any("synthetic-secret" in part or "postgresql://" in part for part in calls[0])


def test_client_env_refuses_options_that_could_enable_row_security():
    with pytest.raises(backup.BackupRefused, match="Unsupported connection parameters: options"):
        backup._client_env("host=127.0.0.1 dbname=db options='-c row_security=on'")


def test_backup_module_never_passes_enable_row_security():
    source = Path(backup.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert '"--enable-row-security"' not in code and "'--enable-row-security'" not in code


def _sealed(tmp_path: Path) -> Path:
    root = tmp_path / "cloud-backup-x"
    (root / "media" / "a").mkdir(parents=True)
    (root / "media" / "a" / "obj").write_bytes(b"audio")
    (root / "database.dump").write_bytes(b"dump")
    (root / "manifest.json").write_text("{}")
    backup._write_manifest_sha(root)
    return root


@pytest.mark.parametrize("tamper", ["modify", "add", "remove", "symlink"])
def test_manifest_seal_detects_tampering(tmp_path, tamper):
    root = _sealed(tmp_path)
    backup._check_manifest_sha(root)
    if tamper == "modify":
        (root / "media" / "a" / "obj").write_bytes(b"other")
    elif tamper == "add":
        (root / "media" / "extra").write_bytes(b"x")
    elif tamper == "remove":
        (root / "database.dump").unlink()
    else:
        (root / "media" / "link").symlink_to(root / "database.dump")
    with pytest.raises(backup.BackupInvalid):
        backup._check_manifest_sha(root)


def test_media_classification_refuses_missing_or_changed_assets():
    digest = hashlib.sha256(b"audio").hexdigest()
    assets = [{"object_key": "k1", "byte_size": 5, "sha256": digest}]
    outbox = [{"object_key": "k2", "status": "pending"}, {"object_key": "k3", "status": "pending"}]
    files = {"k1": {"size": 5, "sha256": digest}, "k2": {"size": 1, "sha256": "0" * 64},
             "orphan": {"size": 1, "sha256": "1" * 64}}
    assert backup.classify_media(files, assets, outbox) == {
        "assets": 1, "outbox_retained": 1, "outbox_already_removed": 1, "unreferenced": 1}
    with pytest.raises(backup.BackupInvalid, match="missing"):
        backup.classify_media({}, assets, outbox)
    with pytest.raises(backup.BackupInvalid, match="does not match"):
        backup.classify_media({"k1": {"size": 5, "sha256": "f" * 64}}, assets, outbox)


def test_backup_requires_frozen_writes(tmp_path):
    with pytest.raises(backup.BackupRefused, match="confirm-writes-frozen"):
        backup.create_backup("postgresql://unused", tmp_path, tmp_path / "out", confirm_writes_frozen=False)


def test_drill_refuses_non_disposable_targets(monkeypatch):
    monkeypatch.delenv("CLOUD_DRILL_DISPOSABLE", raising=False)
    with pytest.raises(backup_drill.DrillRefused, match="CLOUD_DRILL_DISPOSABLE"):
        backup_drill.run_drill("postgresql://u@127.0.0.1/db")
    monkeypatch.setenv("CLOUD_DRILL_DISPOSABLE", "yes")
    for name in backup_drill._INDIRECTION_ENV:
        monkeypatch.delenv(name, raising=False)
    for host in ("db.example.com", "10.0.0.5", "127.0.0.1,db.example.com", "localhost"):
        with pytest.raises(backup_drill.DrillRefused, match="loopback"):
            backup_drill.run_drill(f"host={host} user=u dbname=db")
    for dsn in ("host=127.0.0.1 hostaddr=10.0.0.5 user=u dbname=db", "service=prod user=u",
                "host=127.0.0.1 servicefile=/tmp/x user=u"):
        with pytest.raises(backup_drill.DrillRefused, match="refuses the connection parameter"):
            backup_drill.run_drill(dsn)
    with pytest.raises(backup_drill.DrillRefused, match="explicit loopback host"):
        backup_drill.run_drill("user=u dbname=db")
    for name in ("PGHOST", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.setenv(name, "10.0.0.5")
        with pytest.raises(backup_drill.DrillRefused, match=f"Unset {name}"):
            backup_drill.run_drill("host=127.0.0.1 user=u dbname=db")
        monkeypatch.delenv(name)


# ------------------------------------------------------------- database tests


@pytest.fixture
def seeded(tmp_path):
    """A 0009 database with owner, runtime and backup roles and one synthetic Library item + audio.

    Same disposable-server convention as every other database test (TEST_POSTGRES_DSN);
    the drill's own loopback guard applies to run_drill, not to this fixture.
    """
    base = _base_dsn()
    cluster = backup_drill._Cluster(base, tmp_path)
    try:
        cluster.exec(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER CREATEROLE").format(
            sql.Identifier(cluster.owner), sql.Literal(backup_drill.SYNTHETIC_PASSWORD)))
        database = cluster.new_database("t")
        owner_dsn = cluster.dsn(database, cluster.owner)
        migrations.apply(owner_dsn, target=max(PHASE1_PINNED))
        roles.create_runtime_role(owner_dsn, cluster.runtime)
        for role in (cluster.runtime,):
            cluster.exec(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(backup_drill.SYNTHETIC_PASSWORD)))
        roles.grant_runtime(owner_dsn, cluster.runtime)
        assert backup.create_backup_role(cluster.superuser(database), cluster.backup) is True
        assert backup.create_backup_role(cluster.superuser(database), cluster.backup) is False
        cluster.exec(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(cluster.backup), sql.Literal(backup_drill.SYNTHETIC_PASSWORD)))
        media = tmp_path / "media"
        media.mkdir()
        library, _ = backup_drill._repositories(cluster.dsn(database, cluster.runtime), media)
        with psycopg.connect(owner_dsn, autocommit=True) as connection:
            connection.execute("INSERT INTO cloud_users (user_id, created_at) VALUES (%s, now())", (OWNER,))
        item = library.create_for_owner(OWNER, backup_drill._lesson("Backup test"))
        library.put_audio_for_owner(OWNER, item.id, filename="a.mp3", content_type="audio/mpeg",
                                    content=b"synthetic-audio" * 100)
        cluster.database = database
        cluster.media = media
        cluster.item = item
        yield cluster
    finally:
        cluster.teardown()


def test_backup_role_is_separate_from_runtime_and_owner(seeded):
    database = seeded.database
    with psycopg.connect(seeded.dsn(database, seeded.backup)) as connection:
        assert backup.backup_role_problems(connection) == []
    with psycopg.connect(seeded.dsn(database, seeded.runtime)) as connection:
        problems = " ".join(backup.backup_role_problems(connection))
        assert "lacks BYPASSRLS" in problems
        assert "backup role can INSERT/UPDATE/DELETE cloud_library_items" in problems
    with psycopg.connect(seeded.dsn(database, seeded.owner)) as connection:
        problems = " ".join(backup.backup_role_problems(connection))
        assert "lacks BYPASSRLS" in problems and "owns relations" in problems
    with psycopg.connect(seeded.superuser(database)) as connection:
        assert any("SUPERUSER" in p for p in backup.backup_role_problems(connection))
    report = backup.role_separation(seeded.dsn(database, seeded.owner), seeded.runtime, seeded.backup)
    assert report["ok"], report["failures"]
    assert report["table_owners"] == [seeded.owner]
    bad = backup.role_separation(seeded.dsn(database, seeded.owner), seeded.runtime, seeded.runtime)
    assert not bad["ok"] and "runtime and backup roles are the same role" in bad["failures"]


def test_backup_role_with_column_level_write_grant_is_refused(seeded):
    seeded.exec(sql.SQL("GRANT UPDATE (updated_at) ON cloud_library_items TO {}").format(
        sql.Identifier(seeded.backup)), dbname=seeded.database)
    with psycopg.connect(seeded.dsn(seeded.database, seeded.backup)) as connection:
        assert "backup role has column-level UPDATE on cloud_library_items" in backup.backup_role_problems(connection)
    report = backup.role_separation(seeded.dsn(seeded.database, seeded.owner), seeded.runtime, seeded.backup)
    assert not report["ok"]


def test_backup_role_with_temporary_or_other_schema_create_is_refused(seeded):
    with psycopg.connect(seeded.dsn(seeded.database, seeded.backup)) as connection:
        assert backup.backup_role_problems(connection) == []  # provisioning revoked TEMPORARY from PUBLIC
    seeded.exec(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO PUBLIC").format(sql.Identifier(seeded.database)))
    seeded.exec("CREATE SCHEMA scratch", dbname=seeded.database)
    seeded.exec(sql.SQL("GRANT CREATE ON SCHEMA scratch TO {}").format(sql.Identifier(seeded.backup)),
                dbname=seeded.database)
    with psycopg.connect(seeded.dsn(seeded.database, seeded.backup)) as connection:
        problems = backup.backup_role_problems(connection)
    assert any("TEMPORARY" in p for p in problems)
    assert "backup role can CREATE in schema scratch" in problems


def test_media_listing_refuses_an_unreadable_directory(tmp_path, monkeypatch):
    root = tmp_path / "media"
    (root / "owner" / "item").mkdir(parents=True)
    (root / "owner" / "item" / "obj").write_bytes(b"x")

    def failing_walk(top, onerror=None, followlinks=False):
        onerror(PermissionError(13, "Permission denied", str(root / "owner")))
        return iter(())

    monkeypatch.setattr(backup.os, "walk", failing_walk)
    with pytest.raises(backup.BackupRefused, match="cannot be read"):
        backup._media_listing(root)


def test_backup_role_with_write_privilege_is_refused(seeded):
    seeded.exec(sql.SQL("GRANT DELETE ON cloud_users TO {}").format(sql.Identifier(seeded.backup)),
                dbname=seeded.database)
    with psycopg.connect(seeded.dsn(seeded.database, seeded.backup)) as connection:
        assert "backup role can DELETE cloud_users" in backup.backup_role_problems(connection)


def test_backup_restore_round_trip_and_refusals(seeded, tmp_path):
    _require_tools(seeded.base)
    backup_dsn = seeded.dsn(seeded.database, seeded.backup)
    result = backup.create_backup(backup_dsn, seeded.media, tmp_path / "out", confirm_writes_frozen=True)
    assert result.media_objects == 1 and result.rows > 0
    manifest = backup.verify_backup(result.path)
    assert [row["version"] for row in manifest["ledger"]] == list(PHASE1_PINNED)
    assert manifest["media"]["classes"]["assets"] == 1

    # Refusals: non-empty target database, non-empty target media root.
    non_empty_media = tmp_path / "busy"
    non_empty_media.mkdir()
    (non_empty_media / "x").write_bytes(b"x")
    target = seeded.new_database("r1")
    with pytest.raises(backup.BackupRefused, match="empty directory"):
        backup.restore_backup(result.path, seeded.superuser(target), non_empty_media)
    with pytest.raises(backup.BackupRefused, match="not empty"):
        backup.restore_backup(result.path, seeded.superuser(seeded.database), tmp_path / "m0")
    with pytest.raises(backup.BackupRefused, match="cannot bypass"):
        backup.restore_backup(result.path, seeded.dsn(target, seeded.runtime), tmp_path / "m1")

    report = backup.restore_backup(result.path, seeded.superuser(target), tmp_path / "m2",
                                   live_media_root=seeded.media)
    assert report.ledger == list(PHASE1_PINNED) and report.media_objects == 1
    library, _ = backup_drill._repositories(seeded.dsn(target, seeded.runtime), tmp_path / "m2")
    assert [item.id for item in library.list_for_owner(OWNER)] == [seeded.item.id]


def test_backup_refuses_when_referenced_media_is_missing_or_corrupt(seeded, tmp_path):
    _require_tools(seeded.base)
    backup_dsn = seeded.dsn(seeded.database, seeded.backup)
    [stored] = [p for p in seeded.media.rglob("*") if p.is_file()]
    stored.write_bytes(b"corrupted")
    with pytest.raises(backup.BackupInvalid, match="does not match its metadata"):
        backup.create_backup(backup_dsn, seeded.media, tmp_path / "out", confirm_writes_frozen=True)
    stored.unlink()
    with pytest.raises(backup.BackupInvalid, match="missing"):
        backup.create_backup(backup_dsn, seeded.media, tmp_path / "out", confirm_writes_frozen=True)
    assert not any((tmp_path / "out").glob("*.partial"))
    assert not any((tmp_path / "out").glob("cloud-backup-*"))


def test_restore_rejects_a_backup_whose_rows_differ_from_its_manifest(seeded, tmp_path):
    _require_tools(seeded.base)
    result = backup.create_backup(seeded.dsn(seeded.database, seeded.backup), seeded.media,
                                  tmp_path / "out", confirm_writes_frozen=True)
    manifest = json.loads((result.path / "manifest.json").read_text())
    manifest["tables"]["cloud_library_items"]["sha256"] = "0" * 64
    (result.path / "manifest.json").write_text(json.dumps(manifest))
    backup._write_manifest_sha(result.path)
    target = seeded.new_database("r2")
    with pytest.raises(backup.BackupInvalid, match="Restored rows differ.*cloud_library_items"):
        backup.restore_backup(result.path, seeded.superuser(target), tmp_path / "m")


def test_full_drill_backup_migrate_restore_remigrate(tmp_path):
    base = _base_dsn()
    _require_tools(base)
    if os.getenv("CLOUD_DRILL_DISPOSABLE") != "yes":
        # The drill's guard needs a loopback server (not a published container port);
        # the dedicated workflow provides one and makes this a failure, not a skip.
        if os.getenv("CLOUD_BACKUP_REQUIRE_PG_TOOLS") == "1":
            pytest.fail("CLOUD_BACKUP_REQUIRE_PG_TOOLS=1 but CLOUD_DRILL_DISPOSABLE is not set")
        pytest.skip("Set CLOUD_DRILL_DISPOSABLE=yes on a loopback disposable server to run the full drill")
    report = backup_drill.run_drill(base, tmp_path / "drill")
    failed = [entry for entry in report["checks"] if not entry["ok"]]
    assert report["ok"] and not failed, failed
    names = {entry["check"] for entry in report["checks"]}
    top = list(approved_migrations())[-1]
    assert report["approved_top"] == top
    assert "owner pg_dump without RLS bypass fails (H-1)" in names
    assert "data-loss boundary: write after B0 is absent from the restore" in names
    if "0014" in approved_migrations():
        assert any(name.startswith("M-1:") for name in names)
        assert any(name.startswith("0014 rollback R2") for name in names)
        assert "0014: restore of the --enable-row-security dump is rejected" in names
