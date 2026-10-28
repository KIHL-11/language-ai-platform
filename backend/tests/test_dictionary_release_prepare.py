"""Legacy rollback conversion, candidate preparation, and build-phase fault injection."""

import gzip
import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from migration_support import (
    NOW,
    MigrationEnvironment,
    make_writable,
    release_dir,
    tree_state,
)
from services.dictionary import legacy_release as legacy_module
from services.dictionary import release_prepare as prepare_module
from services.dictionary.forms_sidecar import lookup_forms
from services.dictionary.legacy_release import (
    LegacyConversionError,
    create_legacy_rollback_release,
    legacy_fingerprint,
)
from services.dictionary.release_manifest import (
    ManifestValidationError,
    file_sha256,
    validate_published_release,
    validate_ready_eligibility,
    validate_rollback_release,
)
from services.dictionary.release_prepare import PrepareError, RepositoryState
from services.dictionary.release_store import (
    ReleaseStoreError,
    disk_budget,
    resolve_layout,
    switch_current,
)


@pytest.fixture()
def env(tmp_path):
    return MigrationEnvironment(tmp_path)


def _legacy_identity(env):
    stat_result = env.legacy.stat()
    return file_sha256(env.legacy), stat_result.st_mtime_ns, stat_result.st_ino


# ----------------------------------------------------------------- legacy

def test_legacy_dry_run_creates_nothing(env):
    before = tree_state(env.root)
    plan = create_legacy_rollback_release(resolve_layout(env.storage), env.legacy, dry_run=True)
    assert plan["action"] == "would_create" and plan["copy_method"] == "byte_copy"
    assert plan["build_id"].startswith("legacy-")
    assert tree_state(env.root) == before


def test_legacy_preflight_rejects_insufficient_disk_without_creating_release(env):
    layout = env.layout
    before = tree_state(env.root)
    with pytest.raises(LegacyConversionError, match="insufficient disk space"):
        create_legacy_rollback_release(
            layout,
            env.legacy,
            minimum_headroom_bytes=0,
            disk_usage=lambda _path: type("Usage", (), {"free": 1})(),
        )
    assert tree_state(env.root) == before


def test_legacy_ready_is_not_written_before_independent_validation(
    env, monkeypatch
):
    def reject(*_args):
        raise ManifestValidationError("simulated independent validation failure")

    monkeypatch.setattr(legacy_module, "validate_rollback_eligibility", reject)
    with pytest.raises(ManifestValidationError, match="simulated independent"):
        create_legacy_rollback_release(
            env.layout,
            env.legacy,
            minimum_headroom_bytes=0,
        )
    release = next(env.layout.releases.iterdir())
    assert not (release / "READY").exists()


def test_legacy_conversion_copies_without_touching_the_installed_database(env):
    identity = _legacy_identity(env)
    learning = env.learning_hashes()
    build_id = env.legacy_release()
    release = release_dir(env, build_id)
    assert _legacy_identity(env) == identity
    assert env.learning_hashes() == learning
    assert file_sha256(release / "dictionary.sqlite3") == identity[0]
    assert sorted(path.name for path in release.iterdir()) == [
        "READY", "dictionary.forms.sqlite3", "dictionary.sqlite3",
        "legacy-provenance.json", "rollback-verification.json",
    ]
    ready = (release / "READY").read_text().strip()
    assert ready == hashlib.sha256((release / "rollback-verification.json").read_bytes()).hexdigest()
    provenance = json.loads((release / "legacy-provenance.json").read_text())
    assert provenance["source_provenance"] == "unverified"
    assert "/" not in provenance["legacy_database"]["name"]
    assert str(env.root) not in (release / "legacy-provenance.json").read_text()
    # Fresh Sidecar against the final release path; sealed read-only.
    assert lookup_forms(release / "dictionary.sqlite3", release / "dictionary.forms.sqlite3", 1).status == "available"
    assert not os.access(release, os.W_OK) or os.geteuid() == 0
    assert stat.S_IMODE((release / "dictionary.sqlite3").stat().st_mode) == 0o444
    assert stat.S_IMODE(release.stat().st_mode) == 0o555
    validate_rollback_release(env.layout.releases, build_id)


