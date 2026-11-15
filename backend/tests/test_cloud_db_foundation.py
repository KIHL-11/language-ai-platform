"""Cloud Phase 1 database foundation: migration runner, runtime role, preflight.

Every test runs in its own throwaway database and with throwaway roles, both
dropped afterwards. Only synthetic data is used. Requires TEST_POSTGRES_DSN
pointing at a disposable PostgreSQL 16 server whose user may create databases
and roles (the CI service container, or a local scratch cluster).
"""
from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from services.cloud import migrations, preflight, roles, schema
from services.cloud.schema import RUNTIME_GRANTS, load_manifest

SYNTHETIC_PASSWORD = "synthetic-test-only"
NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
OWNER_A = "cloud-p1-owner-a"
OWNER_B = "cloud-p1-owner-b"
PROVENANCE = '[{"source": "synthetic"}]'


def _admin_dsn() -> str:
    value = os.getenv("TEST_POSTGRES_DSN")
    if not value:
        if os.getenv("CLOUD_P1_REQUIRE_POSTGRES") == "1":
            pytest.fail("CLOUD_P1_REQUIRE_POSTGRES=1 but TEST_POSTGRES_DSN is not set; refusing to skip")
        pytest.skip("TEST_POSTGRES_DSN is required for the Cloud database foundation tests")
    return value


def _present_versions() -> list[str]:
    versions = []
    for migration in load_manifest():
        if not migration.exists():
            break
        versions.append(migration.version)
    return versions


@pytest.fixture
def cloud_db():
    """A fresh empty database plus helpers; dropped with its roles afterwards."""
    base = _admin_dsn()
    name = f"lap_p1_{uuid.uuid4().hex[:12]}"
    created_roles: list[str] = []
    with psycopg.connect(base, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    admin = make_conninfo(base, dbname=name)

    class Database:
        admin_dsn = admin
        dbname = name

        def role(self, prefix: str = "rt") -> str:
            role_name = f"lap_p1_{prefix}_{uuid.uuid4().hex[:10]}"
            created_roles.append(role_name)
            return role_name

        def runtime(self, *, versions_target: str | None = None, login: bool = True) -> str:
            migrations.apply(admin, target=versions_target)
            role_name = self.role()
            roles.create_runtime_role(admin, role_name)
            roles.grant_runtime(admin, role_name)
            if login:
                self.execute(
                    sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role_name), sql.Literal(SYNTHETIC_PASSWORD)
                    )
                )
            self.runtime_role = role_name
            return make_conninfo(admin, user=role_name, password=SYNTHETIC_PASSWORD)

        def execute(self, statement, params=None) -> None:
            with psycopg.connect(admin, autocommit=True) as connection:
                connection.execute(statement, params)

        def owner_role_member(self, runtime_role: str) -> None:
            """Make the runtime role a member of a role that owns a table."""
            owner = self.role("own")
            self.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(owner)))
            self.execute(sql.SQL("ALTER TABLE cloud_users OWNER TO {}").format(sql.Identifier(owner)))
            self.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(owner), sql.Identifier(runtime_role)))

        def scalar(self, statement, params=None):
            with psycopg.connect(admin, autocommit=True) as connection:
                return connection.execute(statement, params).fetchone()[0]

        def tables(self) -> set[str]:
            with psycopg.connect(admin, autocommit=True) as connection:
                return {r[0] for r in connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'")}

    try:
        yield Database()
    finally:
        with psycopg.connect(base, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
            for role_name in reversed(created_roles):
                connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name)))


def _copy_migrations(tmp_path: Path) -> Path:
    target = tmp_path / "migrations"
    shutil.copytree(schema.MIGRATIONS_DIR, target)
    return target


# ---------------------------------------------------------------- manifest

def test_manifest_pins_exactly_0001_through_0009_and_present_files_match():
    manifest = load_manifest()
    assert [m.version for m in manifest] == [f"{i:04d}" for i in range(1, 10)]
    present = _present_versions()
    assert present[:6] == ["0001", "0002", "0003", "0004", "0005", "0006"]
    for migration in manifest:
        if migration.exists():
            migration.body()  # digest and framing verified


def test_manifest_with_0010_is_refused(tmp_path):
    directory = _copy_migrations(tmp_path)
    manifest = directory / schema.MANIFEST_NAME
    manifest.write_text(manifest.read_text() + "0" * 64 + "  0010_cleanup.up.sql\n")
    with pytest.raises(schema.SchemaDefinitionError, match="outside the Phase 1 set"):
        load_manifest(directory)


