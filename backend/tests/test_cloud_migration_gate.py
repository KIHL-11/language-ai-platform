"""Phase 1.5 migration gate (MG-1..MG-10).

The real Phase 1.5 set is 0014 (Library RLS). These tests approve a synthetic
0015 on top of it in a temporary migration directory to prove the rules: a version applies
only when pinned in code *and* in the manifest, in order, without gaps; 0010-0013
are always refused; unknown versions are refused; schema, policy, privilege and
ledger checks cover the new version exactly; a failed version leaves nothing
behind and can be retried safely.
"""
from __future__ import annotations

import hashlib
import shutil
import threading
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from services.cloud import migrations, preflight, roles, schema
from services.cloud.migrations import MigrationRefused
from test_cloud_db_foundation import SYNTHETIC_PASSWORD, cloud_db  # noqa: F401  (fixture)

PROBE = "cloud_gate_probe"
POLICY = "cloud_gate_probe_owner_policy"
GOOD_SYNTH = f"""BEGIN;
CREATE TABLE {PROBE} (
    owner_user_id VARCHAR(128) NOT NULL REFERENCES cloud_users(user_id) ON DELETE RESTRICT
);
ALTER TABLE {PROBE} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {PROBE} FORCE ROW LEVEL SECURITY;
CREATE POLICY {POLICY} ON {PROBE}
    USING (owner_user_id = current_setting('app.current_owner', true))
    WITH CHECK (owner_user_id = current_setting('app.current_owner', true));
COMMIT;
""".encode()
FAILING_SYNTH = f"""BEGIN;
CREATE TABLE {PROBE} (owner_user_id VARCHAR(128) NOT NULL);
SELECT 1 / 0;
COMMIT;
""".encode()
NAME_SYNTH = "0015_gate_probe.up.sql"
PHASE1 = [f"{i:04d}" for i in range(1, 10)]
APPLIED = PHASE1 + ["0014"]  # the real Library RLS version precedes the synthetic one
SYNTH = "0015"


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _tree(tmp_path: Path, extra: dict[str, bytes] | None = None, manifest_extra: list[str] | None = None) -> Path:
    """Copy of the real 0001-0009 set plus extra files and manifest lines."""
    directory = tmp_path / "migrations"
    shutil.copytree(schema.MIGRATIONS_DIR, directory)
    for name, body in (extra or {}).items():
        (directory / name).write_bytes(body)
    if manifest_extra:
        with (directory / schema.MANIFEST_NAME).open("a", encoding="utf-8") as handle:
            handle.writelines(line + "\n" for line in manifest_extra)
    return directory


@pytest.fixture
def approve_synth(monkeypatch):
    """Approve a synthetic 0015 in code, with its exact schema description."""
    def approve(body: bytes = GOOD_SYNTH, *, name: str = NAME_SYNTH, digest: str | None = None):
        monkeypatch.setitem(schema.PHASE15_ALLOWED, SYNTH, (name, digest or _digest(body)))
        monkeypatch.setitem(schema.TABLES_BY_VERSION, SYNTH, (PROBE,))
        monkeypatch.setitem(schema.FORCED_RLS_BY_VERSION, SYNTH, (PROBE,))
        monkeypatch.setitem(schema.POLICY_NAMES, PROBE, POLICY)
        monkeypatch.setitem(schema.RUNTIME_GRANTS, PROBE, frozenset({"SELECT", "INSERT"}))
    return approve


def _runtime(cloud_db, directory):  # noqa: F811
    role = cloud_db.role()
    roles.create_runtime_role(cloud_db.admin_dsn, role)
    roles.grant_runtime(cloud_db.admin_dsn, role, directory=directory)
    cloud_db.runtime_role = role
    cloud_db.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
        sql.Identifier(role), sql.Literal(SYNTHETIC_PASSWORD)))
    return make_conninfo(cloud_db.admin_dsn, user=role, password=SYNTHETIC_PASSWORD)


