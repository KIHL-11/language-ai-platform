"""Automated backup -> migrate -> restore -> re-migrate drill on a DISPOSABLE server.

Refuses to run unless ``CLOUD_DRILL_DISPOSABLE=yes`` and the server is reached
over a loopback address or a local socket. Everything it creates (databases,
roles, media directories) has a random ``lapdrill_`` name and is removed at
the end. Only synthetic owners, lessons, audio bytes and study items are used.

Sequence (versions above 0009 run only when this code approves them):

1. Source database at 0009 with migration owner, runtime role and backup role.
2. Synthetic data for two owners through the real repositories (Library item,
   audio object, Study Bank item).
3. Negative controls: the runtime role and the owner role are refused as
   backup roles; ``pg_dump --enable-row-security`` by the owner exits 0 but
   loses rows, and restoring it is rejected by the fingerprint check.
4. Backup B0 (pre-migration), timed.
5. Migrate to the highest approved version; preflight with that minimum;
   one more write after B0 (this is the data that a restore of B0 loses).
6. Restore B0 into a new database and media root, timed; runtime preflight at
   0009 ok and at the new minimum refused; application reads through the
   repositories (items, audio bytes, study items, owner isolation); the
   post-B0 write is absent (measured data-loss boundary).
7. Re-migrate the restored copy; preflight ok; repositories work under RLS.
8. Backup B1 of the migrated source and restore it (post-migration backup).
9. 0014 rollback options on copies: the down file alone leaves the ledger at
   0014 and preflight fails at every minimum; down plus removal of the 0014
   ledger row in one transaction passes preflight at 0009 with no row lost.
"""
from __future__ import annotations

import hashlib
import json
import ipaddress
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from services.cloud import backup, migrations, preflight, roles
from services.cloud.schema import MIGRATIONS_DIR, PHASE1_PINNED, approved_migrations

SYNTHETIC_PASSWORD = "synthetic-drill-only"
OWNERS = ("lapdrill-owner-a", "lapdrill-owner-b")
AUDIO = {owner: hashlib.sha256(owner.encode()).digest() * 512 for owner in OWNERS}


class DrillRefused(RuntimeError):
    """The drill would not run against a disposable local server."""


def _require_disposable(dsn: str) -> None:
    if os.getenv("CLOUD_DRILL_DISPOSABLE") != "yes":
        raise DrillRefused("Set CLOUD_DRILL_DISPOSABLE=yes to confirm the server is disposable")
    host = (conninfo_to_dict(dsn).get("host") or "").strip()
    hosts = [h for h in host.split(",") if h] or [""]
    for item in hosts:
        if item in {"", "localhost"} or item.startswith("/"):
            continue
        try:
            if ipaddress.ip_address(item).is_loopback:
                continue
        except ValueError:
            pass
        raise DrillRefused("The drill only runs against a loopback or local-socket server")


def _lesson(title: str):
    from services.schemas.lesson import Lesson

    return Lesson.model_validate({
        "id": "lapdrill-lesson",
        "title": title,
        "source_url": f"https://www.youtube.com/watch?v={uuid.uuid4().hex[:11]}",
        "source_language": "en",
        "target_language": "zh-CN",
        "level": "B2",
        "audio_url": None,
        "source": "subtitles",
        "sentences": [{
            "id": 1, "text": "Synthetic drill sentence.", "translation": "合成演练句子。",
            "start": 0.5, "end": 3.0, "keywords": [], "chunks": [], "grammar": "", "ai_status": "ok",
        }],
        "training_order": ["active_recall", "blind_listening", "comprehension", "mini_dictation",
                           "shadowing", "retell", "live_dialogue"],
        "ai": {"provider": "mock", "status": "complete"},
    })


def _study_input(owner: str, text: str):
    from services.schemas.study_bank import AiTextStudyProvenance, StudyItemInput

    return StudyItemInput(
        user_id=owner, kind="chunk", source_language="en", target_language="zh-CN", text=text,
        meaning="合成", usage="synthetic drill",
        provenance=[AiTextStudyProvenance(
            source_text=f"We {text}.", evidence_start=3, evidence_end=3 + len(text))],
    )