def test_nested_transaction_control_is_refused(tmp_path):
    directory = tmp_path / "m"
    directory.mkdir()
    body = b"BEGIN;\nCREATE TABLE cloud_users(user_id text);\nCOMMIT;\nDROP TABLE x;\nCOMMIT;\n"
    import hashlib
    (directory / "0001_x.up.sql").write_bytes(body)
    (directory / schema.MANIFEST_NAME).write_text(f"{hashlib.sha256(body).hexdigest()}  0001_x.up.sql\n")
    with pytest.raises(schema.SchemaDefinitionError, match="nested transaction control"):
        load_manifest(directory)[0].body()


# ---------------------------------------------------------------- runner

def test_plan_on_empty_database_is_read_only(cloud_db):
    result = migrations.plan(cloud_db.admin_dsn)
    assert result.applied == []
    assert result.pending_versions == _present_versions()
    assert cloud_db.tables() == set()  # plan never creates the ledger


def test_apply_records_ledger_creates_expected_tables_and_is_idempotent(cloud_db):
    done = migrations.apply(cloud_db.admin_dsn)
    present = _present_versions()
    assert done == present
    assert cloud_db.tables() == schema.expected_tables(present)
    with psycopg.connect(cloud_db.admin_dsn) as connection:
        rows = connection.execute(
            "SELECT version, sha256 FROM cloud_schema_migrations ORDER BY version").fetchall()
    assert rows == [(m.version, m.sha256) for m in load_manifest() if m.version in present]
    assert migrations.apply(cloud_db.admin_dsn) == []


@pytest.mark.parametrize("target", ["0010", "0011", "0012", "0013"])
def test_versions_0010_and_later_are_never_applied(cloud_db, target):
    with pytest.raises(migrations.MigrationRefused, match="outside the Phase 1 set"):
        migrations.apply(cloud_db.admin_dsn, target=target)
    assert cloud_db.tables() == set()


def test_missing_pinned_file_is_refused_before_any_change(cloud_db, tmp_path):
    directory = _copy_migrations(tmp_path)
    for path in directory.glob("0004_*.up.sql"):
        path.unlink()
    with pytest.raises(migrations.MigrationRefused, match="0004_owner_scoped_speaking.up.sql is missing"):
        migrations.apply(cloud_db.admin_dsn, target="0005", directory=directory)
    assert cloud_db.tables() == set()


def test_tampered_file_is_refused_before_any_change(cloud_db, tmp_path):
    directory = _copy_migrations(tmp_path)
    path = next(directory.glob("0003_*.up.sql"))
    path.write_bytes(path.read_bytes().replace(b"COMMIT;", b"-- edited\nCOMMIT;"))
    with pytest.raises(migrations.MigrationRefused, match="does not match the pinned digest"):
        migrations.apply(cloud_db.admin_dsn, directory=directory)
    assert cloud_db.tables() == set()


def test_failing_version_rolls_back_with_its_ledger_row(cloud_db, tmp_path):
    import hashlib
    directory = tmp_path / "m"
    directory.mkdir()
    first = (schema.MIGRATIONS_DIR / "0001_owner_scoped_library.up.sql").read_bytes()
    broken = b"BEGIN;\nCREATE TABLE cloud_study_items (x INTEGER);\nSELECT 1 / 0;\nCOMMIT;\n"
    (directory / "0001_owner_scoped_library.up.sql").write_bytes(first)
    (directory / "0002_broken.up.sql").write_bytes(broken)
    (directory / schema.MANIFEST_NAME).write_text(
        f"{hashlib.sha256(first).hexdigest()}  0001_owner_scoped_library.up.sql\n"
        f"{hashlib.sha256(broken).hexdigest()}  0002_broken.up.sql\n"
    )
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrations.apply(cloud_db.admin_dsn, directory=directory)
    assert "cloud_study_items" not in cloud_db.tables()
    assert cloud_db.scalar("SELECT array_agg(version ORDER BY version) FROM cloud_schema_migrations") == ["0001"]


def test_unmanaged_database_is_refused(cloud_db):
    cloud_db.execute("CREATE TABLE cloud_users (user_id TEXT)")
    with pytest.raises(migrations.MigrationRefused, match="no migration ledger"):
        migrations.apply(cloud_db.admin_dsn)
    with pytest.raises(migrations.MigrationRefused, match="no migration ledger"):
        migrations.plan(cloud_db.admin_dsn)