def _ledger(cloud_db):  # noqa: F811
    return cloud_db.scalar("SELECT array_agg(version ORDER BY version) FROM cloud_schema_migrations")


# MG-1: the real tree is exactly 0001-0009 plus the approved 0014, pinned in code and manifest alike.
def test_mg1_real_tree_is_exactly_the_pinned_phase1_set():
    assert schema.PHASE15_ALLOWED == {"0014": (
        "0014_library_row_level_security.up.sql",
        "a8ece18b59b42f24a530c23a6ccec19299c41678b0aca1903b50230437a56c0c")}
    manifest = schema.load_manifest()
    assert [m.version for m in manifest] == APPLIED
    for migration in manifest:
        assert (migration.filename, migration.sha256) == schema.approved_migrations()[migration.version]
        assert _digest(migration.path.read_bytes()) == migration.sha256


# MG-2: an approved, manifest-pinned 0015 applies after 0014 and passes exact preflight.
def test_mg2_approved_0015_applies_and_passes_exact_preflight(cloud_db, tmp_path, approve_synth):  # noqa: F811
    approve_synth()
    directory = _tree(tmp_path, {NAME_SYNTH: GOOD_SYNTH}, [f"{_digest(GOOD_SYNTH)}  {NAME_SYNTH}"])
    assert migrations.apply(cloud_db.admin_dsn, directory=directory) == APPLIED + [SYNTH]
    assert _ledger(cloud_db) == APPLIED + [SYNTH]
    dsn = _runtime(cloud_db, directory)
    report = preflight.run_preflight(dsn, minimum_version=SYNTH, directory=directory)
    assert report.ok, report.failures
    assert migrations.apply(cloud_db.admin_dsn, directory=directory) == []  # idempotent


# MG-3: a manifest line plus file without code approval is refused before any write.
def test_mg3_manifest_only_addition_is_refused(cloud_db, tmp_path):  # noqa: F811
    directory = _tree(tmp_path, {NAME_SYNTH: GOOD_SYNTH}, [f"{_digest(GOOD_SYNTH)}  {NAME_SYNTH}"])
    with pytest.raises(MigrationRefused, match="not an approved migration"):
        migrations.apply(cloud_db.admin_dsn, directory=directory)
    assert cloud_db.tables() == set()


# MG-4: the code pin and the manifest must agree; a tampered file is refused at apply.
def test_mg4_digest_or_name_mismatch_and_tampered_file_are_refused(cloud_db, tmp_path, approve_synth):  # noqa: F811
    approve_synth(digest="0" * 64)
    directory = _tree(tmp_path, {NAME_SYNTH: GOOD_SYNTH}, [f"{_digest(GOOD_SYNTH)}  {NAME_SYNTH}"])
    with pytest.raises(MigrationRefused, match="does not match the pinned file name and digest"):
        migrations.apply(cloud_db.admin_dsn, directory=directory)

    approve_synth(name="0015_other_name.up.sql")
    with pytest.raises(MigrationRefused, match="does not match the pinned file name and digest"):
        migrations.apply(cloud_db.admin_dsn, directory=directory)

    approve_synth()
    (directory / NAME_SYNTH).write_bytes(GOOD_SYNTH + b"-- tampered\n")
    with pytest.raises(MigrationRefused, match="does not match the pinned digest"):
        migrations.apply(cloud_db.admin_dsn, directory=directory)
    assert cloud_db.tables() == set()


