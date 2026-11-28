"""Cloud PostgreSQL backup, verification and isolated restore (operator tooling).

Why this exists: 0002-0004 (and 0014) put tables under ENABLE + FORCE row level
security. FORCE binds the table owner too, so a plain ``pg_dump`` run by the
migration/owner role fails on those tables, and ``pg_dump --enable-row-security``
"succeeds" while silently writing only the rows visible without an owner
context, i.e. none. This module never passes ``--enable-row-security`` and
refuses to back up unless every row can be read:

- The backup connection must be a dedicated role: BYPASSRLS, not SUPERUSER, no
  CREATEROLE/CREATEDB/REPLICATION, member of nothing but ``pg_read_all_data``,
  owner of nothing, no write privilege on any table and no CREATE privilege.
  It is therefore neither the runtime role (which preflight requires to be
  NOBYPASSRLS) nor the migration owner (which owns the tables).
- ``row_security`` is forced off for the session, so a role that cannot bypass
  a policy gets an error instead of a filtered result.
- Row counts and a content digest per table, the migration ledger and the
  Library media rows are read in one REPEATABLE READ READ ONLY transaction
  whose exported snapshot is handed to ``pg_dump --snapshot``: the dump and the
  fingerprints describe the same instant.
- Every media object under the media root is copied, re-hashed, and checked
  against ``cloud_library_media_assets`` (size and SHA-256 of every READY
  asset). A missing or changed referenced object refuses the backup.
- The backup is written to a ``.partial`` directory, sealed with
  ``MANIFEST.sha256`` and published with a no-replace rename.

Restore writes only into an empty database and a new media directory, then
recomputes every fingerprint and compares it with the manifest. DSNs are read
from environment variables by the CLI and passed to the PostgreSQL client
tools through ``PG*`` environment variables, never as arguments.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from services.cloud.migrations import MigrationRefused, applied_versions
from services.cloud.roles import _role_problems as _runtime_role_problems
from services.cloud.schema import MIGRATIONS_DIR, expected_tables, validate_identifier

FORMAT_VERSION = 1
DUMP_NAME = "database.dump"
MEDIA_DIR = "media"
MANIFEST_JSON = "manifest.json"
MANIFEST_SHA = "MANIFEST.sha256"
ALLOWED_BACKUP_MEMBERSHIPS = frozenset({"pg_read_all_data"})
WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SESSION_SETTINGS = (
    "SET TIME ZONE 'UTC'",
    "SET DateStyle = 'ISO, YMD'",
    "SET IntervalStyle = 'postgres'",
    "SET extra_float_digits = 3",
    "SET bytea_output = 'hex'",
    "SET row_security = off",
)
# conninfo keyword -> libpq environment variable understood by pg_dump/pg_restore
_PG_ENV = {
    "host": "PGHOST",
    "hostaddr": "PGHOSTADDR",
    "port": "PGPORT",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "dbname": "PGDATABASE",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}
_VERSION_LINE = re.compile(r"\(PostgreSQL\) (\d+)(?:\.(\d+))?")


class BackupRefused(RuntimeError):
    """The operation is unsafe or its preconditions are not met; nothing was written."""


class BackupInvalid(RuntimeError):
    """A backup or a restored copy does not match its manifest."""


@dataclass
class BackupResult:
    path: Path
    tables: int
    rows: int
    media_objects: int
    media_bytes: int
    dump_bytes: int
    seconds: float


@dataclass
class RestoreReport:
    database: str
    media_root: Path
    tables: int
    rows: int
    media_objects: int
    ledger: list[str]
    timings: dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------- tools


def _tool(name: str) -> str:
    directory = os.getenv("CLOUD_PG_BIN", "").strip()
    if directory:
        candidate = Path(directory) / name
        if not candidate.is_file():
            raise BackupRefused(f"{name} not found in CLOUD_PG_BIN")
        return str(candidate)
    found = shutil.which(name)
    if not found:
        raise BackupRefused(f"{name} is not installed (set CLOUD_PG_BIN to the PostgreSQL bin directory)")
    return found


def tool_major(name: str) -> int:
    output = subprocess.run(
        [_tool(name), "--version"], capture_output=True, text=True, check=True, timeout=30
    ).stdout
    match = _VERSION_LINE.search(output)
    if not match:
        raise BackupRefused(f"Cannot read the {name} version")
    return int(match.group(1))


def _client_env(dsn: str) -> dict[str, str]:
    """libpq environment for a client tool; the DSN never appears in argv."""
    params = conninfo_to_dict(dsn)
    unknown = sorted(set(params) - set(_PG_ENV))
    if unknown:
        raise BackupRefused("Unsupported connection parameters: " + ", ".join(unknown))
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "HOME", "TMPDIR"}}
    # Nothing inherited from the operator's PG* environment (notably PGOPTIONS,
    # which could turn row_security on and make pg_dump silently filter rows).
    for key, value in params.items():
        if value is not None:
            env[_PG_ENV[key]] = str(value)
    return env


def _run_tool(args: list[str], dsn: str, *, timeout: int) -> None:
    result = subprocess.run(
        args, env=_client_env(dsn), capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        # The tools print object names and server messages, never the password.
        message = (result.stderr or result.stdout).strip().splitlines()
        raise BackupInvalid(f"{Path(args[0]).name} failed: " + " | ".join(message[-5:]))


# ---------------------------------------------------------------------- sessions


def _connect(dsn: str, **kwargs):
    return psycopg.connect(dsn, connect_timeout=10, **kwargs)


def _apply_session_settings(connection) -> None:
    for statement in _SESSION_SETTINGS:
        connection.execute(statement)


def _server_major(connection) -> int:
    return int(connection.execute("SHOW server_version_num").fetchone()[0]) // 10000


def _public_tables(connection) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
        )
    ]


def role_problems_for_backup(connection, role: str) -> list[str]:
    """Why ``role`` must not take a backup (empty list: acceptable)."""
    row = connection.execute(
        "SELECT oid, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication, rolinherit"
        " FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    if row is None:
        return ["backup role does not exist"]
    oid, superuser, bypass, createrole, createdb, replication, inherit = row
    problems: list[str] = []
    if superuser:
        problems.append("backup role must not be SUPERUSER (use a dedicated BYPASSRLS role)")
    if not bypass:
        problems.append("backup role lacks BYPASSRLS: forced row level security would hide rows")
    for flag, name in ((createrole, "CREATEROLE"), (createdb, "CREATEDB"), (replication, "REPLICATION")):
        if flag:
            problems.append(f"backup role has {name}")
    if not inherit:
        problems.append("backup role is NOINHERIT and cannot use pg_read_all_data")
    memberships = {
        r[0]
        for r in connection.execute(
            "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid"
            " WHERE m.member = %s",
            (oid,),
        )
    }
    extra = sorted(memberships - ALLOWED_BACKUP_MEMBERSHIPS)
    if extra:
        problems.append("backup role is a member of other roles: " + ", ".join(extra))
    if connection.execute(
        "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE c.relowner = %s AND n.nspname NOT IN ('pg_catalog', 'information_schema') LIMIT 1",
        (oid,),
    ).fetchone():
        problems.append("backup role owns relations (it must not be the migration owner)")
    if connection.execute("SELECT 1 FROM pg_namespace WHERE nspowner = %s", (oid,)).fetchone():
        problems.append("backup role owns a schema")
    if connection.execute(
        "SELECT datdba = %s FROM pg_database WHERE datname = current_database()", (oid,)
    ).fetchone()[0]:
        problems.append("backup role owns the database")
    if connection.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE')", (role,)).fetchone()[0]:
        problems.append("backup role can CREATE in schema public")
    if connection.execute(
        "SELECT has_database_privilege(%s, current_database(), 'CREATE')", (role,)
    ).fetchone()[0]:
        problems.append("backup role can CREATE schemas in the database")
    for table in _public_tables(connection):
        qualified = f"public.{table}"
        if not connection.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')", (role, qualified)
        ).fetchone()[0]:
            problems.append(f"backup role cannot SELECT {table}")
        writable = [
            privilege
            for privilege in WRITE_PRIVILEGES
            if connection.execute(
                "SELECT has_table_privilege(%s, %s, %s)", (role, qualified, privilege)
            ).fetchone()[0]
        ]
        if writable:
            problems.append(f"backup role can {'/'.join(writable)} {table}")
    return problems


def backup_role_problems(connection) -> list[str]:
    """Checks for the connected session's role (refuses SET ROLE)."""
    current, session = connection.execute("SELECT current_user, session_user").fetchone()
    problems = [] if current == session else ["current_user differs from session_user (SET ROLE in use)"]
    return problems + role_problems_for_backup(connection, current)