def test_second_runner_is_refused_while_the_lock_is_held(cloud_db):
    with psycopg.connect(cloud_db.admin_dsn, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (migrations.LOCK_KEY,))
        with pytest.raises(migrations.MigrationRefused, match="Another migration runner"):
            migrations.apply(cloud_db.admin_dsn)
    assert cloud_db.tables() == set()


def test_runner_is_forward_only(cloud_db):
    migrations.apply(cloud_db.admin_dsn, target="0004")
    with pytest.raises(migrations.MigrationRefused, match="forward-only"):
        migrations.apply(cloud_db.admin_dsn, target="0002")


def test_runner_refuses_a_tampered_ledger(cloud_db):
    migrations.apply(cloud_db.admin_dsn, target="0002")
    cloud_db.execute("UPDATE cloud_schema_migrations SET sha256 = repeat('0', 64) WHERE version = '0001'")
    with pytest.raises(migrations.MigrationRefused, match="Ledger digest for 0001"):
        migrations.apply(cloud_db.admin_dsn)


# ---------------------------------------------------------------- runtime role

def test_runtime_role_is_created_nologin_with_safe_attributes(cloud_db):
    role = cloud_db.role()
    assert roles.create_runtime_role(cloud_db.admin_dsn, role) is True
    assert roles.create_runtime_role(cloud_db.admin_dsn, role) is False
    with psycopg.connect(cloud_db.admin_dsn) as connection:
        row = connection.execute(
            "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit,"
            " rolreplication FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    assert row == (False, False, False, False, False, False, False)


@pytest.mark.parametrize("attribute", ["CREATEDB", "BYPASSRLS", "CREATEROLE"])
def test_existing_unsafe_role_is_refused_and_never_altered(cloud_db, attribute):
    role = cloud_db.role()
    cloud_db.execute(sql.SQL("CREATE ROLE {} NOLOGIN " + attribute).format(sql.Identifier(role)))
    with pytest.raises(roles.RoleRefused, match=attribute):
        roles.create_runtime_role(cloud_db.admin_dsn, role)
    migrations.apply(cloud_db.admin_dsn)
    with pytest.raises(roles.RoleRefused, match=attribute):
        roles.grant_runtime(cloud_db.admin_dsn, role)


@pytest.mark.parametrize("name", ["postgres", "pg_monitor", "Bad-Name", "x; DROP ROLE y"])
def test_role_names_are_validated(cloud_db, name):
    with pytest.raises(ValueError):
        roles.create_runtime_role(cloud_db.admin_dsn, name)


def test_grant_requires_a_managed_schema(cloud_db):
    role = cloud_db.role()
    roles.create_runtime_role(cloud_db.admin_dsn, role)
    with pytest.raises(roles.RoleRefused, match="No migration ledger"):
        roles.grant_runtime(cloud_db.admin_dsn, role)


def test_grant_matches_the_least_privilege_table_exactly(cloud_db):
    dsn = cloud_db.runtime()
    present = _present_versions()
    granted = roles.grant_runtime(cloud_db.admin_dsn, cloud_db.runtime_role)
    assert set(granted) == schema.expected_tables(present)
    for table, privileges in granted.items():
        assert set(privileges) == RUNTIME_GRANTS[table]
    report = preflight.run_preflight(dsn)
    assert report.ok, report.failures
    assert report.versions == present
    assert report.role == cloud_db.runtime_role


# ---------------------------------------------------------------- preflight negatives

def _ident(name):
    return sql.Identifier(name)


NEGATIVE_CASES = {
    "bypassrls": (lambda db, r: db.execute(sql.SQL("ALTER ROLE {} BYPASSRLS").format(_ident(r))),
                  "runtime role has BYPASSRLS"),
    "createdb": (lambda db, r: db.execute(sql.SQL("ALTER ROLE {} CREATEDB").format(_ident(r))),
                 "runtime role has CREATEDB"),
    "table_owner": (lambda db, r: db.execute(sql.SQL("ALTER TABLE cloud_study_items OWNER TO {}").format(_ident(r))),
                    "runtime role owns relations: cloud_study_items"),
    "schema_owner": (lambda db, r: db.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(_ident(r))),
                     "runtime role owns a schema"),
    "member_of_owner_role": (lambda db, r: db.owner_role_member(r),
                             "runtime role is a member of other roles"),
    "predefined_role": (lambda db, r: db.execute(sql.SQL("GRANT pg_read_all_data TO {}").format(_ident(r))),
                        "runtime role is a member of other roles: pg_read_all_data"),
    "excess_truncate": (lambda db, r: db.execute(sql.SQL("GRANT TRUNCATE ON cloud_users TO {}").format(_ident(r))),
                        "excess table-level privilege TRUNCATE on cloud_users"),
    "excess_column_update": (
        lambda db, r: db.execute(sql.SQL("GRANT UPDATE (created_at) ON cloud_users TO {}").format(_ident(r))),
        "excess column-level privilege UPDATE on cloud_users"),
    "excess_public_grant": (lambda db, r: db.execute("GRANT DELETE ON cloud_users TO PUBLIC"),
                            "excess table-level privilege DELETE on cloud_users"),
    "missing_insert": (lambda db, r: db.execute(sql.SQL("REVOKE INSERT ON cloud_users FROM {}").format(_ident(r))),
                       "missing table-level privilege INSERT on cloud_users"),
    "create_on_public": (lambda db, r: db.execute(sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(_ident(r))),
                         "runtime role can CREATE in schema public"),
    "create_on_database": (lambda db, r: db.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(_ident(db.dbname), _ident(r))),
                           "runtime role can CREATE schemas in the database"),
    "extra_0010_like_table": (lambda db, r: db.execute("CREATE TABLE cloud_media_generations (id UUID PRIMARY KEY)"),
                              "unexpected tables: cloud_media_generations"),
    "extra_view": (lambda db, r: db.execute("CREATE VIEW cloud_all_items AS SELECT * FROM cloud_study_items"),
                   "unexpected views/sequences/foreign tables: cloud_all_items"),
    "extra_schema": (lambda db, r: db.execute("CREATE SCHEMA lap_shadow"),
                     "unexpected schemas: lap_shadow"),
    "rls_not_forced": (lambda db, r: db.execute("ALTER TABLE cloud_study_items NO FORCE ROW LEVEL SECURITY"),
                       "row level security is not enabled and forced on cloud_study_items"),
    "rls_disabled": (lambda db, r: db.execute("ALTER TABLE cloud_topic_sessions DISABLE ROW LEVEL SECURITY"),
                     "row level security is not enabled and forced on cloud_topic_sessions"),
    "dropped_constraint": (
        lambda db, r: db.execute("ALTER TABLE cloud_topic_sessions DROP CONSTRAINT cloud_topic_json_identity_required"),
        "missing constraint cloud_topic_json_identity_required"),
    "ledger_tampered": (
        lambda db, r: db.execute("UPDATE cloud_schema_migrations SET sha256 = repeat('0', 64) WHERE version='0002'"),
        "migration ledger: Ledger digest for 0002"),
    "ledger_unreadable": (
        lambda db, r: db.execute(sql.SQL("REVOKE SELECT ON cloud_schema_migrations FROM {}").format(_ident(r))),
        "runtime role cannot read cloud_schema_migrations"),
}