# MG-5: 0010-0013 are refused in the manifest, as a target, in the allowlist and in the ledger.
@pytest.mark.parametrize("reserved", ["0010", "0011", "0012", "0013"])
def test_mg5_reserved_versions_are_refused_everywhere(cloud_db, tmp_path, monkeypatch, reserved):  # noqa: F811
    body = b"BEGIN;\nSELECT 1;\nCOMMIT;\n"
    name = f"{reserved}_frozen.up.sql"
    directory = _tree(tmp_path, {name: body}, [f"{_digest(body)}  {name}"])
    with pytest.raises(MigrationRefused, match="reserved for the frozen B-series"):
        migrations.apply(cloud_db.admin_dsn, directory=directory)
    with pytest.raises(MigrationRefused, match="reserved for the frozen B-series"):
        migrations.apply(cloud_db.admin_dsn, target=reserved)
    with monkeypatch.context() as patched:
        patched.setitem(schema.PHASE15_ALLOWED, reserved, (name, _digest(body)))
        with pytest.raises(schema.SchemaDefinitionError, match="must be above 0013"):
            schema.approved_migrations()
    with pytest.raises(ValueError, match="reserved for the frozen B-series"):
        schema.validate_version(reserved)

    migrations.apply(cloud_db.admin_dsn)
    dsn = _runtime(cloud_db, schema.MIGRATIONS_DIR)
    assert preflight.run_preflight(dsn, minimum_version="0009").ok
    cloud_db.execute(  # a forged ledger row for a frozen version
        "INSERT INTO cloud_schema_migrations (version, filename, sha256, server_version_num) "
        "VALUES (%s, %s, %s, 160000)", (reserved, name, _digest(body)))
    assert not preflight.run_preflight(dsn, minimum_version="0009").ok
    with pytest.raises(MigrationRefused, match="beyond the pinned manifest|not a contiguous prefix of the manifest"):
        migrations.apply(cloud_db.admin_dsn)
    with pytest.raises(roles.RoleRefused, match="beyond the pinned manifest|not a contiguous prefix of the manifest"):
        roles.grant_runtime(cloud_db.admin_dsn, cloud_db.runtime_role)


# MG-6: no gaps and no unknown versions.
def test_mg6_gaps_and_unknown_versions_are_refused(cloud_db, tmp_path, approve_synth):  # noqa: F811
    approve_synth()
    directory = _tree(tmp_path, {NAME_SYNTH: GOOD_SYNTH})
    manifest = directory / schema.MANIFEST_NAME
    lines = manifest.read_text().splitlines()
    manifest.write_text("\n".join(line for line in lines if "0009_" not in line)
                        + f"\n{_digest(GOOD_SYNTH)}  {NAME_SYNTH}\n")
    with pytest.raises(MigrationRefused, match="approved order from 0001 without gaps"):
        migrations.apply(cloud_db.admin_dsn, directory=directory)

    unknown = b"BEGIN;\nSELECT 1;\nCOMMIT;\n"
    directory = _tree(tmp_path / "u", {"0016_unknown.up.sql": unknown},
                      [f"{_digest(unknown)}  0016_unknown.up.sql"])
    with pytest.raises(MigrationRefused, match="not an approved migration"):
        migrations.apply(cloud_db.admin_dsn, directory=directory)
    with pytest.raises(ValueError, match="not an approved migration"):
        schema.validate_version("0016")
    assert cloud_db.tables() == set()


# MG-7: preflight checks the new version exactly (policy, FORCE RLS, privileges, ledger digest).
@pytest.mark.parametrize("mutation, reason", [
    (f"ALTER TABLE {PROBE} NO FORCE ROW LEVEL SECURITY", PROBE),
    (f"DROP POLICY {POLICY} ON {PROBE}", POLICY),
    ("GRANT UPDATE ON {probe} TO {role}", PROBE),
    ("UPDATE cloud_schema_migrations SET sha256 = repeat('0', 64) WHERE version = '0015'", "0015"),
])
def test_mg7_preflight_is_exact_for_the_new_version(cloud_db, tmp_path, approve_synth, mutation, reason):  # noqa: F811
    approve_synth()
    directory = _tree(tmp_path, {NAME_SYNTH: GOOD_SYNTH}, [f"{_digest(GOOD_SYNTH)}  {NAME_SYNTH}"])
    migrations.apply(cloud_db.admin_dsn, directory=directory)
    dsn = _runtime(cloud_db, directory)
    assert preflight.run_preflight(dsn, minimum_version=SYNTH, directory=directory).ok
    if "{role}" in mutation:
        cloud_db.execute(sql.SQL(mutation).format(
            probe=sql.Identifier(PROBE), role=sql.Identifier(cloud_db.runtime_role)))
    else:
        cloud_db.execute(mutation)
    report = preflight.run_preflight(dsn, minimum_version=SYNTH, directory=directory)
    assert not report.ok
    assert any(reason in failure for failure in report.failures), report.failures


