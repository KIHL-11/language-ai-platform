import fcntl
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


FORMS_INDEX_SCHEMA_VERSION = 1
SOURCE_DICTIONARY_SCHEMA_VERSION = 1
FORM_LIMIT = 30
_BUILD_BATCH_SIZE = 2_000


@dataclass(frozen=True, slots=True)
class FormsIndexBuildResult:
    source_rows_scanned: int
    forms_indexed: int
    entries_indexed: int
    size_bytes: int


@dataclass(frozen=True, slots=True)
class FormsLookupResult:
    forms: tuple[str, ...]
    status: str
    message: str | None = None


def default_forms_index_path(source_path: Path) -> Path:
    return source_path.with_name(f"{source_path.stem}.forms.sqlite3")


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _source_identity(path: Path, connection: sqlite3.Connection) -> dict[str, str]:
    stat = path.stat()
    version = connection.execute(
        "SELECT value FROM metadata WHERE key = 'schema_version'"
    ).fetchone()
    if version is None or version["value"] != str(SOURCE_DICTIONARY_SCHEMA_VERSION):
        raise ValueError("Unsupported local dictionary schema")
    return {
        "source_path": str(path.resolve()),
        "source_size": str(stat.st_size),
        "source_mtime_ns": str(stat.st_mtime_ns),
        "source_device": str(stat.st_dev),
        "source_inode": str(stat.st_ino),
        "source_schema_version": str(version["value"]),
    }


def _sidecar_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row["key"]): str(row["value"])
        for row in connection.execute("SELECT key, value FROM metadata")
    }


def _cleanup_build_files(build_path: Path) -> None:
    for path in (build_path, Path(f"{build_path}-journal"), Path(f"{build_path}-wal"), Path(f"{build_path}-shm")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def build_forms_index(
    source_path: Path,
    index_path: Path | None = None,
    *,
    _interrupt_after_rows: int | None = None,
) -> FormsIndexBuildResult:
    """Build a disposable entry_id -> first-30-forms index from a read-only source."""
    source_path = source_path.expanduser()
    index_path = (index_path or default_forms_index_path(source_path)).expanduser()
    if source_path.resolve() == index_path.resolve():
        raise ValueError("Forms index must not replace the source dictionary")
    if not source_path.is_file():
        raise FileNotFoundError("Local dictionary index is not installed")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(f"{index_path}.lock")
    build_path = Path(f"{index_path}.building")

    with lock_path.open("a+b") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another forms-index build is already running") from exc
        _cleanup_build_files(build_path)
        source = _readonly_connection(source_path)
        destination: sqlite3.Connection | None = None
        try:
            identity_before = _source_identity(source_path, source)
            destination = sqlite3.connect(build_path)
            destination.row_factory = sqlite3.Row
            destination.execute("PRAGMA journal_mode = DELETE")
            destination.execute("PRAGMA synchronous = FULL")
            destination.executescript(
                """
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE forms (
                    entry_id INTEGER NOT NULL,
                    form_order INTEGER NOT NULL,
                    form TEXT NOT NULL,
                    PRIMARY KEY(entry_id, form_order)
                ) WITHOUT ROWID;
                """
            )

            source_rows = 0
            forms_indexed = 0
            entries_indexed = 0
            previous_entry_id: int | None = None
            entry_form_count = 0
            batch: list[tuple[int, int, str]] = []
            cursor = source.execute(
                "SELECT id, entry_id, form FROM forms ORDER BY id"
            )
            while rows := cursor.fetchmany(_BUILD_BATCH_SIZE):
                for row in rows:
                    source_rows += 1
                    entry_id = int(row["entry_id"])
                    if previous_entry_id is not None and entry_id < previous_entry_id:
                        raise ValueError(
                            "Source forms are not grouped by entry_id; cannot build bounded index safely"
                        )
                    if entry_id != previous_entry_id:
                        previous_entry_id = entry_id
                        entry_form_count = 0
                        entries_indexed += 1
                    if entry_form_count < FORM_LIMIT:
                        batch.append((entry_id, int(row["id"]), str(row["form"])))
                        forms_indexed += 1
                    entry_form_count += 1
                    if len(batch) >= _BUILD_BATCH_SIZE:
                        destination.executemany(
                            "INSERT INTO forms(entry_id, form_order, form) VALUES (?, ?, ?)",
                            batch,
                        )
                        batch.clear()
                    if _interrupt_after_rows is not None and source_rows >= _interrupt_after_rows:
                        raise InterruptedError("Simulated forms-index build interruption")
            if batch:
                destination.executemany(
                    "INSERT INTO forms(entry_id, form_order, form) VALUES (?, ?, ?)",
                    batch,
                )

            identity_after = _source_identity(source_path, source)
            if identity_after != identity_before:
                raise RuntimeError("Source dictionary changed during forms-index build")
            metadata = {
                "schema_version": str(FORMS_INDEX_SCHEMA_VERSION),
                **identity_after,
                "source_rows_scanned": str(source_rows),
                "forms_indexed": str(forms_indexed),
                "entries_indexed": str(entries_indexed),
            }
            destination.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                metadata.items(),
            )
            destination.commit()
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Forms index failed SQLite integrity_check")
            plan = " ".join(
                str(row[3])
                for row in destination.execute(
                    "EXPLAIN QUERY PLAN SELECT form FROM forms WHERE entry_id = ? ORDER BY form_order LIMIT ?",
                    (0, FORM_LIMIT),
                )
            )
            if "SEARCH forms USING PRIMARY KEY" not in plan:
                raise RuntimeError("Forms index query is not using its entry_id primary key")
            destination.close()
            destination = None
            with build_path.open("rb") as built_file:
                os.fsync(built_file.fileno())
            os.replace(build_path, index_path)
            directory_fd = os.open(index_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return FormsIndexBuildResult(
                source_rows_scanned=source_rows,
                forms_indexed=forms_indexed,
                entries_indexed=entries_indexed,
                size_bytes=index_path.stat().st_size,
            )
        finally:
            if destination is not None:
                destination.close()
            source.close()
            _cleanup_build_files(build_path)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def lookup_forms(
    source_path: Path,
    index_path: Path,
    entry_id: int,
) -> FormsLookupResult:
    if not index_path.is_file():
        return FormsLookupResult(
            forms=(),
            status="index_unavailable",
            message="Build the local dictionary forms index to load inflections.",
        )
    try:
        source = _readonly_connection(source_path)
        try:
            source_identity = _source_identity(source_path, source)
        finally:
            source.close()
        sidecar = _readonly_connection(index_path)
        try:
            metadata = _sidecar_metadata(sidecar)
            if metadata.get("schema_version") != str(FORMS_INDEX_SCHEMA_VERSION):
                return FormsLookupResult(
                    forms=(),
                    status="index_invalid",
                    message="The local dictionary forms index is invalid and must be rebuilt.",
                )
            if any(metadata.get(key) != value for key, value in source_identity.items()):
                return FormsLookupResult(
                    forms=(),
                    status="index_stale",
                    message="The local dictionary forms index is out of date and must be rebuilt.",
                )
            forms = tuple(
                str(row["form"])
                for row in sidecar.execute(
                    "SELECT form FROM forms WHERE entry_id = ? ORDER BY form_order LIMIT ?",
                    (entry_id, FORM_LIMIT),
                )
            )
            return FormsLookupResult(forms=forms, status="available")
        finally:
            sidecar.close()
    except (OSError, sqlite3.DatabaseError, ValueError):
        return FormsLookupResult(
            forms=(),
            status="index_invalid",
            message="The local dictionary forms index cannot be read and must be rebuilt.",
        )
