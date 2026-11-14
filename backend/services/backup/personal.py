"""Backup, verification and isolated restore for Personal Desktop learning data.

The live storage directory is only ever read. Backups are written to a new
``.partial`` directory, hashed, fsynced and then renamed into place; an
existing backup is never overwritten. Restore materializes a backup into a new,
empty directory, never into the live storage, and proves the copy loads through
the application's own store classes with the same records as at backup time.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "language-ai-personal-backup/1"
MANIFEST = "MANIFEST.sha256"
METADATA = "backup.json"
SQLITE_SUFFIXES = (".sqlite3", ".sqlite", ".db")
SKIPPED_SUFFIXES = (".tmp", "-wal", "-shm", "-journal", ".lock")
DEFAULT_PORT = 8000
CHUNK = 1024 * 1024

LIBRARY_FILE = "learning_library.json"
LIBRARY_MEDIA = "library_media"
STUDY_FILE = "study_bank.json"
SPEAKING_FILE = "speaking_sessions.json"
CHUNK_FILE = "chunk_bank.json"


class BackupRefused(RuntimeError):
    """The operation was refused before anything was written."""


class BackupInvalid(RuntimeError):
    """A backup failed verification."""


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    files: int
    bytes: int
    summary: dict


def default_storage_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "storage"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(records: list) -> str:
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def service_is_listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def _source_files(storage: Path) -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(storage, followlinks=False):
        root_path = Path(root)
        for name in list(dirs):
            if (root_path / name).is_symlink():
                raise BackupRefused(f"Symbolic link in storage: {(root_path / name).relative_to(storage)}")
        for name in sorted(names):
            path = root_path / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise BackupRefused(f"Symbolic link in storage: {path.relative_to(storage)}")
            if not stat.S_ISREG(mode):
                raise BackupRefused(f"Unsupported file type in storage: {path.relative_to(storage)}")
            if name.endswith(SKIPPED_SUFFIXES):
                continue  # transient write artifacts; SQLite WAL content is captured by the backup API
            files.append(path)
    return sorted(files)


SQLITE_SIDECARS = ("-wal", "-journal")


def _fingerprint(paths: list[Path]) -> dict[str, tuple[int, int]]:
    """Size and mtime of each file and of any SQLite sidecar journal next to it."""
    result = {}
    for path in paths:
        for candidate in (path, *(Path(f"{path}{suffix}") for suffix in SQLITE_SIDECARS)):
            if candidate.exists():
                info = candidate.stat()
                result[str(candidate)] = (info.st_size, info.st_mtime_ns)
    return result


def _copy_sqlite(source: Path, target: Path, staging: Path) -> None:
    """Consistent single-file copy that never opens the live database.

    SQLite may create -wal/-shm files even for a read-only connection, so the
    live file and its sidecar journals are only byte-copied into a private
    staging directory; the backup API then runs against that copy (recovering
    any committed WAL content) and the result is converted to rollback-journal
    mode so the backup holds exactly one file.
    """
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / source.name
    shutil.copyfile(source, staged)
    for suffix in SQLITE_SIDECARS:
        sidecar = Path(f"{source}{suffix}")
        if sidecar.exists():
            shutil.copyfile(sidecar, Path(f"{staged}{suffix}"))
    source_db = sqlite3.connect(staged)
    try:
        target_db = sqlite3.connect(target)
        try:
            source_db.backup(target_db)
            target_db.execute("PRAGMA journal_mode=DELETE")
        finally:
            target_db.close()
    finally:
        source_db.close()
    shutil.rmtree(staging)


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    for directory in [root, *[p for p in root.rglob("*") if p.is_dir()]]:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def summarize(directory: Path) -> dict:
    """Load each dataset through the application's store classes (read-only)."""
    from services.library.store import LearningLibraryStore
    from services.retrieval.chunk_store import ChunkStore
    from services.speaking.store import SpeakingSessionStore
    from services.study_bank.store import StudyBankStore

    summary: dict = {}
    library_path = directory / LIBRARY_FILE
    if library_path.is_file():
        library = LearningLibraryStore(
            library_path, directory / LIBRARY_MEDIA, directory / ".not-restored-source-media"
        )
        items = library.list_items()
        summary["library"] = {
            "count": len(items),
            "ids": sorted(item.id for item in items),
            "digest": _canonical_digest(sorted((i.model_dump(mode="json") for i in items), key=lambda v: v["id"])),
            "progress_digest": _canonical_digest(sorted(
                ([i.id, i.model_dump(mode="json").get("progress")] for i in items))),
        }
    study_path = directory / STUDY_FILE
    if study_path.is_file():
        study_items = StudyBankStore(study_path)._read_items()
        summary["study_bank"] = {
            "count": len(study_items),
            "ids": sorted(item.id for item in study_items),
            "digest": _canonical_digest(sorted((i.model_dump(mode="json") for i in study_items), key=lambda v: v["id"])),
            "srs_digest": _canonical_digest(sorted(
                [i.id, i.review.model_dump(mode="json")] for i in study_items)),
        }
    speaking_path = directory / SPEAKING_FILE
    if speaking_path.is_file():
        sessions = SpeakingSessionStore(speaking_path)._read()
        summary["speaking"] = {
            "count": len(sessions),
            "events": sum(session.event_count for session in sessions),
            "digest": _canonical_digest(sorted((s.model_dump(mode="json") for s in sessions), key=lambda v: v["id"])),
        }
    chunk_path = directory / CHUNK_FILE
    if chunk_path.is_file():
        chunks = ChunkStore(chunk_path).read_chunks()
        summary["chunk_memory"] = {
            "count": len(chunks),
            "digest": _canonical_digest(sorted(
                (c.model_dump(mode="json") for c in chunks), key=lambda v: json.dumps(v, sort_keys=True))),
        }
    databases = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix in SQLITE_SUFFIXES:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                check = connection.execute("PRAGMA integrity_check").fetchone()[0]
                tables = {
                    name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                    for (name,) in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
                }
            finally:
                connection.close()
            databases[path.relative_to(directory).as_posix()] = {"integrity": check, "rows": tables}
    if databases:
        summary["sqlite"] = databases
    return summary


