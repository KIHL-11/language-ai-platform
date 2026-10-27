"""Non-destructive conversion of the flat legacy dictionary into a rollback release.

The installed ``storage/dictionary.sqlite3`` is opened read-only or read as
bytes only. It is never moved, checkpointed, rewritten, or deleted, and its
WAL (if any) is never touched. The copy lives in a new
``dictionary_releases/legacy-<sha>`` directory with a freshly built Sidecar.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from services.dictionary.forms_sidecar import build_forms_index, lookup_forms
from services.dictionary.provider import SQLiteDictionaryProvider
from services.dictionary.release_manifest import (
    ManifestValidationError,
    _sqlite_health,
    file_sha256,
    validate_rollback_release,
)
from services.dictionary.release_store import (
    DICTIONARY_NAME,
    SIDECAR_NAME,
    ReleaseLayout,
    ReleaseStoreError,
    canonical_json,
    create_release_directory,
    fsync_directory,
    fsync_file,
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
    stat_result = legacy_database.stat()
    database_sha = file_sha256(legacy_database)
    wal_sha = file_sha256(wal) if wal_present else None
    combined = hashlib.sha256(f"{database_sha}:{wal_sha or ''}".encode()).hexdigest()
    return {
        "sha256": database_sha,
        "size_bytes": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "wal_present": wal_present,
        "wal_sha256": wal_sha,
        "state_sha256": combined,
    }


def legacy_build_id(fingerprint: dict) -> str:
    return f"legacy-{fingerprint['state_sha256'][:16]}"


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
) -> dict:
    """Create (or re-verify) the ``legacy-<sha>`` rollback release.

    Requires the backend to be stopped by the caller. Returns a JSON-safe record
    without absolute paths.
    """
    legacy_database = Path(legacy_database)
    if legacy_database.resolve().is_relative_to(layout.releases.resolve()):
        raise LegacyConversionError("legacy dictionary must not live inside dictionary_releases")
    before = legacy_fingerprint(legacy_database)
    build_id = legacy_build_id(before)
    release = layout.release(build_id)
    plan = {
        "build_id": build_id,
        "copy_method": "sqlite_backup" if before["wal_present"] else "byte_copy",
        "legacy_sha256": before["sha256"],
        "legacy_size_bytes": before["size_bytes"],
        "wal_present": before["wal_present"],
    }
    if release.exists() or release.is_symlink():
        try:
            validate_rollback_release(layout.releases, build_id)
        except (ManifestValidationError, OSError) as exc:
            raise LegacyConversionError(
                f"{build_id} exists but is not a valid rollback release; it was preserved "
                "for inspection and was not modified"
            ) from exc
        return {**plan, "action": "verified_existing", "dry_run": dry_run}
    if dry_run:
        return {**plan, "action": "would_create", "dry_run": True}
    if not layout.releases.is_dir():
        raise ReleaseStoreError("dictionary_releases does not exist")

    release = create_release_directory(layout, build_id)
    database = release / DICTIONARY_NAME
    partial = release / f".{DICTIONARY_NAME}.partial"
    if before["wal_present"]:
        _backup_copy(legacy_database, partial)
    else:
        _byte_copy(legacy_database, partial)
    os.replace(partial, database)
    fsync_directory(release)
    for companion in _sidecar_paths(database).values():
        if companion.exists():
            raise LegacyConversionError("rollback copy left SQLite journal state behind")
    if not before["wal_present"] and file_sha256(database) != before["sha256"]:
        raise LegacyConversionError("rollback byte copy does not match the legacy database")

    sidecar = release / SIDECAR_NAME
    build_forms_index(database, sidecar)
    lock = Path(f"{sidecar}.lock")
    if lock.is_file() and not lock.is_symlink():
        lock.unlink()
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