# MG-8: a failing 0015 leaves neither objects nor a ledger row; retrying fails the same clean way.
def test_mg8_failed_version_is_atomic_and_retry_stays_clean(cloud_db, tmp_path, approve_synth):  # noqa: F811
    approve_synth(FAILING_SYNTH)
    directory = _tree(tmp_path, {NAME_SYNTH: FAILING_SYNTH}, [f"{_digest(FAILING_SYNTH)}  {NAME_SYNTH}"])
    for _ in range(2):
        with pytest.raises(psycopg.errors.DivisionByZero):
            migrations.apply(cloud_db.admin_dsn, directory=directory)
        assert PROBE not in cloud_db.tables()
        assert _ledger(cloud_db) == APPLIED
    assert preflight.run_preflight(_runtime(cloud_db, directory), minimum_version="0014",
                                   directory=directory).ok


# MG-9: a transient lock conflict aborts 0015 cleanly; once released, a retry applies it exactly once.
def test_mg9_transient_lock_timeout_then_safe_retry(cloud_db, tmp_path, approve_synth, monkeypatch):  # noqa: F811
    approve_synth()
    directory = _tree(tmp_path, {NAME_SYNTH: GOOD_SYNTH}, [f"{_digest(GOOD_SYNTH)}  {NAME_SYNTH}"])
    assert migrations.apply(cloud_db.admin_dsn, directory=directory, target="0014") == APPLIED
    monkeypatch.setattr(migrations, "LOCK_TIMEOUT", "300ms")
    holder = psycopg.connect(cloud_db.admin_dsn)
    try:
        holder.execute("LOCK TABLE cloud_users IN ACCESS EXCLUSIVE MODE")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            migrations.apply(cloud_db.admin_dsn, directory=directory)
        assert PROBE not in cloud_db.tables()
        assert _ledger(cloud_db) == APPLIED
    finally:
        holder.rollback()
        holder.close()
    assert migrations.apply(cloud_db.admin_dsn, directory=directory) == [SYNTH]
    assert _ledger(cloud_db) == APPLIED + [SYNTH]


# MG-10: only one runner at a time; the second is refused without writing.
def test_mg10_concurrent_runner_is_refused(cloud_db, tmp_path, approve_synth):  # noqa: F811
    approve_synth()
    directory = _tree(tmp_path, {NAME_SYNTH: GOOD_SYNTH}, [f"{_digest(GOOD_SYNTH)}  {NAME_SYNTH}"])
    migrations.apply(cloud_db.admin_dsn, directory=directory, target="0014")
    with psycopg.connect(cloud_db.admin_dsn, autocommit=True) as holder:
        holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (migrations.LOCK_KEY,))
        errors: list[Exception] = []
        worker = threading.Thread(target=lambda: _capture(errors, cloud_db.admin_dsn, directory))
        worker.start()
        worker.join(timeout=30)
        assert len(errors) == 1 and isinstance(errors[0], MigrationRefused)
        assert "holds the lock" in str(errors[0])
    assert _ledger(cloud_db) == APPLIED


def _capture(errors, dsn, directory):
    try:
        migrations.apply(dsn, directory=directory)
    except Exception as exc:  # noqa: BLE001 - recorded for the assertion above
        errors.append(exc)