def _write_manifest(directory: Path) -> tuple[int, int]:
    lines, total = [], 0
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        relative = path.relative_to(directory).as_posix()
        if relative == MANIFEST:
            continue
        total += path.stat().st_size
        lines.append(f"{_sha256(path)}  {relative}")
    (directory / MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines), total


def create_backup(
    storage: Path,
    destination: Path,
    *,
    port: int | None = DEFAULT_PORT,
    now: datetime | None = None,
) -> BackupResult:
    storage, destination = Path(storage), Path(destination)
    if not storage.is_dir():
        raise BackupRefused("Storage directory does not exist")
    if storage.is_symlink():
        raise BackupRefused("Storage directory is a symbolic link")
    if _is_inside(destination, storage) or _is_inside(storage, destination):
        raise BackupRefused("Backup destination must be outside the storage directory")
    if port is not None and service_is_listening(port):
        raise BackupRefused(f"The app is listening on 127.0.0.1:{port}; stop it before backing up")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    final = destination / f"personal-backup-{stamp}"
    partial = destination / f"personal-backup-{stamp}.partial"
    if final.exists() or partial.exists():
        raise BackupRefused(f"{final.name} already exists; refusing to overwrite")
    sources = _source_files(storage)
    before = _fingerprint(sources)
    destination.mkdir(parents=True, exist_ok=True)
    partial.mkdir(mode=0o700)
    try:
        data = partial / "storage"
        data.mkdir()
        staging = partial / ".sqlite-staging"
        for source in sources:
            target = data / source.relative_to(storage)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix in SQLITE_SUFFIXES:
                _copy_sqlite(source, target, staging)
            else:
                shutil.copyfile(source, target)
        if _fingerprint(_source_files(storage)) != before:
            raise BackupRefused("Storage changed while the backup was running; nothing was kept")
        summary = summarize(data)
        metadata = {
            "format": FORMAT,
            "created_at": (now or datetime.now(timezone.utc)).isoformat(),
            "source_files": len(sources),
            "summary": summary,
        }
        (partial / METADATA).write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        files, total = _write_manifest(partial)
        _fsync_tree(partial)
        if final.exists():
            raise BackupRefused(f"{final.name} appeared during the backup; refusing to overwrite")
        os.rename(partial, final)
        descriptor = os.open(destination, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    verify_backup(final)
    return BackupResult(path=final, files=files, bytes=total, summary=summary)


def verify_backup(backup: Path) -> dict:
    """Check every file against MANIFEST.sha256 and that nothing is missing or extra."""
    backup = Path(backup)
    manifest = backup / MANIFEST
    if not manifest.is_file():
        raise BackupInvalid("MANIFEST.sha256 is missing")
    expected: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, separator, relative = line.partition("  ")
        if not separator or len(digest) != 64 or relative.startswith("/") or ".." in Path(relative).parts:
            raise BackupInvalid("Malformed manifest line")
        expected[relative] = digest
    actual = {
        p.relative_to(backup).as_posix() for p in backup.rglob("*")
        if p.is_file() or p.is_symlink()
    } - {MANIFEST}
    if any((backup / name).is_symlink() for name in actual):
        raise BackupInvalid("Backup contains a symbolic link")
    missing, extra = sorted(set(expected) - actual), sorted(actual - set(expected))
    if missing:
        raise BackupInvalid("Files missing from backup: " + ", ".join(missing))
    if extra:
        raise BackupInvalid("Files not in manifest: " + ", ".join(extra))
    for relative, digest in expected.items():
        if _sha256(backup / relative) != digest:
            raise BackupInvalid(f"Checksum mismatch: {relative}")
    metadata = json.loads((backup / METADATA).read_text(encoding="utf-8"))
    if metadata.get("format") != FORMAT:
        raise BackupInvalid("Unsupported backup format")
    return metadata


def restore_backup(backup: Path, target: Path, *, live_storage: Path | None = None) -> dict:
    """Restore into a new, empty directory and prove it loads identically.

    Never writes into ``live_storage`` (default: the app's storage directory).
    Returns a report; raises BackupInvalid when the restored data differs.
    """
    backup, target = Path(backup), Path(target)
    live = Path(live_storage) if live_storage is not None else default_storage_dir()
    if target.exists():
        raise BackupRefused("Restore target already exists; restore only into a new directory")
    if _is_inside(target, live) or _is_inside(live, target):
        raise BackupRefused("Restore target must be outside the live storage directory")
    if _is_inside(target, backup):
        raise BackupRefused("Restore target must be outside the backup")
    metadata = verify_backup(backup)
    target.mkdir(parents=True, mode=0o700)
    shutil.copytree(backup / "storage", target, dirs_exist_ok=True, symlinks=False)
    restored = summarize(target)
    expected = metadata["summary"]
    if restored != expected:
        differing = sorted(k for k in set(restored) | set(expected) if restored.get(k) != expected.get(k))
        raise BackupInvalid("Restored data differs from the backup summary: " + ", ".join(differing))
    return {"target": str(target), "datasets": sorted(restored), "summary": restored, "matches_backup": True}
