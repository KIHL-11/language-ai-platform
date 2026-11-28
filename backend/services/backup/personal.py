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

FORMAT = "language-ai-personal-backup/2"  # /2 adds per-file copy verification and SQLite logical digests
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


class AtomicPublishUnsupported(BackupRefused):
    """This platform or filesystem cannot publish without risking an overwrite."""


_RENAME_NOREPLACE = 1      # Linux renameat2 flag
_RENAME_EXCL = 0x00000004  # macOS renamex_np / renameatx_np flag
_AT_FDCWD_LINUX = -100
_AT_FDCWD_DARWIN = -2


def _at_fdcwd() -> int:
    import sys

    return _AT_FDCWD_DARWIN if sys.platform == "darwin" else _AT_FDCWD_LINUX


def _rename_noreplace(source: Path, target: Path) -> None:
    """Atomically rename ``source`` to ``target``; never replace an existing target."""
    _rename_noreplace_at(None, source, None, target)


def _rename_noreplace_at(src_dir_fd: int | None, source, dst_dir_fd: int | None, target) -> None:
    """Atomic no-replace rename, optionally relative to directory descriptors.

    A plain ``os.rename`` silently replaces an existing empty directory, and an
    ``exists()`` check before it is a race. Only kernel-level no-replace renames
    are used (Linux ``renameat2(RENAME_NOREPLACE)``, macOS
    ``renameatx_np(RENAME_EXCL)``); anything else fails closed.
    """
    import ctypes
    import errno
    import sys

    src, dst = os.fsencode(str(source)), os.fsencode(str(target))
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as exc:
        raise AtomicPublishUnsupported("Cannot load libc for an atomic no-replace rename") from exc
    cwd = _at_fdcwd()
    from_fd = cwd if src_dir_fd is None else src_dir_fd
    to_fd = cwd if dst_dir_fd is None else dst_dir_fd
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(from_fd, src, to_fd, dst, _RENAME_NOREPLACE)
    elif sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        result = libc.renameatx_np(from_fd, src, to_fd, dst, _RENAME_EXCL)
    else:
        raise AtomicPublishUnsupported(
            f"No atomic no-replace rename on {sys.platform}; refusing to publish")
    if result == 0:
        return
    code = ctypes.get_errno()
    if code in (errno.EEXIST, errno.ENOTEMPTY):
        raise BackupRefused(f"{os.path.basename(str(target))} already exists; refusing to overwrite")
    if code in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)):
        raise AtomicPublishUnsupported(
            f"The filesystem does not support atomic no-replace rename (errno {code})")
    raise OSError(code, os.strerror(code), str(target))


def service_is_listening(port: int) -> bool:
    """Best-effort refusal only: is anything accepting on loopback IPv4 or IPv6?

    A closed port is NOT proof that the app is stopped (another port, another
    interface, or a process mid-write). The operator's explicit confirmation is
    the real precondition; this probe only catches the obvious mistake.
    """
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.5)
                if probe.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue  # family unavailable on this host
    return False


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


def _copy_bytes(source: Path, target: Path) -> str:
    """Raw streaming copy; returns the SHA-256 of the bytes read from source."""
    digest = hashlib.sha256()
    with source.open("rb") as reader, target.open("xb") as writer:
        for block in iter(lambda: reader.read(CHUNK), b""):
            digest.update(block)
            writer.write(block)
    return digest.hexdigest()


def _copy_file_verified(source: Path, target: Path, expected: str | None = None) -> str:
    """Copy, then re-read the target from disk; any byte difference fails."""
    read_digest = _copy_bytes(source, target)
    if expected is not None and read_digest != expected:
        raise BackupInvalid(f"Source changed or was corrupt while copying {source.name}")
    if _sha256(target) != read_digest:
        raise BackupInvalid(f"Copy verification failed for {target.name}")
    return read_digest