def create_backup_role(privileged_dsn: str, role: str) -> bool:
    """Create the dedicated backup role NOLOGIN; returns False if a safe one already exists.

    Creating a BYPASSRLS role needs a superuser (or the provider's equivalent).
    LOGIN and the password are set interactively by the operator afterwards.
    """
    role = validate_identifier(role)
    with _connect(privileged_dsn, autocommit=True) as connection:
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            problems = role_problems_for_backup(connection, role)
            if problems:
                raise BackupRefused("Existing role is unsafe as a backup role: " + "; ".join(problems))
            return False
        ident = sql.Identifier(role)
        with connection.transaction():
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT"
                    " NOREPLICATION BYPASSRLS"
                ).format(ident)
            )
            connection.execute(sql.SQL("GRANT pg_read_all_data TO {}").format(ident))
            database = connection.execute("SELECT current_database()").fetchone()[0]
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), ident)
            )
        problems = role_problems_for_backup(connection, role)
        if problems:
            raise BackupRefused("Created backup role failed its checks: " + "; ".join(problems))
        return True


def role_separation(admin_dsn: str, runtime_role: str, backup_role: str) -> dict:
    """Read-only report on the migration owner, runtime and backup roles."""
    runtime_role = validate_identifier(runtime_role)
    backup_role = validate_identifier(backup_role)
    with _connect(admin_dsn, autocommit=True) as connection:
        owners = sorted({
            row[0]
            for row in connection.execute(
                "SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
            )
        })
        runtime = _runtime_role_problems(connection, runtime_role)
        backup = role_problems_for_backup(connection, backup_role)
        failures = [f"runtime: {p}" for p in runtime] + [f"backup: {p}" for p in backup]
        if runtime_role == backup_role:
            failures.append("runtime and backup roles are the same role")
        for owner in owners:
            if owner in {runtime_role, backup_role}:
                failures.append(f"{owner} owns tables and must be neither runtime nor backup role")
        if len(owners) != 1:
            failures.append("tables are not owned by exactly one migration owner role")
        return {"ok": not failures, "table_owners": owners, "runtime_role": runtime_role,
                "backup_role": backup_role, "failures": failures}


