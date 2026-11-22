"""Create and grant the least-privilege Cloud runtime role (admin connection).

The role is created NOLOGIN. Giving it LOGIN and a password is a separate,
interactive step for the operator (psql ``\\password``), so no credential ever
passes through this code, its arguments or its logs.
"""
from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql

from services.cloud.migrations import MigrationRefused, applied_versions
from services.cloud.schema import (
    MIGRATIONS_DIR,
    RUNTIME_GRANTS,
    expected_tables,
    validate_identifier,
)

SAFE_ATTRIBUTES = sql.SQL(
    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
)


class RoleRefused(RuntimeError):
    """The role is unsafe or the database is not in a grantable state."""


def _role_problems(connection, role: str) -> list[str]:
    row = connection.execute(
        "SELECT oid, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication"
        " FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    if row is None:
        return ["role does not exist"]
    oid, *flags = row
    problems = [
        name
        for flag, name in zip(flags, ("SUPERUSER", "BYPASSRLS", "CREATEROLE", "CREATEDB", "REPLICATION"))
        if flag
    ]
    if connection.execute("SELECT 1 FROM pg_auth_members WHERE member = %s", (oid,)).fetchone():
        problems.append("member of other roles")
    if connection.execute(
        "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE c.relowner = %s AND n.nspname NOT IN ('pg_catalog','information_schema')",
        (oid,),
    ).fetchone():
        problems.append("owns relations")
    if connection.execute("SELECT 1 FROM pg_namespace WHERE nspowner = %s", (oid,)).fetchone():
        problems.append("owns a schema")
    if connection.execute(
        "SELECT 1 FROM pg_database WHERE datname = current_database() AND datdba = %s", (oid,)
    ).fetchone():
        problems.append("owns the database")
    return problems


def create_runtime_role(admin_dsn: str, role: str) -> bool:
    """Create the role NOLOGIN with safe attributes. Returns False if it already existed.

    An existing role is accepted only if it is already safe; it is never altered.
    """
    role = validate_identifier(role)
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as connection:
        exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        if exists:
            problems = _role_problems(connection, role)
            if problems:
                raise RoleRefused("Existing role is unsafe: " + ", ".join(problems))
            return False
        connection.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN {}").format(sql.Identifier(role), SAFE_ATTRIBUTES)
        )
        return True


def grant_runtime(admin_dsn: str, role: str, *, directory: Path = MIGRATIONS_DIR) -> dict[str, list[str]]:
    """Reset the role's privileges in this database to exactly RUNTIME_GRANTS."""
    role = validate_identifier(role)
    ident = sql.Identifier(role)
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as connection:
        problems = _role_problems(connection, role)
        if problems:
            raise RoleRefused("Refusing to grant to an unsafe role: " + ", ".join(problems))
        try:
            versions = applied_versions(connection, directory)
        except MigrationRefused as exc:
            raise RoleRefused(str(exc)) from exc
        tables = expected_tables(versions)
        live = {
            row[0]
            for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        }
        if live != tables:
            raise RoleRefused("Live tables differ from the applied Phase 1 versions")
        granted: dict[str, list[str]] = {}
        with connection.transaction():
            connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            connection.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(ident))
            connection.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(ident))
            connection.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ident))
            database = connection.execute("SELECT current_database()").fetchone()[0]
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), ident)
            )
            for table in sorted(tables):
                privileges = sorted(RUNTIME_GRANTS[table])
                connection.execute(
                    sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                        sql.SQL(", ").join(sql.SQL(p) for p in privileges),
                        sql.Identifier("public", table),
                        ident,
                    )
                )
                granted[table] = privileges
        return granted
