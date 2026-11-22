"""Read-only preflight for the Cloud runtime database connection.

Run with the *runtime* DSN before any Cloud repository is wired. It refuses a
connection whose role could defeat row level security (SUPERUSER, BYPASSRLS,
table or schema ownership, role membership/inheritance, CREATE rights), a schema
that differs from the applied Phase 1 versions (including any 0010+ object),
and privileges that differ from the least-privilege grant table in either
direction. Messages never contain the DSN or credentials.
"""
from __future__ import annotations

import re
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
    expected_policies,
    expected_tables,
    forced_rls_tables,
)

COLUMN_CAPABLE = frozenset({"SELECT", "INSERT", "UPDATE", "REFERENCES"})

# Every check runs with this search_path: pg_catalog first, pg_temp last, and
# nothing user-writable, so neither deparsed text nor any function the checks
# call can be shadowed by objects in public (e.g. a public.current_setting).
TRUSTED_SEARCH_PATH = "SET search_path TO pg_catalog, pg_temp"

_TREE_TOKEN = re.compile(
    r"\{([A-Z_]+)|:(opno|opfuncid|opresulttype|funcid|funcresulttype|varno|varattno|varlevelsup"
    r"|resulttype|consttype|constisnull) (\S+)")


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
    if not connection.execute(
        "SELECT has_schema_privilege(current_user, 'public', 'USAGE')"
    ).fetchone()[0]:
        failures.append("missing USAGE on schema public")
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
    shadowing = connection.execute(
        "SELECT 'function ' || p.oid::pg_catalog.regprocedure::pg_catalog.text FROM pg_catalog.pg_proc p"
        " WHERE p.pronamespace = 'public'::pg_catalog.regnamespace"
        " UNION ALL SELECT 'operator ' || o.oid::pg_catalog.regoperator::pg_catalog.text"
        " FROM pg_catalog.pg_operator o WHERE o.oprnamespace = 'public'::pg_catalog.regnamespace ORDER BY 1"
    ).fetchall()
    if shadowing:
        failures.append("unexpected functions/operators in schema public: " + ", ".join(r[0] for r in shadowing))
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


def _tree_signature(tree: str | None) -> list[tuple[str, str]]:
    """Node types and identity fields of a stored expression, in tree order."""
    if tree is None:
        return []
    return [("node", node) if node else (key, value) for node, key, value in _TREE_TOKEN.findall(tree)]


def _expected_owner_signature(connection, relation_oid: int) -> list[list[tuple[str, str]]]:
    """Accepted signatures of ``owner_user_id::text = pg_catalog.current_setting(text, true)``.

    Identities come from explicitly schema-qualified catalog lookups, so a
    same-named function or operator in another schema can never match.
    """
    row = connection.execute(
        "SELECT 'pg_catalog.=(text,text)'::pg_catalog.regoperator::pg_catalog.oid,"
        " 'pg_catalog.texteq(text,text)'::pg_catalog.regprocedure::pg_catalog.oid,"
        " 'pg_catalog.current_setting(text,boolean)'::pg_catalog.regprocedure::pg_catalog.oid,"
        " (SELECT a.attnum FROM pg_catalog.pg_attribute a"
        "   WHERE a.attrelid = %s AND a.attname = 'owner_user_id' AND NOT a.attisdropped)",
        (relation_oid,),
    ).fetchone()
    eq_operator, texteq, current_setting, owner_attnum = (str(value) for value in row)
    signatures = []
    for opfuncid in (texteq, "0"):  # stored trees may leave opfuncid unresolved
        signatures.append([
            ("node", "OPEXPR"), ("opno", eq_operator), ("opfuncid", opfuncid), ("opresulttype", "16"),
            ("node", "RELABELTYPE"),
            ("node", "VAR"), ("varno", "1"), ("varattno", owner_attnum), ("varlevelsup", "0"),
            ("resulttype", "25"),
            ("node", "FUNCEXPR"), ("funcid", current_setting), ("funcresulttype", "25"),
            ("node", "CONST"), ("consttype", "25"), ("constisnull", "false"),
            ("node", "CONST"), ("consttype", "16"), ("constisnull", "false"),
        ])
    return signatures