def test_legacy_conversion_is_idempotent_and_reverifies(env):
    build_id = env.legacy_release()
    before = tree_state(env.storage)
    again = create_legacy_rollback_release(env.layout, env.legacy, now=NOW)
    assert again["action"] == "verified_existing" and again["build_id"] == build_id
    assert tree_state(env.storage) == before


def test_incomplete_legacy_directory_is_preserved_and_retry_uses_new_name(env):
    plan = create_legacy_rollback_release(env.layout, env.legacy, dry_run=True)
    partial = release_dir(env, plan["build_id"])
    partial.mkdir()
    (partial / "dictionary.sqlite3").write_bytes(b"half-copied")
    result = create_legacy_rollback_release(env.layout, env.legacy, now=NOW)
    assert result["build_id"] == plan["build_id"] + "-r2"
    assert result["preserved_incomplete_attempts"] == [plan["build_id"]]
    assert (partial / "dictionary.sqlite3").read_bytes() == b"half-copied"
    again = create_legacy_rollback_release(env.layout, env.legacy, now=NOW)
    assert again["action"] == "verified_existing" and again["build_id"] == result["build_id"]


def test_wal_mode_header_without_wal_file_uses_backup(env):
    connection = sqlite3.connect(env.legacy)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.close()  # clean close: no -wal file, header still says WAL
    assert not Path(f"{env.legacy}-wal").exists()
    layout = env.layout
    before = sorted(path.name for path in env.storage.iterdir())
    result = create_legacy_rollback_release(layout, env.legacy, now=NOW)
    assert result["copy_method"] == "byte_copy_then_delete_journal"
    release = release_dir(env, result["build_id"])
    assert not Path(f"{release / 'dictionary.sqlite3'}-wal").exists()
    # Nothing new appears beside the installed database (no -wal/-shm).
    after = sorted(path.name for path in env.storage.iterdir())
    assert after == before
    copy = sqlite3.connect(f"file:{release / 'dictionary.sqlite3'}?mode=ro", uri=True)
    try:
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        copy.close()
    validate_rollback_release(env.layout.releases, result["build_id"])


def test_legacy_wal_state_uses_sqlite_backup_and_never_touches_wal(env, tmp_path):
    # Build a crash-state copy: main file plus an uncheckpointed WAL.
    work = tmp_path / "wal-work"
    work.mkdir()
    database = work / "dictionary.sqlite3"
    database.write_bytes(env.legacy.read_bytes())
    writer = sqlite3.connect(database)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("INSERT INTO metadata(key, value) VALUES ('wal_only_row', 'present')")
    writer.commit()
    crash = tmp_path / "crash"
    crash.mkdir()
    for suffix in ("", "-wal"):
        (crash / f"dictionary.sqlite3{suffix}").write_bytes(Path(f"{database}{suffix}").read_bytes())
    writer.close()
    legacy = env.storage / "dictionary.sqlite3"
    legacy.chmod(0o644)
    legacy.write_bytes((crash / "dictionary.sqlite3").read_bytes())
    Path(f"{legacy}-wal").write_bytes((crash / "dictionary.sqlite3-wal").read_bytes())
    main_hash = file_sha256(legacy)
    wal_hash = file_sha256(Path(f"{legacy}-wal"))

    result = create_legacy_rollback_release(env.layout, legacy, now=NOW)
    assert result["copy_method"] == "sqlite_backup"
    release = release_dir(env, result["build_id"])
    assert file_sha256(legacy) == main_hash
    assert file_sha256(Path(f"{legacy}-wal")) == wal_hash
    assert not Path(f"{release / 'dictionary.sqlite3'}-wal").exists()
    copy = sqlite3.connect(f"file:{release / 'dictionary.sqlite3'}?mode=ro", uri=True)
    try:
        assert copy.execute("SELECT value FROM metadata WHERE key='wal_only_row'").fetchone() == ("present",)
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        copy.close()
    validate_rollback_release(env.layout.releases, result["build_id"])


