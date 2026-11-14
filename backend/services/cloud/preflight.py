"""Read-only preflight for the Cloud runtime database connection.

Run with the *runtime* DSN before any Cloud repository is wired. It refuses a
connection whose role could defeat row level security (SUPERUSER, BYPASSRLS,
table or schema ownership, role membership/inheritance, CREATE rights), a schema
that differs from the applied Phase 1 versions (including any 0010+ object),
and privileges that differ from the least-privilege grant table in either
direction. Messages never contain the DSN or credentials.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from services.cloud.migrations import MigrationRefused, applied_versions
from services.cloud.schema import (
    COLUMNS_BY_VERSION,
    CONSTRAINTS_BY_VERSION,
    LEDGER_TABLE,
    MIGRATIONS_DIR,
    RUNTIME_GRANTS,
    TABLE_PRIVILEGES,
    expected_tables,
    forced_rls_tables,
)

COLUMN_CAPABLE = frozenset({"SELECT", "INSERT", "UPDATE", "REFERENCES"})


class CloudPreflightFailed(RuntimeError):
    def __init__(self, failures: list[str]):
        self.failures = list(failures)
        super().__init__("Cloud database preflight failed: " + "; ".join(self.failures))


@dataclass(frozen=True, slots=True)
class PreflightReport:
    role: str
    versions: list[str]
    server_version_num: int
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _role_checks(connection) -> tuple[str, list[str]]:
    failures: list[str] = []
    row = connection.execute(
        "SELECT current_user, session_user, rolsuper, rolbypassrls, rolcreaterole,"
        " rolcreatedb, rolreplication FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    role, session_role, superuser, bypass, createrole, createdb, replication = row
    if role != session_role:
        failures.append("current_user differs from session_user (SET ROLE in use)")
    for flag, name in (
        (superuser, "SUPERUSER"),
        (bypass, "BYPASSRLS"),
        (createrole, "CREATEROLE"),
        (createdb, "CREATEDB"),
        (replication, "REPLICATION"),
    ):
        if flag:
            failures.append(f"runtime role has {name}")
    memberships = connection.execute(
        "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid"
        " WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = current_user)"
        " ORDER BY 1"
    ).fetchall()
    if memberships:
        failures.append(
            "runtime role is a member of other roles: " + ", ".join(r[0] for r in memberships)
        )
    owned = connection.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)"
        " AND n.nspname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1"
    ).fetchall()
    if owned:
        failures.append("runtime role owns relations: " + ", ".join(r[0] for r in owned[:10]))
    owns_schema = connection.execute(
        "SELECT count(*) FROM pg_namespace WHERE nspowner ="
        " (SELECT oid FROM pg_roles WHERE rolname = current_user)"
    ).fetchone()[0]
    if owns_schema:
        failures.append("runtime role owns a schema")
    owns_db = connection.execute(
        "SELECT datdba = (SELECT oid FROM pg_roles WHERE rolname = current_user)"
        " FROM pg_database WHERE datname = current_database()"
    ).fetchone()[0]
    if owns_db:
        failures.append("runtime role owns the database")
    if connection.execute(
        "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
    ).fetchone()[0]:
        failures.append("runtime role can CREATE in schema public")
    if connection.execute(
        "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
    ).fetchone()[0]:
        failures.append("runtime role can CREATE schemas in the database")
    return role, failures


def _schema_checks(connection, versions: list[str]) -> list[str]:
    failures: list[str] = []
    live = {
        row[0]
        for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    }
    expected = expected_tables(versions)
    missing, extra = sorted(expected - live), sorted(live - expected)
    if missing:
        failures.append("missing tables: " + ", ".join(missing))
    if extra:
        failures.append("unexpected tables: " + ", ".join(extra))
    other = connection.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm', 'f', 'p', 'S') ORDER BY 1"
    ).fetchall()
    if other:
        failures.append("unexpected views/sequences/foreign tables: " + ", ".join(r[0] for r in other))
    schemas = connection.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('public', 'information_schema')"
        " AND nspname NOT LIKE 'pg\\_%' ORDER BY 1"
    ).fetchall()
    if schemas:
        failures.append("unexpected schemas: " + ", ".join(r[0] for r in schemas))
    for table in sorted(forced_rls_tables(versions) & live):
        enabled, forced = connection.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE oid = to_regclass(%s)",
            (f"public.{table}",),
        ).fetchone()
        if not (enabled and forced):
            failures.append(f"row level security is not enabled and forced on {table}")
    for version in versions:
        for table, column in COLUMNS_BY_VERSION.get(version, ()):
            found = connection.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'"
                " AND table_name = %s AND column_name = %s",
                (table, column),
            ).fetchone()
            if found is None:
                failures.append(f"missing column {table}.{column} (version {version})")
        for table, constraint in CONSTRAINTS_BY_VERSION.get(version, ()):
            found = connection.execute(
                "SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass(%s) AND conname = %s",
                (f"public.{table}", constraint),
            ).fetchone()
            if found is None:
                failures.append(f"missing constraint {constraint} (version {version})")
    return failures


def _privilege_checks(connection, versions: list[str]) -> list[str]:
    failures: list[str] = []
    for table in sorted(expected_tables(versions)):
        if connection.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()[0] is None:
            continue  # reported by the schema check
        wanted = RUNTIME_GRANTS.get(table, frozenset())
        for privilege in TABLE_PRIVILEGES:
            function = "has_any_column_privilege" if privilege in COLUMN_CAPABLE else "has_table_privilege"
            held = connection.execute(
                f"SELECT {function}(current_user, %s, %s)", (f"public.{table}", privilege)
            ).fetchone()[0]
            if held and privilege not in wanted:
                failures.append(f"excess privilege {privilege} on {table}")
            elif not held and privilege in wanted:
                failures.append(f"missing privilege {privilege} on {table}")
    return failures


def run_preflight(
    dsn: str,
    *,
    minimum_version: str = "0006",
    directory: Path = MIGRATIONS_DIR,
) -> PreflightReport:
    failures: list[str] = []
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as connection:
            role, role_failures = _role_checks(connection)
            failures.extend(role_failures)
            server_version = int(connection.execute("SHOW server_version_num").fetchone()[0])
            try:
                versions = applied_versions(connection, directory)
            except MigrationRefused as exc:
                failures.append(f"migration ledger: {exc}")
                versions = []
            except psycopg.errors.InsufficientPrivilege:
                failures.append(f"runtime role cannot read {LEDGER_TABLE}")
                versions = []
            if versions and versions[-1] < minimum_version:
                failures.append(f"schema is at {versions[-1]}, at least {minimum_version} is required")
            if versions:
                failures.extend(_schema_checks(connection, versions))
                failures.extend(_privilege_checks(connection, versions))
    except psycopg.OperationalError:
        return PreflightReport(role="", versions=[], server_version_num=0,
                               failures=["cannot connect to the runtime database"])
    return PreflightReport(role=role, versions=versions, server_version_num=server_version,
                           failures=failures)


def require_preflight(dsn: str, **kwargs) -> PreflightReport:
    report = run_preflight(dsn, **kwargs)
    if not report.ok:
        raise CloudPreflightFailed(report.failures)
    return report