def _require_rls_bypass(connection, purpose: str) -> None:
    superuser, bypass = connection.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if not (superuser or bypass):
        raise BackupRefused(
            f"The {purpose} role cannot bypass row level security, so it cannot read every row"
        )


def table_fingerprints(connection, tables: list[str]) -> dict[str, dict[str, object]]:
    """Row count and an order-independent SHA-256 over the rows' text form, per table.

    Settings that influence the text form (time zone, date/interval style, float
    digits, bytea output) are pinned by ``_apply_session_settings``; ordering uses
    the "C" collation, so the result does not depend on the database locale.
    """
    result: dict[str, dict[str, object]] = {}
    for table in tables:
        count, digest = connection.execute(
            sql.SQL(
                "SELECT count(*), encode(sha256(convert_to(coalesce(string_agg("
                "md5(t::text), '' ORDER BY md5(t::text) COLLATE \"C\"), ''), 'UTF8')), 'hex')"
                " FROM {} AS t"
            ).format(sql.Identifier("public", table))
        ).fetchone()
        result[table] = {"rows": int(count), "sha256": digest}
    return result


def _ledger(connection) -> list[dict[str, str]]:
    return [
        {"version": row[0], "filename": row[1], "sha256": row[2]}
        for row in connection.execute(
            "SELECT version, filename, sha256 FROM cloud_schema_migrations ORDER BY version"
        )
    ]


def _media_rows(connection) -> tuple[list[dict], list[dict]]:
    assets = [
        {"object_key": row[0], "byte_size": int(row[1]), "sha256": row[2]}
        for row in connection.execute(
            "SELECT object_key, byte_size, sha256 FROM cloud_library_media_assets ORDER BY object_key"
        )
    ]
    outbox = [
        {"object_key": row[0], "status": row[1]}
        for row in connection.execute(
            "SELECT object_key, status FROM cloud_media_cleanup_outbox ORDER BY object_key"
        )
    ]
    return assets, outbox


