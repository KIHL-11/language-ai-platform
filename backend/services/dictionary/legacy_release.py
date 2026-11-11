"""Non-destructive conversion of the flat legacy dictionary into a rollback release.

The installed ``storage/dictionary.sqlite3`` is opened read-only or read as
bytes only. It is never moved, checkpointed, rewritten, or deleted, and its
WAL (if any) is never touched. The copy lives in a new
``dictionary_releases/legacy-<sha>`` directory with a freshly built Sidecar.
"""

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from services.dictionary.forms_sidecar import build_forms_index, lookup_forms
from services.dictionary.provider import SQLiteDictionaryProvider
from services.dictionary.release_manifest import (
    ManifestValidationError,
    _sqlite_health,
    file_sha256,
    validate_rollback_eligibility,
    validate_rollback_release,
)
from services.dictionary.release_store import (
    DICTIONARY_NAME,
    GIB,
    SIDECAR_NAME,
    ReleaseLayout,
    ReleaseStoreError,
    canonical_json,
    create_release_directory,
    disk_budget,
    fsync_directory,
    fsync_file,
    require_free_space,
    seal_release,
    write_new_file_atomic,
)

PROVENANCE_NAME = "legacy-provenance.json"
VERIFICATION_NAME = "rollback-verification.json"


class LegacyConversionError(RuntimeError):
    """The legacy database cannot be converted safely."""


def _sidecar_paths(path: Path) -> dict[str, Path]:
    return {suffix: Path(f"{path}{suffix}") for suffix in ("-wal", "-journal", "-shm")}


def legacy_fingerprint(legacy_database: Path) -> dict:
    """Hash the closed legacy database (and WAL when present) without opening SQLite."""
    if legacy_database.is_symlink() or not legacy_database.is_file():
        raise LegacyConversionError("legacy dictionary must be a regular, non-symlink file")
    companions = _sidecar_paths(legacy_database)
    journal = companions["-journal"]
    if journal.exists() and journal.stat().st_size > 0:
        raise LegacyConversionError(
            "legacy dictionary has a non-empty rollback journal; refusing to copy a possibly "
            "uncommitted database"
        )
    wal = companions["-wal"]
    wal_present = wal.is_file() and wal.stat().st_size > 0
    with legacy_database.open("rb") as handle:
        header = handle.read(100)
    if len(header) < 100 or not header.startswith(b"SQLite format 3\x00"):
        raise LegacyConversionError("legacy dictionary is not an SQLite database")
    # Header bytes 18/19 == 2 mark a WAL-mode database even after a clean close
    # (no -wal file). A byte copy of such a file would reopen in WAL mode.
    wal_mode = header[18] == 2 or header[19] == 2
    stat_result = legacy_database.stat()
    database_sha = file_sha256(legacy_database)
    wal_sha = file_sha256(wal) if wal_present else None
    combined = hashlib.sha256(f"{database_sha}:{wal_sha or ''}".encode()).hexdigest()
    return {
        "sha256": database_sha,
        "size_bytes": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "wal_present": wal_present,
        "wal_mode": wal_mode,
        "wal_sha256": wal_sha,
        "wal_size_bytes": wal.stat().st_size if wal_present else 0,
        "state_sha256": combined,
    }


_MAX_ATTEMPTS = 9


def legacy_build_id(fingerprint: dict, attempt: int = 1) -> str:
    base = f"legacy-{fingerprint['state_sha256'][:16]}"
    return base if attempt == 1 else f"{base}-r{attempt}"


def _byte_copy(source: Path, destination: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o644)
    with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
        for block in iter(lambda: input_file.read(4 * 1024 * 1024), b""):
            output.write(block)
        output.flush()
        os.fsync(output.fileno())


def _backup_copy(source: Path, destination: Path) -> None:
    """Use SQLite's online backup from a read-only source connection."""
    source_connection = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    try:
        source_connection.execute("PRAGMA query_only = ON")
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
            destination_connection.execute("PRAGMA journal_mode = DELETE")
            destination_connection.commit()
        finally:
            destination_connection.close()
    finally:
        source_connection.close()
    fsync_file(destination)