def sqlite_logical_digest(path: Path) -> str:
    """SHA-256 of the database's SQL dump; equal content gives an equal digest."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        digest = hashlib.sha256()
        for line in connection.iterdump():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()
    finally:
        connection.close()


def _sqlite_integrity(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        return f"error: {exc}"
    finally:
        connection.close()


def _sqlite_backup(staged: Path, target: Path) -> None:
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


def _copy_sqlite(source: Path, target: Path, staging: Path) -> None:
    """Consistent single-file copy that never opens the live database.

    SQLite may create -wal/-shm files even for a read-only connection, so the
    live file and its sidecar journals are only byte-copied (verified) into a
    private staging directory; the backup API then runs against that copy
    (recovering committed WAL content) into rollback-journal mode, and the
    result must pass integrity_check and match the staged copy's logical dump.
    """
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / source.name
    _copy_file_verified(source, staged)
    for suffix in SQLITE_SIDECARS:
        sidecar = Path(f"{source}{suffix}")
        if sidecar.exists():
            _copy_file_verified(sidecar, Path(f"{staged}{suffix}"))
    _sqlite_backup(staged, target)
    integrity = _sqlite_integrity(target)
    if integrity != "ok":
        raise BackupInvalid(f"SQLite copy of {source.name} failed integrity_check: {integrity}")
    if sqlite_logical_digest(target) != sqlite_logical_digest(staged):
        raise BackupInvalid(f"SQLite copy of {source.name} differs from its source")
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
            relative = path.relative_to(directory).as_posix()
            try:
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
                databases[relative] = {"integrity": check, "rows": tables,
                                       "logical_sha256": sqlite_logical_digest(path)}
            except sqlite3.DatabaseError as exc:
                # A damaged database must compare unequal, never crash the check.
                databases[relative] = {"integrity": f"error: {exc}"}
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
    confirm_app_stopped: bool,
    port: int | None = DEFAULT_PORT,
    now: datetime | None = None,
) -> BackupResult:
    """Back up ``storage``. The operator must have stopped the app and say so.

    ``confirm_app_stopped`` is the precondition; the loopback port probe is an
    additional best-effort refusal and never proves the app is stopped.
    """
    if confirm_app_stopped is not True:
        raise BackupRefused("Stop the app first and confirm it explicitly (confirm_app_stopped=True)")
    storage, destination = Path(storage), Path(destination)
    if not storage.is_dir():
        raise BackupRefused("Storage directory does not exist")
    if storage.is_symlink():
        raise BackupRefused("Storage directory is a symbolic link")
    if _is_inside(destination, storage) or _is_inside(storage, destination):
        raise BackupRefused("Backup destination must be outside the storage directory")
    if port is not None and service_is_listening(port):
        raise BackupRefused(f"Something is listening on loopback port {port}; stop the app before backing up")
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
                _copy_file_verified(source, target)
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
        _rename_noreplace(partial, final)
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


_FORBIDDEN_NAME_CHARS = frozenset("\\\x00") | frozenset(chr(c) for c in range(0x20)) | {"\x7f"}


def _safe_relative(name: str) -> tuple[str, ...]:
    """Validate a manifest path: relative POSIX, no traversal, no odd separators."""
    if not name or name.startswith("/") or any(ch in _FORBIDDEN_NAME_CHARS for ch in name):
        raise BackupInvalid(f"Unsafe path in manifest: {name!r}")
    parts = tuple(name.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise BackupInvalid(f"Unsafe path in manifest: {name!r}")
    if name != METADATA and parts[0] != "storage":
        raise BackupInvalid(f"Unexpected path in manifest: {name!r}")
    if parts[0] == "storage" and len(parts) < 2:
        raise BackupInvalid(f"Unsafe path in manifest: {name!r}")
    return parts


def _no_symlink_components(root: Path, parts: tuple[str, ...]) -> Path:
    """Return root/parts after checking that no component (including the leaf) is a symlink."""
    current = root
    for part in parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise BackupInvalid(f"Files missing from backup: {'/'.join(parts)}") from exc
        if stat.S_ISLNK(mode):
            raise BackupInvalid(f"Backup contains a symbolic link: {'/'.join(parts)}")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise BackupInvalid(f"Not a regular file in backup: {'/'.join(parts)}")
    return current


@dataclass(frozen=True, slots=True)
class VerifiedBackup:
    root: Path
    metadata: dict
    entries: dict[str, str]  # manifest path -> SHA-256, from the single verified read


def _verify_backup_trusted(backup: Path) -> VerifiedBackup:
    """One pass: read MANIFEST once, validate every path, hash every file once.

    ``backup.json`` is parsed from the same bytes that were hashed, so nothing
    is read again after verification. Callers must use the returned entries
    and never re-read the manifest.
    """
    root = Path(backup).resolve(strict=True)
    manifest_path = root / MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupInvalid("MANIFEST.sha256 is missing")
    entries: dict[str, str] = {}
    for line in manifest_path.read_bytes().decode("utf-8").splitlines():
        if not line.strip():
            continue
        digest, separator, relative = line.partition("  ")
        if not separator or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise BackupInvalid("Malformed manifest line")
        _safe_relative(relative)
        if relative in entries:
            raise BackupInvalid(f"Duplicate manifest entry: {relative}")
        entries[relative] = digest
    if METADATA not in entries:
        raise BackupInvalid("backup.json is not in the manifest")
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BackupInvalid(f"Backup contains a symbolic link: {path.relative_to(root).as_posix()}")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    actual.discard(MANIFEST)
    missing, extra = sorted(set(entries) - actual), sorted(actual - set(entries))
    if missing:
        raise BackupInvalid("Files missing from backup: " + ", ".join(missing))
    if extra:
        raise BackupInvalid("Files not in manifest: " + ", ".join(extra))
    metadata_bytes = b""
    for relative, digest in entries.items():
        path = _no_symlink_components(root, _safe_relative(relative))
        if relative == METADATA:
            metadata_bytes = path.read_bytes()
            actual_digest = hashlib.sha256(metadata_bytes).hexdigest()
        else:
            actual_digest = _sha256(path)
        if actual_digest != digest:
            raise BackupInvalid(f"Checksum mismatch: {relative}")
    try:
        metadata = json.loads(metadata_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupInvalid("backup.json is not valid JSON") from exc
    if not isinstance(metadata, dict) or metadata.get("format") != FORMAT:
        raise BackupInvalid("Unsupported backup format")
    return VerifiedBackup(root=root, metadata=metadata, entries=dict(entries))


def verify_backup(backup: Path) -> dict:
    """Check every file against MANIFEST.sha256 and that nothing is missing or extra."""
    return _verify_backup_trusted(backup).metadata


class ContainmentUnsupported(BackupRefused):
    """This platform cannot do descriptor-relative restore writes; nothing was written."""


class ContainmentViolation(BackupInvalid):
    """The staging tree was moved, replaced or changed by someone else during the restore."""


_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def _dir_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _O_CLOEXEC


def _require_descriptor_ops() -> None:
    """Fail closed unless every directory-descriptor-relative primitive exists."""
    needed_dir_fd = (os.open, os.mkdir, os.unlink, os.rmdir, os.stat)
    if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
            or not all(function in os.supports_dir_fd for function in needed_dir_fd)
            or os.stat not in os.supports_follow_symlinks
            or os.listdir not in os.supports_fd):
        raise ContainmentUnsupported(
            "This platform lacks directory-descriptor-relative file operations; refusing to restore")


def _ident(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _create_file_at(dir_fd: int, name: str) -> int:
    """Create a new file in an already-open directory; never follows or reuses a name."""
    return os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _O_CLOEXEC, 0o600,
                   dir_fd=dir_fd)


def _copy_into(source_fd: int, target_fd: int) -> str:
    """Stream source to target through descriptors; returns the SHA-256 of the bytes read."""
    digest = hashlib.sha256()
    while True:
        block = os.read(source_fd, CHUNK)
        if not block:
            break
        digest.update(block)
        view = memoryview(block)
        while view:
            view = view[os.write(target_fd, view):]
    return digest.hexdigest()


def _hash_fd(fd: int) -> str:
    """SHA-256 of an open file from offset 0, without reopening it by name."""
    digest, offset = hashlib.sha256(), 0
    while True:
        block = os.pread(fd, CHUNK, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


class _SourceTree:
    """Read-only access to a verified backup's ``storage/`` through directory descriptors."""

    def __init__(self, root: Path):
        self.fds: dict[tuple[str, ...], int] = {}
        top = os.open(root, os.O_RDONLY | os.O_DIRECTORY | _O_CLOEXEC)
        try:
            self.fds[()] = os.open("storage", _dir_flags(), dir_fd=top)
        finally:
            os.close(top)

    def open_file(self, parts: tuple[str, ...]) -> int:
        for depth in range(1, len(parts)):
            key = parts[:depth]
            if key not in self.fds:
                try:
                    self.fds[key] = os.open(key[-1], _dir_flags(), dir_fd=self.fds[key[:-1]])
                except OSError as exc:
                    raise BackupInvalid(f"Backup contains a symbolic link or bad directory: {'/'.join(key)}") from exc
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | _O_CLOEXEC, dir_fd=self.fds[parts[:-1]])
        except OSError as exc:
            raise BackupInvalid(f"Backup contains a symbolic link or missing file: {'/'.join(parts)}") from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise BackupInvalid(f"Not a regular file in backup: {'/'.join(parts)}")
        return fd

    def close(self) -> None:
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()