def test_hot_rollback_journal_blocks_conversion(env):
    Path(f"{env.legacy}-journal").write_bytes(b"hot-journal")
    with pytest.raises(LegacyConversionError, match="rollback journal"):
        create_legacy_rollback_release(env.layout, env.legacy)
    assert not any(env.layout.releases.iterdir())


def test_symlinked_legacy_database_is_refused(env, tmp_path):
    alias = env.storage / "alias.sqlite3"
    alias.symlink_to(env.legacy)
    with pytest.raises(LegacyConversionError, match="non-symlink"):
        legacy_fingerprint(alias)


# ---------------------------------------------------------------- prepare

def test_prepare_dry_run_is_read_only(env):
    rollback = env.legacy_release()
    before = tree_state(env.root)
    plan = env.prepare(rollback, dry_run=True)
    assert plan["action"] == "would_prepare"
    assert plan["build_id"].startswith("20261027T120000Z-")
    assert tree_state(env.root) == before


def test_prepare_publishes_a_complete_ready_release(env):
    rollback = env.legacy_release()
    legacy_identity = _legacy_identity(env)
    learning = env.learning_hashes()
    result = env.prepare(rollback)
    release = release_dir(env, result["build_id"])
    assert sorted(path.name for path in release.iterdir()) == [
        "READY", "build-report.json", "dictionary.forms.sqlite3", "dictionary.sqlite3",
        "manifest.json", "quality-report.json",
    ]
    manifest_bytes = (release / "manifest.json").read_bytes()
    assert (release / "READY").read_text().strip() == hashlib.sha256(manifest_bytes).hexdigest()
    manifest = validate_published_release(release, env.layout.releases, env.sources)
    assert manifest["rollback"]["previous_build_id"] == rollback
    telemetry = manifest["statistics"]["alignment_telemetry"]
    assert telemetry["instrumentation_status"] == "complete"
    assert telemetry["entry_level"]["seen"] == (
        telemetry["entry_level"]["accepted"]["total"] + telemetry["entry_level"]["rejected"]["total"]
    )
    quality = json.loads((release / "quality-report.json").read_text())
    assert quality["gold_case_failures"] == 0
    assert quality["gold_cases"]["incorrect_associations"] == 0
    gold = {case["id"]: case for case in quality["gold_cases"]["cases"]}
    assert gold["en-get-understand"]["chinese"] == "明白"
    assert gold["en-bank-river"]["chinese"] == "河岸"
    assert gold["de-entwicklung-honest-missing"]["classification"] == "coverage_gap"
    assert gold["de-compound-no-fabrication"]["chinese"] is None
    assert quality["snapshot_comparison"]["attribution"].startswith("confounded")
    assert quality["structural_comparison"]["entries"]["en"]["baseline"] > 0
    assert quality["performance"]["passed"] is True
    build_report = json.loads((release / "build-report.json").read_text())
    assert build_report["import_telemetry"]["totals"] == telemetry
    for name in ("manifest.json", "quality-report.json", "build-report.json"):
        text = (release / name).read_text()
        assert str(env.root) not in text
    assert _legacy_identity(env) == legacy_identity
    assert env.learning_hashes() == learning
    assert stat.S_IMODE(release.stat().st_mode) == 0o555


def test_published_candidate_can_serve_as_a_later_rollback(env):
    legacy = env.legacy_release()
    candidate = env.prepare(legacy)["build_id"]
    validate_rollback_release(env.layout.releases, candidate)
    switch_current(env.layout, candidate)
    second = env.prepare(candidate, now=NOW.replace(hour=13))
    manifest = json.loads((release_dir(env, second["build_id"]) / "manifest.json").read_text())
    assert manifest["validation"]["snapshot_comparison"]["same_source_hashes_as_baseline"] is True
    assert manifest["validation"]["snapshot_comparison"]["attribution"].startswith("importer effect only")