def _repositories(runtime_dsn: str, media_root: Path):
    from services.library.postgres import FilesystemMediaObjectStore, PostgresOwnerScopedLibraryRepository
    from services.study_bank.postgres import PostgresOwnerScopedStudyBankRepository

    return (
        PostgresOwnerScopedLibraryRepository(runtime_dsn, FilesystemMediaObjectStore(media_root)),
        PostgresOwnerScopedStudyBankRepository(runtime_dsn),
    )


class _Cluster:
    def __init__(self, superuser_dsn: str, work: Path):
        self.base = superuser_dsn
        self.work = work
        tag = uuid.uuid4().hex[:8]
        self.tag = tag
        self.owner = f"lapdrill_owner_{tag}"
        self.runtime = f"lapdrill_rt_{tag}"
        self.backup = f"lapdrill_bk_{tag}"
        self.databases: list[str] = []
        self.roles = [self.owner, self.runtime, self.backup]

    def exec(self, statement, *, dbname: str | None = None) -> None:
        dsn = make_conninfo(self.base, dbname=dbname) if dbname else self.base
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(statement)

    def dsn(self, database: str, role: str) -> str:
        return make_conninfo(self.base, dbname=database, user=role, password=SYNTHETIC_PASSWORD)

    def superuser(self, database: str) -> str:
        return make_conninfo(self.base, dbname=database)

    def new_database(self, label: str, *, template: str | None = None) -> str:
        name = f"lapdrill_{label}_{self.tag}"
        statement = sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(self.owner))
        if template:
            statement = sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE {}").format(
                sql.Identifier(name), sql.Identifier(self.owner), sql.Identifier(template))
        self.exec(statement)
        self.databases.append(name)
        return name

    def teardown(self) -> None:
        for name in reversed(self.databases):
            try:
                self.exec(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            except psycopg.Error:
                pass
        for role in self.roles:
            try:
                self.exec(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
            except psycopg.Error:
                pass


def _check(results: list[dict], name: str, ok: bool, **detail) -> bool:
    results.append({"check": name, "ok": bool(ok), **detail})
    return bool(ok)


def _expect_refused(callable_, *exceptions) -> str | None:
    try:
        callable_()
    except exceptions as exc:
        return type(exc).__name__
    return None


def _preflight(dsn: str, minimum: str) -> tuple[bool, list[str]]:
    report = preflight.run_preflight(dsn, minimum_version=minimum)
    return report.ok, report.failures


def _trap_check(results, cluster, work: Path, sealed: Path, owner_dsn: str, label: str, version: str) -> None:
    """The --enable-row-security trap: the owner's dump exits 0, loses rows, and is rejected."""
    trap_dir = work / label
    shutil.copytree(sealed, trap_dir)
    dump = trap_dir / backup.DUMP_NAME
    trap = subprocess.run(
        [backup._tool("pg_dump"), "--format=custom", "--no-password", "--enable-row-security",
         f"--file={dump}"],
        env=backup._client_env(owner_dsn), capture_output=True, text=True, timeout=600)
    _check(results, f"{version}: owner pg_dump --enable-row-security exits 0 (silent trap)", trap.returncode == 0)
    manifest = json.loads((trap_dir / backup.MANIFEST_JSON).read_text(encoding="utf-8"))
    manifest["dump"]["sha256"] = backup._sha256_file(dump)[0]
    manifest["dump"]["bytes"] = dump.stat().st_size
    (trap_dir / backup.MANIFEST_JSON).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    backup._write_manifest_sha(trap_dir)
    database = cluster.new_database(label)
    error = None
    try:
        backup.restore_backup(trap_dir, cluster.superuser(database), work / f"media-{label}")
    except backup.BackupInvalid as exc:
        error = str(exc)
    _check(results, f"{version}: restore of the --enable-row-security dump is rejected",
           error is not None and "Restored rows differ" in error, error=(error or "")[:300])


def run_drill(superuser_dsn: str, work_dir: Path | None = None, *, directory: Path = MIGRATIONS_DIR) -> dict:
    _require_disposable(superuser_dsn)
    approved = list(approved_migrations())
    phase1_top = max(PHASE1_PINNED)
    top = approved[-1]
    results: list[dict] = []
    timings: dict[str, float] = {}
    work = Path(work_dir or tempfile.mkdtemp(prefix="lapdrill-"))
    work.mkdir(parents=True, exist_ok=True)
    cluster = _Cluster(superuser_dsn, work)
    started = time.monotonic()
    try:
        cluster.exec(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER CREATEROLE").format(
            sql.Identifier(cluster.owner), sql.Literal(SYNTHETIC_PASSWORD)))
        source = cluster.new_database("src")
        owner_dsn = cluster.dsn(source, cluster.owner)
        migrations.apply(owner_dsn, target=phase1_top, directory=directory)
        roles.create_runtime_role(owner_dsn, cluster.runtime)
        cluster.exec(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(cluster.runtime), sql.Literal(SYNTHETIC_PASSWORD)))
        roles.grant_runtime(owner_dsn, cluster.runtime, directory=directory)
        backup.create_backup_role(cluster.superuser(source), cluster.backup)
        cluster.exec(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
            sql.Identifier(cluster.backup), sql.Literal(SYNTHETIC_PASSWORD)))
        runtime_dsn = cluster.dsn(source, cluster.runtime)
        backup_dsn = cluster.dsn(source, cluster.backup)
        separation = backup.role_separation(owner_dsn, cluster.runtime, cluster.backup)
        _check(results, "role separation: owner / runtime / backup", separation["ok"],
               failures=separation["failures"])

        # Synthetic data through the real repositories.
        media = work / "media-src"
        media.mkdir()
        library, study = _repositories(runtime_dsn, media)
        with psycopg.connect(owner_dsn, autocommit=True) as connection:
            for owner in OWNERS:
                connection.execute("INSERT INTO cloud_users (user_id, created_at) VALUES (%s, now())", (owner,))
        seeded = {}
        for owner in OWNERS:
            item = library.create_for_owner(owner, _lesson(f"Drill lesson {owner[-1]}"))
            library.put_audio_for_owner(owner, item.id, filename="drill.mp3", content_type="audio/mpeg",
                                        content=AUDIO[owner])
            study.save_items_for_owner(owner, [_study_input(owner, f"keep {owner[-1]} going")])
            seeded[owner] = item.id
        ok, failures = _preflight(runtime_dsn, phase1_top)
        _check(results, f"source preflight --minimum {phase1_top}", ok, failures=failures)

        # Negative controls.
        bad = work / "refused"
        refused_runtime = _expect_refused(
            lambda: backup.create_backup(runtime_dsn, media, bad, confirm_writes_frozen=True), backup.BackupRefused)
        _check(results, "runtime role refused as backup role", refused_runtime == "BackupRefused")
        refused_owner = _expect_refused(
            lambda: backup.create_backup(owner_dsn, media, bad, confirm_writes_frozen=True), backup.BackupRefused)
        _check(results, "migration owner refused as backup role", refused_owner == "BackupRefused")
        refused_unfrozen = _expect_refused(
            lambda: backup.create_backup(backup_dsn, media, bad, confirm_writes_frozen=False), backup.BackupRefused)
        _check(results, "backup refused without --confirm-writes-frozen", refused_unfrozen == "BackupRefused")
        plain = subprocess.run(
            [backup._tool("pg_dump"), "--format=custom", "--no-password", f"--file={work / 'owner-plain.dump'}"],
            env=backup._client_env(owner_dsn), capture_output=True, text=True, timeout=600)
        _check(results, "owner pg_dump without RLS bypass fails (H-1)", plain.returncode != 0,
               stderr_tail=(plain.stderr.strip().splitlines() or [""])[-1][:200])

        # Backup B0.
        step = time.monotonic()
        b0 = backup.create_backup(backup_dsn, media, work / "backups", confirm_writes_frozen=True)
        timings["backup_b0_s"] = round(time.monotonic() - step, 3)
        _check(results, "backup B0 created and sealed", True, tables=b0.tables, rows=b0.rows,
               media_objects=b0.media_objects, dump_bytes=b0.dump_bytes)
        manifest_b0 = backup.verify_backup(b0.path)
        _check(results, "backup B0 verifies", manifest_b0["ledger"][-1]["version"] == phase1_top)

        # The --enable-row-security trap, once RLS covers the data: the owner's
        # "successful" dump is missing rows and the restore check rejects it.
        _trap_check(results, cluster, work, b0.path, owner_dsn, "trap", phase1_top)

        # Migrate forward; write after B0.
        if top != phase1_top:
            step = time.monotonic()
            done = migrations.apply(owner_dsn, target=top, directory=directory)
            timings["migrate_source_s"] = round(time.monotonic() - step, 3)
            _check(results, f"source migrated to {top}", done == approved[approved.index(phase1_top) + 1:],
                   applied=done)
            ok, failures = _preflight(runtime_dsn, top)
            _check(results, f"source preflight --minimum {top}", ok, failures=failures)
        late = library.create_for_owner(OWNERS[0], _lesson("Written after B0"))

        # Restore B0 and verify it as data and as application.
        restored = cluster.new_database("restore")
        restored_media = work / "media-restore"
        step = time.monotonic()
        report = backup.restore_backup(b0.path, cluster.superuser(restored), restored_media,
                                       live_media_root=media)
        timings["restore_b0_s"] = round(time.monotonic() - step, 3)
        timings.update({f"restore_b0_{k}": v for k, v in report.timings.items()})
        _check(results, "restore B0: rows, ledger and media match the manifest", True,
               tables=report.tables, rows=report.rows, ledger=report.ledger)
        restored_runtime = cluster.dsn(restored, cluster.runtime)
        ok, failures = _preflight(restored_runtime, phase1_top)
        _check(results, f"restored preflight --minimum {phase1_top}", ok, failures=failures)
        if top != phase1_top:
            ok, failures = _preflight(restored_runtime, top)
            _check(results, f"restored preflight --minimum {top} refused before re-migration", not ok)
        r_library, r_study = _repositories(restored_runtime, restored_media)

        def application_checks(label: str, lib, stu) -> None:
            for owner in OWNERS:
                items = lib.list_for_owner(owner)
                _check(results, f"{label}: {owner[-1]} sees exactly its own seeded item",
                       [item.id for item in items] == [seeded[owner]])
                audio = lib.audio_for_owner(owner, seeded[owner])
                data = Path(audio.path).read_bytes()
                _check(results, f"{label}: {owner[-1]} audio bytes intact",
                       hashlib.sha256(data).hexdigest() == hashlib.sha256(AUDIO[owner]).hexdigest())
                _check(results, f"{label}: {owner[-1]} study items intact", len(stu.list_items(owner)) == 1)
            other = OWNERS[1]
            foreign = _expect_refused(lambda: lib.get_for_owner(other, seeded[OWNERS[0]]), Exception)
            _check(results, f"{label}: cross-owner read refused", foreign is not None)

        application_checks("restored B0", r_library, r_study)
        _check(results, "data-loss boundary: write after B0 is absent from the restore",
               late.id not in {item.id for item in r_library.list_for_owner(OWNERS[0])})

        if top != phase1_top:
            step = time.monotonic()
            done = migrations.apply(cluster.dsn(restored, cluster.owner), target=top, directory=directory)
            timings["re_migrate_s"] = round(time.monotonic() - step, 3)
            ok, failures = _preflight(restored_runtime, top)
            _check(results, f"re-migrated restore preflight --minimum {top}", ok and bool(done), failures=failures)
            application_checks(f"re-migrated restore ({top})", r_library, r_study)
            with psycopg.connect(restored_runtime) as connection:
                hidden = connection.execute("SELECT count(*) FROM cloud_library_items").fetchone()[0]
            _check(results, "re-migrated restore: no owner context sees 0 Library rows", hidden == 0)

        # Post-migration backup and restore.
        step = time.monotonic()
        b1 = backup.create_backup(backup_dsn, media, work / "backups", confirm_writes_frozen=True)
        timings["backup_b1_s"] = round(time.monotonic() - step, 3)
        restored_b1 = cluster.new_database("restore_b1")
        step = time.monotonic()
        report_b1 = backup.restore_backup(b1.path, cluster.superuser(restored_b1), work / "media-restore-b1")
        timings["restore_b1_s"] = round(time.monotonic() - step, 3)
        ok, failures = _preflight(cluster.dsn(restored_b1, cluster.runtime), top)
        _check(results, f"backup/restore at {top}: preflight --minimum {top}", ok,
               ledger=report_b1.ledger, rows=report_b1.rows, failures=failures)
        if top != phase1_top:
            _trap_check(results, cluster, work, b1.path, owner_dsn, "trap_top", top)

        # 0014 rollback options, on copies of the migrated source.
        if "0014" in approved and top >= "0014":
            down_sql = (directory / "0014_library_row_level_security.down.sql").read_text(encoding="utf-8")
            only_down = cluster.new_database("down", template=source)
            with psycopg.connect(cluster.dsn(only_down, cluster.owner), autocommit=True) as connection:
                connection.execute(down_sql, prepare=False)
            low_ok, _ = _preflight(cluster.dsn(only_down, cluster.runtime), phase1_top)
            high_ok, _ = _preflight(cluster.dsn(only_down, cluster.runtime), "0014")
            pending = migrations.plan(cluster.dsn(only_down, cluster.owner), target="0014",
                                      directory=directory).pending_versions
            _check(results, "M-1: 0014 down file alone leaves ledger at 0014 and fails preflight everywhere",
                   (not low_ok) and (not high_ok) and pending == [])
            fixed = cluster.new_database("down_ledger", template=source)
            before = backup.table_fingerprints  # same function as the backup manifest
            with psycopg.connect(cluster.superuser(fixed)) as connection:
                backup._apply_session_settings(connection)
                counts_before = {t: v["rows"] for t, v in before(connection, backup._public_tables(connection)).items()}
            with psycopg.connect(cluster.dsn(fixed, cluster.owner)) as connection:
                body = "\n".join(
                    line for line in down_sql.splitlines() if line.strip().upper() not in {"BEGIN;", "COMMIT;"})
                connection.execute(body, prepare=False)
                connection.execute("DELETE FROM cloud_schema_migrations WHERE version = '0014'")
            with psycopg.connect(cluster.superuser(fixed)) as connection:
                backup._apply_session_settings(connection)
                counts_after = {t: v["rows"] for t, v in before(connection, backup._public_tables(connection)).items()}
            ledger_before = counts_before.pop("cloud_schema_migrations")
            ledger_after = counts_after.pop("cloud_schema_migrations")
            rb_ok, rb_failures = _preflight(cluster.dsn(fixed, cluster.runtime), phase1_top)
            pending = migrations.plan(cluster.dsn(fixed, cluster.owner), target="0014",
                                      directory=directory).pending_versions
            _check(results, "0014 rollback R2 (down + ledger row, one transaction): preflight 0009 ok, no row lost,"
                   " 0014 pending again",
                   rb_ok and counts_before == counts_after and ledger_after == ledger_before - 1
                   and pending == ["0014"],
                   failures=rb_failures, data_rows_equal=counts_before == counts_after, pending=pending)
        timings["total_s"] = round(time.monotonic() - started, 3)
        return {
            "ok": all(entry["ok"] for entry in results),
            "approved_top": top,
            "checks": results,
            "timings": timings,
            "sizes": {"b0_dump_bytes": b0.dump_bytes, "b0_media_bytes": b0.media_bytes,
                      "b1_dump_bytes": b1.dump_bytes},
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        cluster.teardown()
        if work_dir is None:
            shutil.rmtree(work, ignore_errors=True)