def _smoke_lookup(database: Path, sidecar: Path) -> dict:
    """Prove that the copied release answers real lookups for both languages present."""
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT language, min(id) FROM entries GROUP BY language ORDER BY language"
        ).fetchall()
        samples = []
        for language, entry_id in rows:
            lemma = connection.execute("SELECT lemma FROM entries WHERE id = ?", (entry_id,)).fetchone()[0]
            samples.append((str(language), int(entry_id), str(lemma)))
    finally:
        connection.close()
    if not samples:
        raise LegacyConversionError("legacy dictionary contains no entries")
    provider = SQLiteDictionaryProvider(database, sidecar)
    checked = []
    for language, entry_id, lemma in samples:
        if language not in {"en", "de"}:
            continue
        if not provider.lookup(language, lemma, ""):
            raise LegacyConversionError(f"rollback lookup failed for a {language} sample entry")
        if lookup_forms(database, sidecar, entry_id).status != "available":
            raise LegacyConversionError("rollback forms Sidecar is not available")
        checked.append(language)
    if not checked:
        raise LegacyConversionError("legacy dictionary has no English or German entries")
    return {"languages_checked": checked}


def create_legacy_rollback_release(
    layout: ReleaseLayout,
    legacy_database: Path,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    minimum_headroom_bytes: int = 10 * GIB,
    disk_usage: Callable[[Path], object] = shutil.disk_usage,
) -> dict:
    """Create (or re-verify) the ``legacy-<sha>`` rollback release.

    Requires the backend to be stopped by the caller. Returns a JSON-safe record
    without absolute paths.
    """
    legacy_database = Path(legacy_database)
    if legacy_database.resolve().is_relative_to(layout.releases.resolve()):
        raise LegacyConversionError("legacy dictionary must not live inside dictionary_releases")
    before = legacy_fingerprint(legacy_database)
    # Only a live -wal file needs SQLite's backup API (it holds committed pages
    # not yet in the main file). A WAL-mode header without a -wal file is a
    # cleanly closed database: byte-copy it (opening it read-only would create
    # -wal/-shm beside the installed file) and switch the copy to DELETE mode.
    use_backup = before["wal_present"]
    convert_journal_mode = before["wal_mode"] and not use_backup
    plan_base = {
        "copy_method": (
            "sqlite_backup" if use_backup
            else "byte_copy_then_delete_journal" if convert_journal_mode
            else "byte_copy"
        ),
        "legacy_sha256": before["sha256"],
        "legacy_size_bytes": before["size_bytes"],
        "wal_present": before["wal_present"],
    }
    projected_sidecar = max(1, before["size_bytes"] // 5)
    budget = disk_budget(
        source_bytes=before["size_bytes"] + before["wal_size_bytes"],
        dictionary_bytes=before["size_bytes"] + before["wal_size_bytes"],
        sidecar_bytes=projected_sidecar,
        minimum_headroom_bytes=minimum_headroom_bytes,
    )
    try:
        free_bytes = require_free_space(
            layout.releases if layout.releases.is_dir() else layout.storage,
            budget["required_free_bytes"],
            disk_usage=disk_usage,
        )
    except ReleaseStoreError as exc:
        raise LegacyConversionError(str(exc)) from exc
    plan_base.update({"disk_budget": budget, "free_bytes_at_preflight": free_bytes})
    # An earlier interrupted attempt leaves an incomplete directory without
    # READY. It is preserved for inspection and skipped; never reused or deleted.
    preserved_attempts = []
    build_id = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        name = legacy_build_id(before, attempt)
        candidate = layout.release(name)
        if not (candidate.exists() or candidate.is_symlink()):
            build_id = name
            break
        try:
            validate_rollback_release(layout.releases, name)
        except (ManifestValidationError, OSError):
            preserved_attempts.append(name)
            continue
        provenance = read_legacy_provenance(candidate)
        if provenance and provenance.get("legacy_database", {}).get("state_sha256") == before["state_sha256"]:
            return {**plan_base, "build_id": name, "action": "verified_existing", "dry_run": dry_run,
                    "preserved_incomplete_attempts": preserved_attempts}
        preserved_attempts.append(name)
    if build_id is None:
        raise LegacyConversionError(
            "too many incomplete legacy conversion attempts are preserved; inspect "
            "dictionary_releases before retrying"
        )
    plan = {**plan_base, "build_id": build_id, "preserved_incomplete_attempts": preserved_attempts}
    if dry_run:
        return {**plan, "action": "would_create", "dry_run": True}
    if not layout.releases.is_dir():
        raise ReleaseStoreError("dictionary_releases does not exist")

    release = create_release_directory(layout, build_id)
    database = release / DICTIONARY_NAME
    partial = release / f".{DICTIONARY_NAME}.partial"
    if use_backup:
        _backup_copy(legacy_database, partial)
    else:
        _byte_copy(legacy_database, partial)
        if file_sha256(partial) != before["sha256"]:
            raise LegacyConversionError("rollback byte copy does not match the legacy database")
        if convert_journal_mode:
            copy = sqlite3.connect(partial)
            try:
                if copy.execute("PRAGMA journal_mode = DELETE").fetchone()[0] != "delete":
                    raise LegacyConversionError("rollback copy could not leave WAL mode")
            finally:
                copy.close()
            fsync_file(partial)
    os.replace(partial, database)
    fsync_directory(release)
    for companion in _sidecar_paths(database).values():
        if companion.exists():
            raise LegacyConversionError("rollback copy left SQLite journal state behind")

    actual_budget = disk_budget(
        source_bytes=before["size_bytes"] + before["wal_size_bytes"],
        dictionary_bytes=database.stat().st_size,
        sidecar_bytes=projected_sidecar,
        minimum_headroom_bytes=minimum_headroom_bytes,
    )
    require_free_space(
        layout.releases,
        max(0, actual_budget["required_free_bytes"] - database.stat().st_size),
        disk_usage=disk_usage,
    )

    sidecar = release / SIDECAR_NAME
    build_forms_index(database, sidecar)
    lock = Path(f"{sidecar}.lock")
    if lock.is_file() and not lock.is_symlink():
        lock.unlink()
    actual_budget = disk_budget(
        source_bytes=before["size_bytes"] + before["wal_size_bytes"],
        dictionary_bytes=database.stat().st_size,
        sidecar_bytes=sidecar.stat().st_size,
        minimum_headroom_bytes=minimum_headroom_bytes,
    )
    require_free_space(
        layout.releases,
        max(
            0,
            actual_budget["required_free_bytes"]
            - database.stat().st_size
            - sidecar.stat().st_size,
        ),
        disk_usage=disk_usage,
    )
    try:
        _sqlite_health(database, sidecar)
    except ManifestValidationError as exc:
        raise LegacyConversionError(f"rollback copy failed validation: {exc}") from exc
    smoke = _smoke_lookup(database, sidecar)

    after = legacy_fingerprint(legacy_database)
    if {key: after[key] for key in ("sha256", "size_bytes", "mtime_ns", "wal_sha256")} != {
        key: before[key] for key in ("sha256", "size_bytes", "mtime_ns", "wal_sha256")
    }:
        raise LegacyConversionError("legacy dictionary changed during conversion; refusing READY")

    timestamp = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    provenance = {
        "provenance_version": 1,
        "build_id": build_id,
        "created_at": timestamp,
        "source_provenance": "unverified",
        "copy_method": plan["copy_method"],
        "legacy_database": {
            "name": legacy_database.name,
            "sha256": before["sha256"],
            "size_bytes": before["size_bytes"],
            "mtime_ns": before["mtime_ns"],
            "wal_present": before["wal_present"],
            "wal_sha256": before["wal_sha256"],
            "state_sha256": before["state_sha256"],
        },
        "smoke": smoke,
    }
    write_new_file_atomic(release, PROVENANCE_NAME, canonical_json(provenance))
    verification = {
        "verification_version": 1,
        "build_id": build_id,
        "validated": True,
        "dictionary": {
            "path": DICTIONARY_NAME,
            "sha256": file_sha256(database),
            "size_bytes": database.stat().st_size,
        },
        "forms_sidecar": {
            "path": SIDECAR_NAME,
            "sha256": file_sha256(sidecar),
            "size_bytes": sidecar.stat().st_size,
            "source_identity_verified": True,
        },
    }
    verification_bytes = write_new_file_atomic(release, VERIFICATION_NAME, canonical_json(verification))
    validate_rollback_eligibility(layout.releases, build_id)
    write_new_file_atomic(release, "READY", (hashlib.sha256(verification_bytes).hexdigest() + "\n").encode())
    seal_release(release)
    validate_rollback_release(layout.releases, build_id)
    return {**plan, "action": "created", "dry_run": False}


def read_legacy_provenance(release: Path) -> dict | None:
    path = release / PROVENANCE_NAME
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