def test_prepare_requires_rollback_to_be_current_release(env):
    legacy = env.legacy_release()
    candidate = env.prepare(legacy)["build_id"]
    switch_current(env.layout, candidate)
    with pytest.raises(PrepareError, match="dictionary_current points to"):
        env.prepare(legacy, now=NOW.replace(hour=13))


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("source_sha", "SHA-256"),
        ("source_size", "size"),
        ("truncated_gzip", "complete gzip"),
        ("license_unverified", "license is not verified"),
        ("license_mismatch", "incompatible"),
        ("missing_rollback", "rollback"),
        ("dirty_worktree", "not clean"),
        ("short_sha", "40-character"),
        ("fixture_gold", "synthetic fixture"),
        ("unapproved_gold", "approved"),
        ("disk", "insufficient disk space"),
        ("symlink_artifact", "escapes"),
    ],
)
def test_preflight_failures_create_no_release(env, damage, message, tmp_path):
    rollback = env.legacy_release()
    overrides = {}
    if damage == "source_sha":
        env.write_evidence([env.source_record("en", snapshot_identity={"sha256": "a" * 64, "size_bytes": env.artifacts["en"].stat().st_size}), env.source_record("de")])
    elif damage == "source_size":
        record = env.source_record("en")
        record["snapshot_identity"]["size_bytes"] += 1
        env.write_evidence([record, env.source_record("de")])
    elif damage == "truncated_gzip":
        env.artifacts["en"].write_bytes(env.artifacts["en"].read_bytes()[:-8])
        env.write_evidence()  # hashes now describe the truncated file
    elif damage == "license_unverified":
        record = env.source_record("en")
        record["license"]["verified"] = False
        env.write_evidence([record, env.source_record("de")])
    elif damage == "license_mismatch":
        record = env.source_record("de")
        record["license"]["label"] = "Another license"
        env.write_evidence([env.source_record("en"), record])
    elif damage == "missing_rollback":
        rollback = "legacy-0000000000000000"
    elif damage == "dirty_worktree":
        overrides["repository"] = RepositoryState("https://github.com/x/y.git", "a" * 40, False, "3.12")
    elif damage == "short_sha":
        overrides["repository"] = RepositoryState("https://github.com/x/y.git", "abc123", True, "3.12")
    elif damage == "fixture_gold":
        overrides["allow_fixture_gold"] = False
    elif damage == "unapproved_gold":
        gold = json.loads(env.gold.read_text())
        gold["approved"] = False
        unapproved = tmp_path / "gold.json"
        unapproved.write_text(json.dumps(gold))
        overrides["gold_set"] = unapproved
    elif damage == "disk":
        overrides["disk_usage"] = lambda path: type("Usage", (), {"free": 1024})()
    elif damage == "symlink_artifact":
        real = tmp_path / "elsewhere.jsonl.gz"
        real.write_bytes(env.artifacts["en"].read_bytes())
        env.artifacts["en"].unlink()
        env.artifacts["en"].symlink_to(real)
    before = sorted(path.name for path in env.layout.releases.iterdir())
    with pytest.raises(PrepareError, match=message):
        env.prepare(rollback, **overrides)
    assert sorted(path.name for path in env.layout.releases.iterdir()) == before