# ------------------------------------------------------------------------- media


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _media_listing(root: Path) -> dict[str, tuple[int, int]]:
    """Relative path -> (size, mtime_ns) for every regular file; refuses links and specials."""
    listing: dict[str, tuple[int, int]] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in directories:
            if (base / name).is_symlink():
                raise BackupRefused("The media root contains a symbolic link")
        for name in files:
            path = base / name
            stat = path.lstat()
            if not path.is_file() or path.is_symlink():
                raise BackupRefused("The media root contains a non-regular file")
            listing[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return listing


def _copy_verified(source: Path, destination: Path) -> tuple[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
            writer.write(block)
        writer.flush()
        os.fsync(writer.fileno())
    copied, copied_size = _sha256_file(destination)
    if copied != digest.hexdigest() or copied_size != size:
        raise BackupInvalid("A media object changed while it was being copied")
    return copied, size


def classify_media(
    files: dict[str, dict[str, object]], assets: list[dict], outbox: list[dict]
) -> dict[str, int]:
    """Check referenced objects and count each class; raises on a missing or changed asset."""
    referenced = {asset["object_key"] for asset in assets}
    for asset in assets:
        entry = files.get(asset["object_key"])
        if entry is None:
            raise BackupInvalid("A READY Library media object is missing from the media root")
        if entry["size"] != asset["byte_size"] or entry["sha256"] != asset["sha256"]:
            raise BackupInvalid("A READY Library media object does not match its metadata")
    queued = {row["object_key"] for row in outbox} - referenced
    return {
        "assets": len(referenced),
        "outbox_retained": len([key for key in files if key in queued]),
        "outbox_already_removed": len([key for key in queued if key not in files]),
        "unreferenced": len([key for key in files if key not in referenced and key not in queued]),
    }


# ---------------------------------------------------------------------- manifest


def _write_manifest_sha(root: Path) -> None:
    lines = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != MANIFEST_SHA):
        digest, _ = _sha256_file(path)
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    (root / MANIFEST_SHA).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _check_manifest_sha(root: Path) -> None:
    sealed = root / MANIFEST_SHA
    if not sealed.is_file():
        raise BackupInvalid("MANIFEST.sha256 is missing")
    expected: dict[str, str] = {}
    for line in sealed.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or not name or name in expected:
            raise BackupInvalid("MANIFEST.sha256 is malformed")
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST_SHA
    }
    if any(path.is_symlink() for path in root.rglob("*")):
        raise BackupInvalid("The backup contains a symbolic link")
    if set(actual) != set(expected):
        raise BackupInvalid("The backup's file set differs from MANIFEST.sha256")
    for name, path in actual.items():
        if _sha256_file(path)[0] != expected[name]:
            raise BackupInvalid("A backup file does not match MANIFEST.sha256")


def _refuse_inside(path: Path, parents: list[Path], what: str) -> None:
    resolved = path.resolve()
    for parent in parents:
        parent = parent.resolve()
        if resolved == parent or parent in resolved.parents:
            raise BackupRefused(f"{what} must be outside {parent.name or parent}")


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# ------------------------------------------------------------------------ backup