@pytest.mark.parametrize("case", sorted(NEGATIVE_CASES))
def test_preflight_refuses(cloud_db, case):
    dsn = cloud_db.runtime()
    mutate, expected = NEGATIVE_CASES[case]
    mutate(cloud_db, cloud_db.runtime_role)
    report = preflight.run_preflight(dsn)
    assert not report.ok
    assert any(expected in failure for failure in report.failures), report.failures
    with pytest.raises(preflight.CloudPreflightFailed):
        preflight.require_preflight(dsn)


def test_preflight_refuses_a_superuser_connection(cloud_db):
    migrations.apply(cloud_db.admin_dsn)
    if not cloud_db.scalar("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
        pytest.skip("TEST_POSTGRES_DSN is not a superuser")
    report = preflight.run_preflight(cloud_db.admin_dsn)
    assert "runtime role has SUPERUSER" in report.failures
    assert "runtime role owns relations" in " ".join(report.failures)


def test_preflight_requires_the_minimum_version(cloud_db):
    dsn = cloud_db.runtime(versions_target="0005")
    report = preflight.run_preflight(dsn, minimum_version="0006")
    assert "schema is at 0005, at least 0006 is required" in report.failures


def test_preflight_reports_connection_failure_without_the_dsn(cloud_db):
    migrations.apply(cloud_db.admin_dsn)
    bad = make_conninfo(cloud_db.admin_dsn, user="lap_p1_nobody", password="not-a-real-secret",
                        port="1")
    report = preflight.run_preflight(bad)
    assert report.failures == ["cannot connect to the runtime database"]
    assert "not-a-real-secret" not in repr(report)


# ---------------------------------------------------------------- runtime behaviour

def test_runtime_role_cannot_run_ddl_or_escape_rls(cloud_db):
    dsn = cloud_db.runtime()
    cloud_db.execute(
        "INSERT INTO cloud_users (user_id, created_at) VALUES (%s, %s), (%s, %s)",
        (OWNER_A, NOW, OWNER_B, NOW),
    )
    with psycopg.connect(dsn, autocommit=True) as connection:
        for statement in (
            "CREATE TABLE cloud_x (id INT)",
            "DROP TABLE cloud_users",
            "TRUNCATE cloud_study_items",
            "ALTER TABLE cloud_study_items DISABLE ROW LEVEL SECURITY",
            "DELETE FROM cloud_schema_migrations",
            "CREATE SCHEMA lap_x",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)
        admin_role = cloud_db.scalar("SELECT current_user")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(admin_role)))
    insert = (
        "INSERT INTO cloud_study_items (owner_user_id, item_id, kind, source_language,"
        " target_language, text, normalized_text, provenance_json, created_at, updated_at, due_at)"
        " VALUES (%s, %s, 'word', 'en', 'zh-CN', 'synthetic', 'synthetic', %s, %s, %s, %s)"
    )
    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT set_config('app.current_owner', %s, true)", (OWNER_A,))
        connection.execute(insert, (OWNER_A, str(uuid.uuid4()), PROVENANCE, NOW, NOW, NOW))
    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT set_config('app.current_owner', %s, true)", (OWNER_B,))
        assert connection.execute("SELECT count(*) FROM cloud_study_items").fetchone()[0] == 0
        assert connection.execute(
            "UPDATE cloud_study_items SET meaning = 'x' WHERE owner_user_id = %s", (OWNER_A,)
        ).rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            connection.execute(insert, (OWNER_A, str(uuid.uuid4()), PROVENANCE, NOW, NOW, NOW))
    with psycopg.connect(dsn) as connection:  # no owner set: nothing visible
        assert connection.execute("SELECT count(*) FROM cloud_study_items").fetchone()[0] == 0
    assert cloud_db.scalar("SELECT count(*) FROM cloud_study_items") == 1