def test_disk_budget_matches_design_formula():
    gib = 1024 ** 3
    budget = disk_budget(source_bytes=3_392_331_563, dictionary_bytes=int(2.5 * gib), sidecar_bytes=gib // 2)
    working = 3_392_331_563 + 3 * int(2.5 * gib) + 3 * (gib // 2) + budget["J_journal_bytes"] + 256 * 1024 * 1024
    assert budget["H_headroom_bytes"] == 10 * gib
    assert budget["required_free_bytes"] == working + 10 * gib
    assert 22 * gib < budget["required_free_bytes"] < 24 * gib
    with pytest.raises(ReleaseStoreError):
        disk_budget(source_bytes=-1, dictionary_bytes=0, sidecar_bytes=0)


def test_actual_candidate_size_rechecks_disk_headroom(env):
    rollback = env.legacy_release()
    calls = {"count": 0}

    def shrinking_disk(_path):
        calls["count"] += 1
        free = 1 << 40 if calls["count"] == 1 else 1
        return type("Usage", (), {"free": free})()

    with pytest.raises(PrepareError, match="insufficient disk space"):
        env.prepare(rollback, disk_usage=shrinking_disk)
    candidate = next(
        path for path in env.layout.releases.iterdir() if path.name != rollback
    )
    assert not (candidate / "READY").exists()
    assert calls["count"] >= 2


def _corrupt(database: Path) -> None:
    data = bytearray(database.read_bytes())
    for offset in range(4096 + 100, min(len(data), 4096 * 3), 7):
        data[offset] ^= 0xFF
    database.write_bytes(bytes(data))


def _orphan_form(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO forms(entry_id, form, normalized_form, tags_json) VALUES (99999, 'x', 'x', '[]')")
    connection.commit()
    connection.close()


def _drop_index(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("DROP INDEX forms_lookup")
    connection.commit()
    connection.close()


@pytest.mark.parametrize(
    ("stage", "action", "message"),
    [
        ("after_import", "kill", "simulated kill"),
        ("after_import", "corrupt", "integrity|cannot be validated|malformed"),
        ("after_import", "orphan", "foreign_key"),
        ("after_import", "drop_index", "forms_lookup"),
        ("after_candidate_validation", "touch", "changed while its Sidecar"),
        ("after_sidecar", "touch", "changed while its Sidecar"),
        ("after_quality", "kill", "simulated kill"),
        ("after_manifest", "kill", "simulated kill"),
    ],
)
def test_build_faults_never_publish_ready_and_never_touch_live_state(env, stage, action, message):
    rollback = env.legacy_release()
    legacy_identity = _legacy_identity(env)
    learning = env.learning_hashes()
    rollback_state = tree_state(release_dir(env, rollback))
    created = {}

    def fault(name):
        if name != stage:
            return
        release = next(path for path in env.layout.releases.iterdir() if path.name != rollback)
        created["release"] = release
        database = release / "dictionary.sqlite3"
        if action == "kill":
            raise RuntimeError("simulated kill")
        if action == "corrupt":
            _corrupt(database)
        elif action == "orphan":
            _orphan_form(database)
        elif action == "drop_index":
            _drop_index(database)
        elif action == "touch":
            os.utime(database, ns=(database.stat().st_atime_ns, database.stat().st_mtime_ns + 1_000_000))

    with pytest.raises(PrepareError, match=message):
        env.prepare(rollback, _fault=fault)
    release = created["release"]
    assert not (release / "READY").exists()
    failure = json.loads((release / "build-failure.json").read_text())
    assert failure["build_id"] == release.name
    assert _legacy_identity(env) == legacy_identity
    assert env.learning_hashes() == learning
    assert tree_state(release_dir(env, rollback)) == rollback_state
    assert not (env.storage / "dictionary_current").exists()


def test_sidecar_interruption_publishes_no_sidecar(env, monkeypatch):
    rollback = env.legacy_release()
    real_build = prepare_module.build_forms_index

    def interrupted(source, output):
        return real_build(source, output, _interrupt_after_rows=1)

    monkeypatch.setattr(prepare_module, "build_forms_index", interrupted)
    with pytest.raises(PrepareError, match="interruption"):
        env.prepare(rollback)
    release = next(path for path in env.layout.releases.iterdir() if path.name != rollback)
    assert not (release / "dictionary.forms.sqlite3").exists()
    assert not (release / "READY").exists()


def test_gold_failure_writes_report_but_no_ready(env, tmp_path):
    rollback = env.legacy_release()
    gold = json.loads(env.gold.read_text())
    gold["cases"][0]["expect"]["chinese_any"] = ["得到"]
    gold["cases"][0]["expect"]["chinese_forbidden"] = ["明白"]
    strict = tmp_path / "gold.json"
    strict.write_text(json.dumps(gold, ensure_ascii=False))
    with pytest.raises(PrepareError, match="gold case"):
        env.prepare(rollback, gold_set=strict)
    release = next(path for path in env.layout.releases.iterdir() if path.name != rollback)
    quality = json.loads((release / "quality-report.json").read_text())
    assert quality["gold_case_failures"] == 1
    assert quality["gold_cases"]["incorrect_associations"] == 1
    assert not (release / "READY").exists() and not (release / "manifest.json").exists()


def test_existing_release_directory_is_never_reused(env):
    rollback = env.legacy_release()
    plan = env.prepare(rollback, dry_run=True)
    (env.layout.releases / plan["build_id"]).mkdir()
    with pytest.raises(ReleaseStoreError, match="already exists"):
        env.prepare(rollback)


def test_renamed_release_is_rejected_after_sidecar_build(env):
    rollback = env.legacy_release()
    build_id = env.prepare(rollback)["build_id"]
    release = release_dir(env, build_id)
    renamed = release.with_name(build_id + "-renamed")
    # A published release is intentionally sealed read-only. To simulate an
    # out-of-band rename on macOS as well as Linux, first emulate a privileged
    # operator removing those permissions. Normal migration code never does this.
    make_writable(release)
    release.rename(renamed)
    with pytest.raises(ManifestValidationError):
        validate_published_release(renamed, env.layout.releases, env.sources)
    # Even with a corrected manifest ID the Sidecar identity is now stale.
    assert lookup_forms(renamed / "dictionary.sqlite3", renamed / "dictionary.forms.sqlite3", 1).status == "index_stale"


@pytest.mark.parametrize("damage", ["ready_missing", "ready_mismatch", "manifest_tampered", "ready_symlink"])
def test_published_release_requires_matching_ready(env, damage, tmp_path):
    rollback = env.legacy_release()
    build_id = env.prepare(rollback)["build_id"]
    release = release_dir(env, build_id)
    make_writable(release)
    if damage == "ready_missing":
        (release / "READY").unlink()
    elif damage == "ready_mismatch":
        (release / "READY").write_text("0" * 64)
    elif damage == "manifest_tampered":
        manifest = json.loads((release / "manifest.json").read_text())
        manifest["created_at"] = "2026-10-28T00:00:00Z"
        (release / "manifest.json").write_text(json.dumps(manifest))
    else:
        external = tmp_path / "READY"
        external.write_text((release / "READY").read_text())
        (release / "READY").unlink()
        (release / "READY").symlink_to(external)
    with pytest.raises(ManifestValidationError, match="READY"):
        validate_published_release(release, env.layout.releases, env.sources)


def test_ready_eligibility_requires_exact_sense_local_accounting(env):
    rollback = env.legacy_release()
    build_id = env.prepare(rollback)["build_id"]
    release = release_dir(env, build_id)
    make_writable(release)
    manifest = json.loads((release / "manifest.json").read_text())
    manifest["statistics"]["alignment_telemetry"]["sense_local"]["seen"] += 1
    (release / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ManifestValidationError, match="account for every translation"):
        validate_ready_eligibility(release, env.layout.releases, env.sources)


def test_symlinked_release_root_is_refused(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (storage / "dictionary_releases").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(ReleaseStoreError, match="symlink"):
        resolve_layout(storage)


def test_source_gzip_artifacts_are_complete_fixtures(env):
    for artifact in env.artifacts.values():
        assert gzip.decompress(artifact.read_bytes())


DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_draft_production_gold_set_is_well_formed_but_blocks_until_owner_approval(tmp_path):
    from services.dictionary.release_quality import QualityGateError, load_gold_set

    draft = DOCS / "DICTIONARY-GOLD-CASES-v1.draft.json"
    with pytest.raises(QualityGateError, match="approved"):
        load_gold_set(draft)
    approved = json.loads(draft.read_text(encoding="utf-8"))
    assert approved["scope"] == "production"
    assert {case["id"] for case in approved["cases"]} >= {
        "en-get-understand", "en-get-obtain", "en-got-become", "de-komme-kommen",
        "de-entwicklung", "en-bank-river", "en-bank-financial", "en-die-not-stopword",
        "de-compound-no-fabrication",
    }
    approved["approved"] = True
    copy = tmp_path / "gold.json"
    copy.write_text(json.dumps(approved, ensure_ascii=False), encoding="utf-8")
    assert load_gold_set(copy)["scope"] == "production"


def test_source_evidence_template_is_structural_but_placeholder_blocked(env):
    from services.dictionary.release_manifest import verify_source_artifacts
    from services.dictionary.release_prepare import load_source_evidence

    sources = load_source_evidence(DOCS / "DICTIONARY-SOURCE-EVIDENCE-v1.example.json")
    with pytest.raises(ManifestValidationError, match="placeholder"):
        verify_source_artifacts(sources, env.sources)