class _StagingTree:
    """Private restore staging tree, addressed only through directory descriptors.

    Every directory is opened with O_DIRECTORY|O_NOFOLLOW relative to its
    already-open parent, and every file is created with O_CREAT|O_EXCL|
    O_NOFOLLOW relative to its already-open directory, so a symlink or a
    replaced *name* can never redirect a write. O_NOFOLLOW does NOT stop a
    same-UID process from renaming an already-open directory elsewhere; that
    relocation is detected by re-checking the inode chain from the parent
    descriptor down to the directory before and after every create and again
    before publication, and it fails closed. Cleanup removes only inodes this
    run created. See docs/PERSONAL-BACKUP.md for the threat model and the
    remaining same-UID limitation.
    """

    def __init__(self, parent: Path, prefix: str):
        import secrets

        self.parent = parent
        self.parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | _O_CLOEXEC)
        self.name = prefix + secrets.token_hex(8)
        # key -> (parent key or None for the root, name, fd, (dev, ino))
        self.dirs: dict[tuple[str, ...], tuple] = {}
        # key -> (directory key, name, (dev, ino))
        self.files: dict[tuple[str, ...], tuple] = {}
        self.created_root = False
        self.published = False

    @property
    def path(self) -> Path:
        return self.parent / self.name

    def open_root(self) -> None:
        os.mkdir(self.name, 0o700, dir_fd=self.parent_fd)
        self.created_root = True
        fd = self._open_dir(self.parent_fd, self.name, ())
        info = os.fstat(fd)
        self.dirs[()] = (None, self.name, fd, _ident(info))
        if os.listdir(fd) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ContainmentViolation("The staging directory was replaced before use")
        self.check_chain(())

    @staticmethod
    def _open_dir(parent_fd: int, name: str, key: tuple[str, ...]) -> int:
        try:
            return os.open(name, _dir_flags(), dir_fd=parent_fd)
        except OSError as exc:  # ELOOP / ENOTDIR: a symlink or non-directory was swapped in
            raise ContainmentViolation(
                f"Staging directory {'/'.join(key) or '.'} is a symbolic link or was replaced") from exc

    def _parent_fd_of(self, key: tuple[str, ...]) -> int:
        parent_key = self.dirs[key][0]
        return self.parent_fd if parent_key is None else self.dirs[parent_key][2]

    def _check_link(self, key: tuple[str, ...]) -> None:
        _, name, fd, ident = self.dirs[key]
        try:
            info = os.stat(name, dir_fd=self._parent_fd_of(key), follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is None or not stat.S_ISDIR(info.st_mode) or _ident(info) != ident or _ident(os.fstat(fd)) != ident:
            raise ContainmentViolation(
                f"Staging directory {'/'.join(key) or '.'} was moved or replaced during the restore")

    def check_chain(self, key: tuple[str, ...]) -> None:
        """Every directory from the staging root down to ``key`` is still where we put it."""
        for depth in range(len(key) + 1):
            self._check_link(key[:depth])

    def ensure_dir(self, parts: tuple[str, ...]) -> tuple[str, ...]:
        for depth in range(1, len(parts) + 1):
            key = parts[:depth]
            if key in self.dirs:
                continue
            parent_fd = self.dirs[key[:-1]][2]
            self.check_chain(key[:-1])
            try:
                os.mkdir(key[-1], 0o700, dir_fd=parent_fd)
            except FileExistsError as exc:
                raise ContainmentViolation(f"Unexpected entry in the staging directory: {'/'.join(key)}") from exc
            fd = self._open_dir(parent_fd, key[-1], key)
            self.dirs[key] = (key[:-1], key[-1], fd, _ident(os.fstat(fd)))
            if os.listdir(fd):
                raise ContainmentViolation(f"Staging directory {'/'.join(key)} was replaced before use")
            self.check_chain(key)
        return parts

    def _check_file(self, key: tuple[str, ...]) -> None:
        dir_key, name, ident = self.files[key]
        try:
            info = os.stat(name, dir_fd=self.dirs[dir_key][2], follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is None or not stat.S_ISREG(info.st_mode) or _ident(info) != ident:
            raise ContainmentViolation(f"Restored file {'/'.join(key)} was moved or replaced")

    def create_file(self, dir_key: tuple[str, ...], name: str) -> int:
        self.check_chain(dir_key)
        fd = _create_file_at(self.dirs[dir_key][2], name)
        key = dir_key + (name,)
        self.files[key] = (dir_key, name, _ident(os.fstat(fd)))
        try:
            self.check_chain(dir_key)  # was the directory relocated just before the create?
            self._check_file(key)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def verify(self, expected: dict[str, str]) -> None:
        """The tree reachable from the staging root is exactly our files, with the manifest digests."""
        for key in self.dirs:
            self.check_chain(key)
        extra: list[str] = []

        def walk(key: tuple[str, ...]) -> None:
            fd = self.dirs[key][2]
            for name in sorted(os.listdir(fd)):
                child = key + (name,)
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise BackupInvalid("Restored tree contains a symbolic link")
                if stat.S_ISDIR(info.st_mode) and child in self.dirs and self.dirs[child][3] == _ident(info):
                    walk(child)
                elif stat.S_ISREG(info.st_mode) and child in self.files and self.files[child][2] == _ident(info):
                    continue
                else:
                    extra.append("/".join(child))

        walk(())
        if extra:
            raise BackupInvalid("Unexpected restored files: " + ", ".join(sorted(extra)))
        missing = sorted(r for r in expected if tuple(r.split("/")) not in self.files)
        if missing:
            raise BackupInvalid("Restored files missing: " + ", ".join(missing))
        for relative, digest in sorted(expected.items()):
            key = tuple(relative.split("/"))
            self._check_file(key)
            dir_key, name, ident = self.files[key]
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | _O_CLOEXEC, dir_fd=self.dirs[dir_key][2])
            try:
                if _ident(os.fstat(fd)) != ident:
                    raise ContainmentViolation(f"Restored file {relative} was replaced")
                if _hash_fd(fd) != digest:
                    raise BackupInvalid(f"Restored file does not match the manifest: {relative}")
            finally:
                os.close(fd)

    def fsync_dirs(self) -> None:
        for _, _, fd, _ in self.dirs.values():
            os.fsync(fd)

    def publish(self, target_name: str) -> None:
        import secrets

        self.check_chain(())
        _rename_noreplace_at(self.parent_fd, self.name, self.parent_fd, target_name)
        info = os.stat(target_name, dir_fd=self.parent_fd, follow_symlinks=False)
        if _ident(info) != self.dirs[()][3]:
            # The staging name was swapped between the check and the rename, so
            # what was renamed is not our tree. Take it down again and fail.
            quarantine = f".{target_name}.rejected-{secrets.token_hex(4)}"
            try:
                _rename_noreplace_at(self.parent_fd, target_name, self.parent_fd, quarantine)
                where = f"moved aside to {quarantine}"
            except OSError:
                where = "could not be moved aside"
            raise ContainmentViolation(
                f"The staging directory was replaced at publication; the published directory {where}")
        self.name = target_name
        self.published = True
        os.fsync(self.parent_fd)

    def cleanup(self) -> list[str]:
        """Remove only the inodes this run created; report anything left behind."""
        left: list[str] = []
        for key in sorted(self.files, key=len, reverse=True):
            dir_key, name, ident = self.files[key]
            try:
                fd = self.dirs[dir_key][2]
                if _ident(os.stat(name, dir_fd=fd, follow_symlinks=False)) == ident:
                    os.unlink(name, dir_fd=fd)
            except OSError:
                left.append("/".join(key))
        for key in sorted(self.dirs, key=len, reverse=True):
            _, name, _, ident = self.dirs[key]
            label = "/".join(key) or self.name
            try:
                parent_fd = self._parent_fd_of(key)
                if _ident(os.stat(name, dir_fd=parent_fd, follow_symlinks=False)) == ident:
                    os.rmdir(name, dir_fd=parent_fd)
                else:
                    left.append(f"{label} (moved away by another process)")
            except FileNotFoundError:
                left.append(f"{label} (moved away by another process)")
            except OSError:
                left.append(label)
        if () not in self.dirs and self.created_root:
            left.append(f"{self.name} (created but never opened)")
        return left

    def close(self) -> None:
        for _, _, fd, _ in self.dirs.values():
            os.close(fd)
        self.dirs.clear()
        os.close(self.parent_fd)


def restore_backup(backup: Path, target: Path, *, live_storage: Path | None = None) -> dict:
    """Restore into a new directory, publishing it only if every check passes.

    The data is copied into a private (0700) staging directory next to
    ``target``, using only directory-descriptor-relative operations. Every
    restored file is hashed through its own descriptor and the tree reachable
    from the staging root must equal the backup MANIFEST (same file set, same
    SHA-256), the datasets must reload through the app's stores with the
    backup-time summary (records, SRS state, SQLite integrity and logical
    digest), and the tree is verified once more afterwards. Only then is the
    staging directory renamed to ``target`` with an atomic no-replace rename.
    On any error only the files and directories this run created are removed
    and ``target`` is never published. Never writes into ``live_storage``.
    """
    backup, target = Path(backup), Path(target)
    live = Path(live_storage) if live_storage is not None else default_storage_dir()
    if target.exists():
        raise BackupRefused("Restore target already exists; restore only into a new directory")
    if _is_inside(target, live) or _is_inside(live, target):
        raise BackupRefused("Restore target must be outside the live storage directory")
    if _is_inside(target, backup):
        raise BackupRefused("Restore target must be outside the backup")
    _require_descriptor_ops()
    verified = _verify_backup_trusted(backup)
    metadata = verified.metadata
    prefix = "storage/"
    # Only the entries from the single verified read are used from here on.
    expected = {k[len(prefix):]: v for k, v in verified.entries.items() if k.startswith(prefix)}
    target.parent.mkdir(parents=True, exist_ok=True)
    tree = _StagingTree(target.parent, f".{target.name}.restore-staging-")
    source: _SourceTree | None = None
    try:
        tree.open_root()
        source = _SourceTree(verified.root)
        for relative, digest in sorted(expected.items()):
            parts = tuple(relative.split("/"))
            dir_key = tree.ensure_dir(parts[:-1])
            source_fd = source.open_file(parts)
            try:
                target_fd = tree.create_file(dir_key, parts[-1])
                try:
                    if _copy_into(source_fd, target_fd) != digest:
                        raise BackupInvalid(f"Source changed or was corrupt while copying {parts[-1]}")
                    os.fsync(target_fd)
                    if _hash_fd(target_fd) != digest:
                        raise BackupInvalid(f"Copy verification failed for {parts[-1]}")
                finally:
                    os.close(target_fd)
            finally:
                os.close(source_fd)
        tree.verify(expected)
        restored = summarize(tree.path)
        if restored != metadata["summary"]:
            differing = sorted(k for k in set(restored) | set(metadata["summary"])
                               if restored.get(k) != metadata["summary"].get(k))
            raise BackupInvalid("Restored data differs from the backup summary: " + ", ".join(differing))
        tree.verify(expected)  # loading must not have changed anything
        tree.fsync_dirs()
        tree.publish(target.name)
    except BaseException as exc:
        if not tree.published:
            left = tree.cleanup()
            if left:
                exc.add_note("Left in place (not created by this restore, or moved by another process): "
                             + ", ".join(left))
        raise
    finally:
        if source is not None:
            source.close()
        tree.close()
    return {"target": str(target), "datasets": sorted(restored), "summary": restored,
            "files_verified": len(expected), "matches_backup": True}