def test_repositories_complete_a_learning_flow_as_the_runtime_role(cloud_db, tmp_path):
    """Grant table sufficiency: every Phase 1 learning repository works with only RUNTIME_GRANTS."""
    from services.ai_chunks.postgres import PostgresOwnerScopedAiChunkRepository
    from services.auth.sessions import BrowserSessionRepository
    from services.library.postgres import FilesystemMediaObjectStore, PostgresOwnerScopedLibraryRepository
    from services.study_bank.postgres import PostgresOwnerScopedStudyBankRepository
    from services.topics.postgres import PostgresTopicRepository
    import test_durable_ai_chunk_candidates as ai_tests
    import test_owner_scoped_study_bank as study_tests

    dsn = cloud_db.runtime()
    cloud_db.execute(
        "INSERT INTO cloud_users (user_id, created_at) VALUES (%s, %s), (%s, %s)",
        (OWNER_A, NOW, OWNER_B, NOW),
    )
    clock = ai_tests.MutableClock()
    study = PostgresOwnerScopedStudyBankRepository(dsn, clock=clock)
    library = PostgresOwnerScopedLibraryRepository(
        dsn, FilesystemMediaObjectStore(tmp_path / "media"), clock=clock)
    candidates = PostgresOwnerScopedAiChunkRepository(dsn, study, clock=clock)
    topics = PostgresTopicRepository(dsn)
    sessions = BrowserSessionRepository(dsn)

    # Library: create, attach audio, list, delete (outbox + media assets).
    item = library.create_for_owner(OWNER_A, ai_tests._lesson())
    library.put_audio_for_owner(OWNER_A, item.id, filename="clip.mp3", content_type="audio/mpeg",
                                content=b"ID3synthetic-audio")
    assert [i.id for i in library.list_for_owner(OWNER_A)] == [item.id]
    assert library.list_for_owner(OWNER_B) == []

    # Study Bank + Daily Review.
    saved = study.save_items_for_owner(OWNER_A, [study_tests._manual(OWNER_A)])[0][0]
    rated, _ = study.rate_item(OWNER_A, saved.id, "good", 0)
    assert rated.review.review_count == 1
    assert study.get_item(OWNER_A, saved.id).review == rated.review
    assert study.list_items(OWNER_B) == []

    # AI Chunk: durable candidate generation and atomic acceptance.
    service = ai_tests._service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, ai_tests._manual())
    accepted = service.decide(OWNER_A, batch.batch_id, ai_tests._decision(batch.candidates[0])).study_item
    assert accepted.id in {i.id for i in study.list_items(OWNER_A)}

    # Topic attempt with a target, a turn and a finish.
    started = topics.start(OWNER_A, "en-friends-reunion", 1, [accepted.id], "p1-start-001")
    sid = started["state"]["session_id"]
    topics.mutate(OWNER_A, "en-friends-reunion", sid, 1, 0, "p1-turn-001", text=accepted.text)
    finished = topics.mutate(OWNER_A, "en-friends-reunion", sid, 1, 2, "p1-finish-001", finish=True)
    assert finished["feedback"]["target_usage"][0]["use_count"] == 1
    with pytest.raises(KeyError):
        topics.get(OWNER_B, "en-friends-reunion", sid)

    # Browser session lifecycle (0005).
    from services.auth.identity import InvalidAuthentication
    token = sessions.issue_for_verified_account(OWNER_A)
    assert sessions.verify(token).user_id == OWNER_A
    rotated = sessions.rotate(token)
    assert sessions.verify(rotated).user_id == OWNER_A
    sessions.revoke(rotated)
    with pytest.raises(InvalidAuthentication):
        sessions.verify(rotated)

    library.delete_for_owner(OWNER_A, item.id)
    assert library.list_for_owner(OWNER_A) == []
    assert preflight.run_preflight(dsn).ok