def _policy_checks(connection, versions: list[str]) -> list[str]:
    """Exact RLS policy set, verified on catalog identity and on deparsed text.

    For every expected policy: name, PERMISSIVE, roles, command, and both the
    USING and WITH CHECK expressions. The expressions are checked twice: the
    stored node tree must bind exactly pg_catalog.= (text,text), the table's
    owner_user_id column and pg_catalog.current_setting(text,boolean) with the
    expected constants, and the text deparsed under the trusted search_path
    must equal the pinned definition.
    """
    failures: list[str] = []
    expected = expected_policies(versions)
    live = {}
    for row in connection.execute(
        "SELECT c.relname, p.polname, p.polrelid::pg_catalog.oid,"
        " CASE WHEN p.polpermissive THEN 'PERMISSIVE' ELSE 'RESTRICTIVE' END,"
        " CASE WHEN p.polroles = '{0}'::pg_catalog.oid[] THEN ARRAY['public']"
        "      ELSE ARRAY(SELECT r.rolname::pg_catalog.text FROM pg_catalog.pg_roles r"
        "                 WHERE r.oid = ANY (p.polroles) ORDER BY 1) END,"
        " CASE p.polcmd WHEN '*' THEN 'ALL' WHEN 'r' THEN 'SELECT' WHEN 'a' THEN 'INSERT'"
        "      WHEN 'w' THEN 'UPDATE' WHEN 'd' THEN 'DELETE' ELSE p.polcmd::pg_catalog.text END,"
        " pg_catalog.pg_get_expr(p.polqual, p.polrelid), pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid),"
        " p.polqual::pg_catalog.text, p.polwithcheck::pg_catalog.text"
        " FROM pg_catalog.pg_policy p JOIN pg_catalog.pg_class c ON c.oid = p.polrelid"
        " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
    ):
        live[(row[0], row[1])] = row[2:]
    for key in sorted(set(live) - set(expected)):
        failures.append(f"unexpected policy {key[1]} on {key[0]}")
    for key, definition in sorted(expected.items()):
        if key not in live:
            failures.append(f"missing policy {key[1]} on {key[0]}")
            continue
        relation_oid, permissive, roles, command, using, with_check, using_tree, check_tree = live[key]
        for field_name, actual, wanted in (
            ("permissive", permissive, definition.permissive),
            ("roles", tuple(roles or ()), definition.roles),
            ("command", command, definition.command),
            ("USING", using, definition.using),
            ("WITH CHECK", with_check, definition.with_check),
        ):
            if actual != wanted:
                failures.append(f"policy {key[1]} on {key[0]} has {field_name} {actual!r}, expected {wanted!r}")
        accepted = _expected_owner_signature(connection, relation_oid)
        for field_name, tree in (("USING", using_tree), ("WITH CHECK", check_tree)):
            if _tree_signature(tree) not in accepted:
                failures.append(
                    f"policy {key[1]} on {key[0]} {field_name} does not bind pg_catalog.= (text,text),"
                    f" owner_user_id and pg_catalog.current_setting(text,boolean)")
    return failures


def _privilege_checks(connection, versions: list[str]) -> list[str]:
    """Required and forbidden privileges are checked separately.

    Required: each privilege in RUNTIME_GRANTS must be held at TABLE level
    (has_table_privilege); a grant on some columns does not satisfy it.
    Forbidden: any other privilege, at table level or on any single column,
    held directly or through PUBLIC, is excess.
    """
    failures: list[str] = []
    for table in sorted(expected_tables(versions)):
        if connection.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()[0] is None:
            continue  # reported by the schema check
        relation = f"public.{table}"
        wanted = RUNTIME_GRANTS.get(table, frozenset())
        for privilege in TABLE_PRIVILEGES:
            table_level = connection.execute(
                "SELECT has_table_privilege(current_user, %s, %s)", (relation, privilege)
            ).fetchone()[0]
            any_level = table_level
            if privilege in COLUMN_CAPABLE:
                any_level = connection.execute(
                    "SELECT has_any_column_privilege(current_user, %s, %s)", (relation, privilege)
                ).fetchone()[0]
            if privilege in wanted:
                if not table_level:
                    detail = " (only column-level grants present)" if any_level else ""
                    failures.append(f"missing table-level privilege {privilege} on {table}{detail}")
            elif any_level:
                scope = "table-level" if table_level else "column-level"
                failures.append(f"excess {scope} privilege {privilege} on {table}")
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
            connection.execute(TRUSTED_SEARCH_PATH)
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
                failures.extend(_policy_checks(connection, versions))
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
