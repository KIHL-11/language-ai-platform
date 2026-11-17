"""Forward-only runner for the Cloud Phase 1 / 1.5 migration set.

Safety properties:
- Only versions approved in code (``schema.approved_migrations``: 0001-0009 plus
  any explicitly allowed Phase 1.5 version) and listed in the pinned manifest
  can be applied, in the approved order without gaps. 0010-0013 are reserved
  for the frozen B-series and always refused; unknown versions are refused.
- Every file is checked against its pinned SHA-256 before anything runs.
- One runner at a time (session advisory lock); a second runner is refused.
- Each version and its ledger row commit in a single transaction, so a failed
  version leaves neither tables nor a ledger entry behind.
- A database that already holds tables but no ledger is refused rather than
  guessed at. There is no automatic rollback: recovery is restore-from-backup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from services.cloud.schema import (
    LEDGER_TABLE,
    MIGRATIONS_DIR,
    Migration,
    SchemaDefinitionError,
    is_reserved_version,
    load_manifest,
)

LOCK_KEY = "language-ai-cloud-schema-migrations"
LOCK_TIMEOUT = "10s"

LEDGER_DDL = f"""
CREATE TABLE {LEDGER_TABLE} (
    version CHAR(4) PRIMARY KEY CHECK (version ~ '^[0-9]{{4}}$'),
    filename VARCHAR(128) NOT NULL,
    sha256 CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by VARCHAR(128) NOT NULL DEFAULT current_user,
    server_version_num INTEGER NOT NULL
)
"""


class MigrationRefused(RuntimeError):
    """The runner refused to change the database. Nothing was applied."""


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    target: str
    applied: list[str]
    pending: list[Migration] = field(default_factory=list)

    @property
    def pending_versions(self) -> list[str]:
        return [migration.version for migration in self.pending]


def _resolve_target(manifest: list[Migration], target: str | None) -> str:
    versions = [migration.version for migration in manifest]
    if target is not None:
        if not (isinstance(target, str) and len(target) == 4 and target.isdigit()):
            raise MigrationRefused("Target must be a four-digit version")
        if is_reserved_version(target):
            raise MigrationRefused(
                f"Version {target} is reserved for the frozen B-series, outside the Phase 1 set, "
                "and is never applied")
        if target not in versions:
            raise MigrationRefused(f"Version {target} is not in the pinned manifest")
        return target
    present = None
    for migration in manifest:
        if not migration.exists():
            break
        present = migration.version
    if present is None:
        raise MigrationRefused("No migration files are present")
    return present


def _ledger_rows(connection) -> list[dict] | None:
    exists = connection.execute(
        "SELECT to_regclass(%s) IS NOT NULL AS present", (f"public.{LEDGER_TABLE}",)
    ).fetchone()
    if not exists[0]:
        return None
    rows = connection.execute(
        f"SELECT version, filename, sha256 FROM public.{LEDGER_TABLE} ORDER BY version"
    ).fetchall()
    return [{"version": r[0], "filename": r[1], "sha256": r[2]} for r in rows]


def _public_tables(connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    }


def _check_ledger(manifest: list[Migration], rows: list[dict]) -> list[str]:
    applied = []
    for index, row in enumerate(rows):
        if index >= len(manifest):
            raise MigrationRefused("Ledger lists versions beyond the pinned manifest")
        expected = manifest[index]
        if row["version"] != expected.version:
            raise MigrationRefused("Ledger versions are not a contiguous prefix of the manifest")
        if row["sha256"] != expected.sha256 or row["filename"] != expected.filename:
            raise MigrationRefused(f"Ledger digest for {expected.version} does not match the manifest")
        applied.append(row["version"])
    return applied


def _build_plan(manifest: list[Migration], applied: list[str], target: str) -> MigrationPlan:
    if applied and applied[-1] > target:
        raise MigrationRefused("Target is below the applied version; the runner is forward-only")
    pending = [m for m in manifest if m.version not in applied and m.version <= target]
    for migration in pending:
        if not migration.exists():
            raise MigrationRefused(f"{migration.filename} is missing")
        try:
            migration.body()
        except SchemaDefinitionError as exc:
            raise MigrationRefused(str(exc)) from exc
    return MigrationPlan(target=target, applied=applied, pending=pending)


def _acquire_lock(connection) -> None:
    locked = connection.execute(
        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (LOCK_KEY,)
    ).fetchone()[0]
    if not locked:
        raise MigrationRefused("Another migration runner holds the lock")


def _release_lock(connection) -> None:
    try:
        connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK_KEY,))
    except psycopg.Error:
        pass  # closing the session releases the lock anyway


def plan(dsn: str, *, target: str | None = None, directory: Path = MIGRATIONS_DIR) -> MigrationPlan:
    """Read-only: report what apply() would do. Never creates the ledger."""
    try:
        manifest = load_manifest(directory)
    except SchemaDefinitionError as exc:
        raise MigrationRefused(str(exc)) from exc
    resolved = _resolve_target(manifest, target)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as connection:
        rows = _ledger_rows(connection)
        if rows is None:
            if _public_tables(connection):
                raise MigrationRefused("Database holds tables but no migration ledger")
            applied: list[str] = []
        else:
            if rows == [] and _public_tables(connection) - {LEDGER_TABLE}:
                raise MigrationRefused("Migration ledger is empty but the database holds tables")
            applied = _check_ledger(manifest, rows)
    return _build_plan(manifest, applied, resolved)


def apply(dsn: str, *, target: str | None = None, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending versions up to ``target`` and return the versions applied."""
    try:
        manifest = load_manifest(directory)
    except SchemaDefinitionError as exc:
        raise MigrationRefused(str(exc)) from exc
    resolved = _resolve_target(manifest, target)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as connection:
        _acquire_lock(connection)
        try:
            rows = _ledger_rows(connection)
            if rows is None and _public_tables(connection):
                raise MigrationRefused("Database holds tables but no migration ledger")
            if rows == [] and _public_tables(connection) - {LEDGER_TABLE}:
                raise MigrationRefused("Migration ledger is empty but the database holds tables")
            applied = _check_ledger(manifest, rows or [])
            # Validate every pending file before the first write, including the ledger.
            migration_plan = _build_plan(manifest, applied, resolved)
            if rows is None:
                with connection.transaction():
                    connection.execute(LEDGER_DDL)
            server_version = int(connection.execute("SHOW server_version_num").fetchone()[0])
            done: list[str] = []
            for migration in migration_plan.pending:
                body = migration.body()
                with connection.transaction():
                    connection.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                    connection.execute(body, prepare=False)
                    connection.execute(
                        f"INSERT INTO {LEDGER_TABLE} (version, filename, sha256, server_version_num)"
                        " VALUES (%s, %s, %s, %s)",
                        (migration.version, migration.filename, migration.sha256, server_version),
                    )
                done.append(migration.version)
            return done
        finally:
            _release_lock(connection)


def applied_versions(connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Versions recorded in the ledger, validated against the pinned manifest.

    Fails closed: a missing or empty ledger is refused, never read as "nothing
    to check".
    """
    rows = _ledger_rows(connection)
    if rows is None:
        raise MigrationRefused("No migration ledger")
    if not rows:
        raise MigrationRefused("Migration ledger is empty")
    try:
        manifest = load_manifest(directory)
    except SchemaDefinitionError as exc:
        raise MigrationRefused(str(exc)) from exc
    return _check_ledger(manifest, rows)