# ---------------------------------------------------------------- operator CLI

def _cli(env_overrides, *args):
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items()
           if k not in {"CLOUD_ADMIN_DATABASE_URL", "CLOUD_RUNTIME_DATABASE_URL"}}
    env.update(env_overrides)
    script = Path(__file__).resolve().parents[1] / "scripts" / "cloud_db.py"
    return subprocess.run([sys.executable, str(script), *args], env=env,
                          capture_output=True, text=True, timeout=120)


def test_cli_requires_dsn_from_environment():
    result = _cli({}, "plan")
    assert result.returncode == 3
    assert "CLOUD_ADMIN_DATABASE_URL is not set" in result.stdout


def test_cli_end_to_end_never_prints_credentials(cloud_db):
    import json
    admin_env = {"CLOUD_ADMIN_DATABASE_URL": cloud_db.admin_dsn}
    plan_out = _cli(admin_env, "plan")
    assert plan_out.returncode == 0, plan_out.stdout + plan_out.stderr
    assert json.loads(plan_out.stdout)["pending"] == _present_versions()
    assert _cli(admin_env, "apply", "--target", "0010").returncode == 2
    assert _cli(admin_env, "apply").returncode == 0
    role = cloud_db.role()
    assert _cli(admin_env, "create-runtime-role", "--role", role).returncode == 0
    assert _cli(admin_env, "grant-runtime", "--role", role).returncode == 0
    cloud_db.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
        sql.Identifier(role), sql.Literal(SYNTHETIC_PASSWORD)))
    runtime_env = {"CLOUD_RUNTIME_DATABASE_URL": make_conninfo(
        cloud_db.admin_dsn, user=role, password=SYNTHETIC_PASSWORD)}
    good = _cli(runtime_env, "preflight")
    assert good.returncode == 0, good.stdout
    assert json.loads(good.stdout)["ok"] is True
    cloud_db.execute(sql.SQL("ALTER ROLE {} BYPASSRLS").format(sql.Identifier(role)))
    bad = _cli(runtime_env, "preflight")
    assert bad.returncode == 2
    assert "runtime role has BYPASSRLS" in json.loads(bad.stdout)["failures"]
    for output in (plan_out, good, bad):
        assert SYNTHETIC_PASSWORD not in output.stdout + output.stderr
        assert cloud_db.admin_dsn not in output.stdout + output.stderr


# ----------------------------------------------------------------------------
# Remediation batch 2 (independent review CHANGES_REQUIRED). Test IDs PRR-R*.
# ----------------------------------------------------------------------------

def _policies(db):
    with psycopg.connect(db.admin_dsn) as connection:
        return {
            (row[0], row[1]): row[2:]
            for row in connection.execute(
                "SELECT tablename, policyname, permissive, roles::text[], cmd, qual, with_check"
                " FROM pg_policies WHERE schemaname = 'public'")
        }