def create_backup(
    backup_dsn: str,
    media_root: Path,
    destination: Path,
    *,
    confirm_writes_frozen: bool,
    directory: Path = MIGRATIONS_DIR,
    timeout: int = 3600,
) -> BackupResult:
    """Consistent logical backup of the Cloud database plus its media objects."""
    if not confirm_writes_frozen:
        raise BackupRefused(
            "Stop the application or put it in maintenance first and pass --confirm-writes-frozen;"
            " media and database are only consistent while nothing writes"
        )
    started = time.monotonic()
    media_root = Path(media_root)
    destination = Path(destination)
    if not media_root.is_dir() or media_root.is_symlink():
        raise BackupRefused("The media root must be an existing directory (not a link)")
    _refuse_inside(destination, [REPOSITORY_ROOT, media_root], "The backup destination")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = destination / f"cloud-backup-{stamp}-{uuid.uuid4().hex[:8]}"
    partial = final.with_name(final.name + ".partial")
    if final.exists() or partial.exists():
        raise BackupRefused("The backup directory already exists")

    with _connect(backup_dsn, autocommit=True) as connection:
        problems = backup_role_problems(connection)
        if problems:
            raise BackupRefused("Refusing to back up with this role: " + "; ".join(problems))
        server_major = _server_major(connection)
        dump_major = tool_major("pg_dump")
        if dump_major != server_major:
            raise BackupRefused(
                f"pg_dump {dump_major} does not match server {server_major}; use the same major version"
            )
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _apply_session_settings(connection)
        snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
        try:
            versions = applied_versions(connection, directory)
        except MigrationRefused as exc:
            raise BackupRefused(f"Unexpected migration ledger: {exc}") from exc
        tables = _public_tables(connection)
        if set(tables) != expected_tables(versions):
            raise BackupRefused("Live tables differ from the applied migration versions")
        fingerprints = table_fingerprints(connection, tables)
        ledger = _ledger(connection)
        assets, outbox = _media_rows(connection)
        server_version = connection.execute("SHOW server_version").fetchone()[0]

        partial.mkdir(mode=0o700)
        try:
            before = _media_listing(media_root)
            dump_path = partial / DUMP_NAME
            # --snapshot pins the dump to the fingerprints above. No
            # --enable-row-security, ever: with BYPASSRLS every row is dumped,
            # without it the dump fails instead of silently filtering.
            _run_tool(
                [_tool("pg_dump"), "--format=custom", "--no-password", f"--snapshot={snapshot}",
                 f"--file={dump_path}"],
                backup_dsn,
                timeout=timeout,
            )
            connection.execute("COMMIT")
            files: dict[str, dict[str, object]] = {}
            for relative in sorted(before):
                digest, size = _copy_verified(media_root / relative, partial / MEDIA_DIR / relative)
                files[relative] = {"size": size, "sha256": digest}
            after = _media_listing(media_root)
            if after != before:
                raise BackupInvalid("Media objects changed during the backup; writes were not frozen")
            classes = classify_media(files, assets, outbox)
            manifest = {
                "format": FORMAT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "server_version": server_version,
                "server_major": server_major,
                "pg_dump_major": dump_major,
                "ledger": ledger,
                "tables": fingerprints,
                "media": {"objects": files, "classes": classes},
                "dump": {"file": DUMP_NAME, "sha256": _sha256_file(dump_path)[0],
                         "bytes": dump_path.stat().st_size},
            }
            (partial / MANIFEST_JSON).write_text(
                json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
            )
            _write_manifest_sha(partial)
            _check_manifest_sha(partial)
            _fsync_dir(partial)
            if final.exists():
                raise BackupRefused("The backup directory already exists")
            os.rename(partial, final)
            _fsync_dir(destination)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise
    return BackupResult(
        path=final,
        tables=len(tables),
        rows=sum(int(entry["rows"]) for entry in fingerprints.values()),
        media_objects=len(files),
        media_bytes=sum(int(entry["size"]) for entry in files.values()),
        dump_bytes=int(manifest["dump"]["bytes"]),
        seconds=round(time.monotonic() - started, 3),
    )


# ------------------------------------------------------------------------ verify