# PRR-R1: the pinned policy definitions equal what a clean apply produces.
def test_prr_r1_clean_apply_matches_the_pinned_policy_definitions(cloud_db):
    dsn = cloud_db.runtime()
    present = _present_versions()
    expected = schema.expected_policies(present)
    live = _policies(cloud_db)
    assert set(live) == set(expected)
    for key, definition in expected.items():
        assert live[key] == (definition.permissive, list(definition.roles), definition.command,
                             definition.using, definition.with_check)
    assert preflight.run_preflight(dsn).ok


POLICY_DRIFT = {
    "using_true": ("ALTER POLICY cloud_study_owner_policy ON cloud_study_items USING (true)",
                   "policy cloud_study_owner_policy on cloud_study_items has USING 'true'"),
    "with_check_true": ("ALTER POLICY cloud_study_owner_policy ON cloud_study_items WITH CHECK (true)",
                        "policy cloud_study_owner_policy on cloud_study_items has WITH CHECK 'true'"),
    "other_qualifier": ("ALTER POLICY cloud_topic_sessions_owner ON cloud_topic_sessions"
                        " USING (owner_user_id IS NOT NULL)",
                        "policy cloud_topic_sessions_owner on cloud_topic_sessions has USING"),
    "restrictive": ("DROP POLICY cloud_ai_batch_owner_policy ON cloud_ai_chunk_batches;"
                    " CREATE POLICY cloud_ai_batch_owner_policy ON cloud_ai_chunk_batches AS RESTRICTIVE"
                    " USING (owner_user_id = current_setting('app.current_owner', true))"
                    " WITH CHECK (owner_user_id = current_setting('app.current_owner', true))",
                    "policy cloud_ai_batch_owner_policy on cloud_ai_chunk_batches has permissive 'RESTRICTIVE'"),
    "command_select_only": ("DROP POLICY cloud_topic_targets_owner ON cloud_topic_targets;"
                            " CREATE POLICY cloud_topic_targets_owner ON cloud_topic_targets FOR SELECT"
                            " USING (owner_user_id = current_setting('app.current_owner', true))",
                            "policy cloud_topic_targets_owner on cloud_topic_targets has command 'SELECT'"),
    "roles_narrowed": ("ALTER POLICY cloud_study_media_owner_policy ON cloud_study_media_provenance TO {role}",
                       "policy cloud_study_media_owner_policy on cloud_study_media_provenance has roles"),
    "extra_permissive_true": ("CREATE POLICY lap_bypass ON cloud_study_items USING (true) WITH CHECK (true)",
                              "unexpected policy lap_bypass on cloud_study_items"),
    "extra_on_non_rls_table": ("CREATE POLICY lap_library ON cloud_library_items USING (true)",
                               "unexpected policy lap_library on cloud_library_items"),
    "dropped": ("DROP POLICY cloud_ai_request_owner_policy ON cloud_ai_chunk_generation_requests",
                "missing policy cloud_ai_request_owner_policy on cloud_ai_chunk_generation_requests"),
}


# PRR-R2: any change to a policy definition, or any extra/missing policy, is refused.
@pytest.mark.parametrize("case", sorted(POLICY_DRIFT))
def test_prr_r2_policy_drift_is_refused(cloud_db, case):
    dsn = cloud_db.runtime()
    statement, expected = POLICY_DRIFT[case]
    for part in statement.split(";"):
        if "{role}" in part:
            cloud_db.execute(sql.SQL(part.replace("{role}", "{}")).format(sql.Identifier(cloud_db.runtime_role)))
        else:
            cloud_db.execute(part)
    report = preflight.run_preflight(dsn)
    assert not report.ok
    assert any(expected in failure for failure in report.failures), report.failures
    with pytest.raises(preflight.CloudPreflightFailed):
        preflight.require_preflight(dsn)


# PRR-R2b: the drift is a real isolation break, not a cosmetic difference.
def test_prr_r2b_using_true_lets_the_runtime_role_read_other_owners(cloud_db):
    dsn = cloud_db.runtime()
    cloud_db.execute("INSERT INTO cloud_users (user_id, created_at) VALUES (%s, %s), (%s, %s)",
                     (OWNER_A, NOW, OWNER_B, NOW))
    cloud_db.execute(
        "INSERT INTO cloud_study_items (owner_user_id, item_id, kind, source_language, target_language,"
        " text, normalized_text, provenance_json, created_at, updated_at, due_at)"
        " VALUES (%s, %s, 'word', 'en', 'zh-CN', 'x', 'x', %s, %s, %s, %s)",
        (OWNER_A, str(uuid.uuid4()), PROVENANCE, NOW, NOW, NOW))
    cloud_db.execute("ALTER POLICY cloud_study_owner_policy ON cloud_study_items USING (true)")
    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT set_config('app.current_owner', %s, true)", (OWNER_B,))
        assert connection.execute("SELECT count(*) FROM cloud_study_items").fetchone()[0] == 1
    assert not preflight.run_preflight(dsn).ok


# PRR-R3: a column-level SELECT never stands in for the required table-level SELECT.
@pytest.mark.parametrize("privilege, table, columns", [
    ("SELECT", "cloud_users", "user_id"),
    ("UPDATE", "cloud_study_items", "meaning"),
    ("INSERT", "cloud_topic_requests", "owner_user_id"),
])
def test_prr_r3_column_level_grant_does_not_satisfy_a_table_level_requirement(
        cloud_db, privilege, table, columns):
    dsn = cloud_db.runtime()
    role = sql.Identifier(cloud_db.runtime_role)
    cloud_db.execute(sql.SQL("REVOKE {} ON {} FROM {}").format(sql.SQL(privilege), sql.Identifier(table), role))
    cloud_db.execute(sql.SQL("GRANT {} ({}) ON {} TO {}").format(
        sql.SQL(privilege), sql.Identifier(columns), sql.Identifier(table), role))
    report = preflight.run_preflight(dsn)
    assert (f"missing table-level privilege {privilege} on {table} (only column-level grants present)"
            in report.failures), report.failures


# PRR-R4: required and forbidden privileges are verified and reported separately.
def test_prr_r4_missing_and_excess_privileges_are_reported_independently(cloud_db):
    dsn = cloud_db.runtime()
    role = sql.Identifier(cloud_db.runtime_role)
    cloud_db.execute(sql.SQL("REVOKE DELETE ON cloud_library_items FROM {}").format(role))
    cloud_db.execute(sql.SQL("GRANT DELETE ON cloud_users TO {}").format(role))
    cloud_db.execute(sql.SQL("GRANT SELECT (owner_user_id) ON cloud_study_media_provenance TO {}").format(role))
    failures = preflight.run_preflight(dsn).failures
    assert "missing table-level privilege DELETE on cloud_library_items" in failures
    assert "excess table-level privilege DELETE on cloud_users" in failures
    assert "excess column-level privilege SELECT on cloud_study_media_provenance" in failures


# PRR-R5: an empty ledger fails closed everywhere it is read.
def test_prr_r5_empty_ledger_fails_closed(cloud_db):
    dsn = cloud_db.runtime()
    cloud_db.execute("DELETE FROM cloud_schema_migrations")
    report = preflight.run_preflight(dsn)
    assert not report.ok
    assert "migration ledger: Migration ledger is empty" in report.failures
    with pytest.raises(migrations.MigrationRefused, match="ledger is empty but the database holds tables"):
        migrations.apply(cloud_db.admin_dsn)
    with pytest.raises(migrations.MigrationRefused, match="ledger is empty but the database holds tables"):
        migrations.plan(cloud_db.admin_dsn)
    with pytest.raises(roles.RoleRefused, match="Migration ledger is empty"):
        roles.grant_runtime(cloud_db.admin_dsn, cloud_db.runtime_role)


# PRR-R6: an empty ledger with no other tables (first version failed) can resume.
def test_prr_r6_empty_ledger_without_tables_resumes_safely(cloud_db, tmp_path):
    import hashlib
    directory = tmp_path / "m"
    directory.mkdir()
    broken = b"BEGIN;\nSELECT 1 / 0;\nCOMMIT;\n"
    (directory / "0001_broken.up.sql").write_bytes(broken)
    (directory / schema.MANIFEST_NAME).write_text(f"{hashlib.sha256(broken).hexdigest()}  0001_broken.up.sql\n")
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrations.apply(cloud_db.admin_dsn, directory=directory)
    assert cloud_db.tables() == {"cloud_schema_migrations"}
    assert cloud_db.scalar("SELECT count(*) FROM cloud_schema_migrations") == 0
    assert migrations.apply(cloud_db.admin_dsn) == _present_versions()


# PRR-R7: schema USAGE is a required privilege too.
def test_prr_r7_missing_schema_usage_is_refused(cloud_db):
    dsn = cloud_db.runtime()
    cloud_db.execute(sql.SQL("REVOKE USAGE ON SCHEMA public FROM {}").format(sql.Identifier(cloud_db.runtime_role)))
    cloud_db.execute("REVOKE USAGE ON SCHEMA public FROM PUBLIC")
    assert "missing USAGE on schema public" in preflight.run_preflight(dsn).failures