def verify_backup(path: Path, *, timeout: int = 600) -> dict:
    """Seal, manifest and dump readability check. Returns the manifest."""
    path = Path(path)
    if not path.is_dir() or path.is_symlink() or path.name.endswith(".partial"):
        raise BackupInvalid("Not a published backup directory")
    _check_manifest_sha(path)
    try:
        manifest = json.loads((path / MANIFEST_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupInvalid("manifest.json is unreadable") from exc
    if manifest.get("format") != FORMAT_VERSION:
        raise BackupInvalid("Unsupported backup format")
    dump = path / DUMP_NAME
    if _sha256_file(dump)[0] != manifest["dump"]["sha256"]:
        raise BackupInvalid("database.dump does not match the manifest")
    objects = manifest["media"]["objects"]
    present = {
        p.relative_to(path / MEDIA_DIR).as_posix()
        for p in (path / MEDIA_DIR).rglob("*")
        if p.is_file()
    } if (path / MEDIA_DIR).is_dir() else set()
    if present != set(objects):
        raise BackupInvalid("Media objects differ from the manifest")
    for relative, entry in objects.items():
        digest, size = _sha256_file(path / MEDIA_DIR / relative)
        if digest != entry["sha256"] or size != entry["size"]:
            raise BackupInvalid("A media object does not match the manifest")
    restore_major = tool_major("pg_restore")
    if restore_major != manifest["server_major"]:
        raise BackupRefused(
            f"pg_restore {restore_major} does not match the backup's server {manifest['server_major']}"
        )
    result = subprocess.run(
        [_tool("pg_restore"), "--list", str(dump)], capture_output=True, text=True, timeout=timeout,
        env={key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ},
    )
    if result.returncode != 0:
        raise BackupInvalid("pg_restore cannot read database.dump")
    listed = {
        match.group(1)
        for match in re.finditer(r"TABLE DATA public (\S+) ", result.stdout)
    }
    if listed != set(manifest["tables"]):
        raise BackupInvalid("The dump's table data differs from the manifest")
    return manifest


# ----------------------------------------------------------------------- restore


def restore_backup(
    path: Path,
    restore_dsn: str,
    target_media_root: Path,
    *,
    live_media_root: Path | None = None,
    directory: Path = MIGRATIONS_DIR,
    timeout: int = 3600,
) -> RestoreReport:
    """Restore into an EMPTY database and a NEW media directory, then prove equality.

    The database is never dropped or overwritten: a target with any table in
    schema public is refused. The restore role must be able to create the
    objects with their original owners and grants (the roles must exist) and
    must bypass row level security, otherwise neither the data load into
    FORCE RLS tables nor the verification can see every row.
    """
    path = Path(path)
    target_media_root = Path(target_media_root)
    timings: dict[str, float] = {}
    started = time.monotonic()
    manifest = verify_backup(path)
    timings["verify_backup_s"] = round(time.monotonic() - started, 3)
    if target_media_root.exists() and (not target_media_root.is_dir() or any(target_media_root.iterdir())):
        raise BackupRefused("The target media root must not exist or must be an empty directory")
    if live_media_root is not None:
        _refuse_inside(target_media_root, [Path(live_media_root)], "The target media root")
        _refuse_inside(Path(live_media_root), [target_media_root], "The live media root")
    _refuse_inside(target_media_root, [REPOSITORY_ROOT], "The target media root")

    with _connect(restore_dsn, autocommit=True) as connection:
        _require_rls_bypass(connection, "restore")
        if _server_major(connection) != manifest["server_major"]:
            raise BackupRefused("The target server's major version differs from the backup")
        if connection.execute(
            "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' LIMIT 1"
        ).fetchone():
            raise BackupRefused("The target database is not empty; restore only into a new database")
        database = connection.execute("SELECT current_database()").fetchone()[0]

    step = time.monotonic()
    _run_tool(
        [_tool("pg_restore"), "--exit-on-error", "--single-transaction", "--no-password",
         f"--dbname={database}", str(path / DUMP_NAME)],
        restore_dsn,
        timeout=timeout,
    )
    timings["database_restore_s"] = round(time.monotonic() - step, 3)

    step = time.monotonic()
    staging = target_media_root.with_name(target_media_root.name + f".restore-{uuid.uuid4().hex[:8]}")
    staging.mkdir(parents=True, mode=0o700)
    try:
        for relative, entry in sorted(manifest["media"]["objects"].items()):
            digest, size = _copy_verified(path / MEDIA_DIR / relative, staging / relative)
            if digest != entry["sha256"] or size != entry["size"]:
                raise BackupInvalid("A restored media object does not match the manifest")
        if target_media_root.exists():
            target_media_root.rmdir()  # empty, checked above
        os.rename(staging, target_media_root)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    timings["media_restore_s"] = round(time.monotonic() - step, 3)

    step = time.monotonic()
    with _connect(restore_dsn) as connection:
        connection.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        _apply_session_settings(connection)
        tables = _public_tables(connection)
        if set(tables) != set(manifest["tables"]):
            raise BackupInvalid("Restored tables differ from the manifest")
        fingerprints = table_fingerprints(connection, tables)
        mismatched = sorted(t for t in tables if fingerprints[t] != manifest["tables"][t])
        if mismatched:
            raise BackupInvalid("Restored rows differ from the backup in: " + ", ".join(mismatched))
        ledger = _ledger(connection)
        if ledger != manifest["ledger"]:
            raise BackupInvalid("The restored migration ledger differs from the backup")
        try:
            versions = applied_versions(connection, directory)
        except MigrationRefused as exc:
            raise BackupInvalid(f"The restored ledger is not valid for this code: {exc}") from exc
        assets, outbox = _media_rows(connection)
        restored_files = {
            relative: {"size": entry["size"], "sha256": entry["sha256"]}
            for relative, entry in manifest["media"]["objects"].items()
        }
        classify_media(restored_files, assets, outbox)
        connection.execute("COMMIT")
    for asset in assets:
        digest, size = _sha256_file(target_media_root / asset["object_key"])
        if digest != asset["sha256"] or size != asset["byte_size"]:
            raise BackupInvalid("A restored READY media object does not match its metadata")
    timings["verification_s"] = round(time.monotonic() - step, 3)
    timings["total_s"] = round(time.monotonic() - started, 3)
    return RestoreReport(
        database=database,
        media_root=target_media_root,
        tables=len(tables),
        rows=sum(int(entry["rows"]) for entry in fingerprints.values()),
        media_objects=len(manifest["media"]["objects"]),
        ledger=versions,
        timings=timings,
    )
