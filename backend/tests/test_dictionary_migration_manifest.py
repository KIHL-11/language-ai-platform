import gzip
import hashlib
import json
from pathlib import Path

import pytest

from services.dictionary.forms_sidecar import build_forms_index
from services.dictionary.provider import import_kaikki_sources
from services.dictionary.release_manifest import (
    ManifestValidationError,
    dictionary_statistics,
    file_sha256,
    validate_manifest_structure,
    validate_ready_eligibility,
)


FIXTURES = Path(__file__).parent / "fixtures"
SHA = "b2f9323d0affd857f4a75903901e0765c54bc0f1"


def _output(path: Path, *, sidecar=False):
    value = {
        "path": path.name,
        "sha256": file_sha256(path),
        "size_bytes": path.stat().st_size,
    }
    if sidecar:
        value["source_identity_verified"] = True
    return value


def _build_dictionary(release: Path):
    release.mkdir()
    database = release / "dictionary.sqlite3"
    sidecar = release / "dictionary.forms.sqlite3"
    import_kaikki_sources(
        [
            (FIXTURES / "kaikki_en_fixture.jsonl", "en", "https://kaikki.org/dictionary/fixture"),
            (FIXTURES / "kaikki_de_fixture.jsonl", "de", "https://kaikki.org/dewiktionary/fixture"),
        ],
        database,
        content_license="Synthetic fixture",
    )
    build_forms_index(database, sidecar)
    return database, sidecar


@pytest.fixture()
def prepared_release(tmp_path):
    releases = tmp_path / "releases"
    sources = tmp_path / "sources"
    releases.mkdir()
    sources.mkdir()
    source_records = []
    for language, name in (("en", "kaikki-en-fixture.jsonl.gz"), ("de", "kaikki-de-fixture.jsonl.gz")):
        artifact = sources / name
        artifact.write_bytes(gzip.compress(f"{language}-fixture-source".encode()))
        source_records.append({
            "edition": f"{language} fixture edition",
            "entry_language": language,
            "acquisition_url": f"https://kaikki.org/{language}/fixture",
            "resolved_url": f"https://kaikki.org/{language}/fixture",
            "rolling_url": True,
            "snapshot_identity": {"sha256": file_sha256(artifact), "size_bytes": artifact.stat().st_size},
            "dates": {
                "dump_date": None,
                "dump_date_status": "unverified",
                "extraction_date": None,
                "extraction_date_status": "unverified",
                "retrieved_at": "2026-10-27T10:00:00Z",
            },
            "http_evidence": {"etag": None, "last_modified": None, "content_length": artifact.stat().st_size},
            "license": {
                "label": "Synthetic fixture",
                "notice_url": f"https://kaikki.org/{language}/",
                "attribution": "Synthetic test fixture",
                "verified": True,
                "verified_at": "2026-10-27T10:00:00Z",
            },
            "artifact_name": name,
        })

    rollback_id = "legacy-rollback-001"
    rollback = releases / rollback_id
    rollback_database, rollback_sidecar = _build_dictionary(rollback)
    rollback_record = {
        "verification_version": 1,
        "build_id": rollback_id,
        "validated": True,
        "dictionary": _output(rollback_database),
        "forms_sidecar": _output(rollback_sidecar, sidecar=True),
    }
    rollback_bytes = json.dumps(rollback_record, sort_keys=True).encode()
    (rollback / "rollback-verification.json").write_bytes(rollback_bytes)
    (rollback / "READY").write_text(hashlib.sha256(rollback_bytes).hexdigest(), encoding="utf-8")

    build_id = "candidate-release-001"
    release = releases / build_id
    database, sidecar = _build_dictionary(release)
    quality = release / "quality-report.json"
    quality.write_text('{"gold_case_failures":0}', encoding="utf-8")
    statistics = dictionary_statistics(database)
    statistics["alignment_telemetry"] = {
        "instrumentation_status": "complete",
        "sense_local": {"seen": 0, "retained": 0, "deduplicated": 0, "truncated": 0},
        "entry_level": {
            "seen": 0,
            "accepted": {"unique_multi_token": 0, "reliable_single_token_head": 0, "unscoped_single_sense": 0, "total": 0},
            "rejected": {
                "ambiguous_or_tied": 0,
                "zero_evidence": 0,
                "generic_stopword_or_lemma_only": 0,
                "single_sense_mismatch": 0,
                "unsupported_morphology": 0,
                "over_limit": 0,
                "total": 0,
            },
        },
    }
    manifest = {
        "manifest_version": 1,
        "example_only": False,
        "status": "candidate_validated",
        "build_id": build_id,
        "created_at": "2026-10-27T12:00:00Z",
        "repository": {
            "url": "https://github.com/KIHL-11/language-ai-platform.git",
            "importer_git_sha": SHA,
            "worktree_clean": True,
            "dictionary_schema_version": 1,
            "forms_sidecar_schema_version": 1,
            "python_version": "3.12",
        },
        "sources": source_records,
        "import": {
            "command": "backend/scripts/import_kaikki_dictionary.py",
            "arguments": {
                "database": "dictionary.sqlite3",
                "content_license": "Synthetic fixture",
                "english_input": source_records[0]["artifact_name"],
                "english_source_url": source_records[0]["acquisition_url"],
                "german_input": source_records[1]["artifact_name"],
                "german_source_url": source_records[1]["acquisition_url"],
            },
            "started_at": "2026-10-27T10:00:00Z",
            "completed_at": "2026-10-27T11:00:00Z",
            "elapsed_seconds": 3600,
            "peak_rss_bytes": 1024,
        },
        "statistics": statistics,
        "validation": {
            "integrity_check": "ok",
            "foreign_key_error_count": 0,
            "required_query_plans": "passed",
            "gold_case_failures": 0,
            "forms_sidecar_status": "available",
            "quality_report": _output(quality),
            "snapshot_comparison": {"same_source_hashes_as_baseline": False, "attribution": "fixture"},
        },
        "outputs": {"dictionary": _output(database), "forms_sidecar": _output(sidecar, sidecar=True)},
        "rollback": {"required": True, "previous_build_id": rollback_id, "validated": True},
    }
    (release / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return release, releases, sources, manifest


def _write_manifest(release, manifest):
    (release / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_complete_candidate_is_ready_eligible(prepared_release):
    release, releases, sources, manifest = prepared_release
    assert validate_manifest_structure(manifest) is manifest
    assert validate_ready_eligibility(release, releases, sources)["build_id"] == release.name


def test_example_manifest_is_structural_but_not_ready_eligible(prepared_release):
    release, releases, sources, manifest = prepared_release
    manifest["example_only"] = True
    _write_manifest(release, manifest)
    validate_manifest_structure(manifest)
    with pytest.raises(ManifestValidationError, match="examples"):
        validate_ready_eligibility(release, releases, sources)


@pytest.mark.parametrize("damage", ["source_sha", "source_size", "output_sha", "output_size"])
def test_placeholder_hashes_and_zero_byte_records_cannot_publish(prepared_release, damage):
    release, releases, sources, manifest = prepared_release
    if damage == "source_sha":
        manifest["sources"][0]["snapshot_identity"]["sha256"] = "0" * 64
    elif damage == "source_size":
        manifest["sources"][0]["snapshot_identity"]["size_bytes"] = 0
    elif damage == "output_sha":
        manifest["outputs"]["dictionary"]["sha256"] = "0" * 64
    else:
        manifest["outputs"]["dictionary"]["size_bytes"] = 0
    _write_manifest(release, manifest)
    validate_manifest_structure(manifest)
    with pytest.raises(ManifestValidationError):
        validate_ready_eligibility(release, releases, sources)


def test_gold_case_failure_blocks_ready(prepared_release):
    release, releases, sources, manifest = prepared_release
    manifest["validation"]["gold_case_failures"] = 1
    _write_manifest(release, manifest)
    with pytest.raises(ManifestValidationError, match="gold case"):
        validate_ready_eligibility(release, releases, sources)


def test_quality_report_gold_failure_blocks_ready(prepared_release):
    release, releases, sources, manifest = prepared_release
    quality = release / "quality-report.json"
    quality.write_text('{"gold_case_failures":1}', encoding="utf-8")
    manifest["validation"]["quality_report"] = _output(quality)
    _write_manifest(release, manifest)
    with pytest.raises(ManifestValidationError, match="quality report contains gold case failures"):
        validate_ready_eligibility(release, releases, sources)


def test_quality_report_boolean_does_not_impersonate_zero(prepared_release):
    release, releases, sources, manifest = prepared_release
    quality = release / "quality-report.json"
    quality.write_text('{"gold_case_failures":false}', encoding="utf-8")
    manifest["validation"]["quality_report"] = _output(quality)
    _write_manifest(release, manifest)
    with pytest.raises(ManifestValidationError, match="quality report contains gold case failures"):
        validate_ready_eligibility(release, releases, sources)


def test_unverified_source_license_blocks_ready(prepared_release):
    release, releases, sources, manifest = prepared_release
    manifest["sources"][0]["license"]["verified"] = False
    _write_manifest(release, manifest)
    validate_manifest_structure(manifest)
    with pytest.raises(ManifestValidationError, match="license is not verified"):
        validate_ready_eligibility(release, releases, sources)


def test_mismatched_source_license_metadata_blocks_ready(prepared_release):
    release, releases, sources, manifest = prepared_release
    manifest["sources"][1]["license"]["label"] = "Different verified license"
    _write_manifest(release, manifest)
    with pytest.raises(ManifestValidationError, match="source licenses are incompatible"):
        validate_ready_eligibility(release, releases, sources)


def test_truncated_gzip_source_blocks_ready(prepared_release):
    release, releases, sources, manifest = prepared_release
    artifact = sources / manifest["sources"][0]["artifact_name"]
    artifact.write_bytes(artifact.read_bytes()[:-4])
    manifest["sources"][0]["snapshot_identity"] = {
        "sha256": file_sha256(artifact),
        "size_bytes": artifact.stat().st_size,
    }
    _write_manifest(release, manifest)
    with pytest.raises(ManifestValidationError, match="complete gzip"):
        validate_ready_eligibility(release, releases, sources)


def test_incomplete_statistics_fail_structure_validation(prepared_release):
    _, _, _, manifest = prepared_release
    del manifest["statistics"]["forms"]
    with pytest.raises(ManifestValidationError, match="statistics"):
        validate_manifest_structure(manifest)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["statistics"]["entries"].__setitem__("total", "two"),
        lambda value: value["statistics"]["chinese_coverage"]["entry_percent"].__setitem__("en", 101),
        lambda value: value["statistics"]["alignment_telemetry"]["sense_local"].__setitem__("retained", 1),
        lambda value: value["statistics"]["alignment_telemetry"]["entry_level"]["accepted"].__setitem__("total", 1),
    ],
)
def test_invalid_statistic_and_telemetry_values_fail_structure_validation(prepared_release, mutation):
    _, _, _, manifest = prepared_release
    mutation(manifest)
    with pytest.raises(ManifestValidationError):
        validate_manifest_structure(manifest)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["sources"][0].__setitem__("acquisition_url", "not-a-uri"),
        lambda value: value["sources"][0]["dates"].__setitem__("retrieved_at", "not-a-date"),
        lambda value: value["sources"][0].__setitem__("artifact_name", "../escape.gz"),
        lambda value: value["outputs"]["dictionary"].__setitem__("path", "../dictionary.sqlite3"),
    ],
)
def test_invalid_uri_date_and_relative_paths_fail_structure_validation(prepared_release, mutation):
    _, _, _, manifest = prepared_release
    mutation(manifest)
    with pytest.raises(ManifestValidationError):
        validate_manifest_structure(manifest)


def test_unverified_sidecar_cannot_publish(prepared_release):
    release, releases, sources, manifest = prepared_release
    manifest["outputs"]["forms_sidecar"]["source_identity_verified"] = False
    _write_manifest(release, manifest)
    validate_manifest_structure(manifest)
    with pytest.raises(ManifestValidationError, match="Sidecar identity"):
        validate_ready_eligibility(release, releases, sources)


def test_stale_sidecar_cannot_publish(prepared_release):
    release, releases, sources, manifest = prepared_release
    database = release / manifest["outputs"]["dictionary"]["path"]
    database.touch()
    with pytest.raises(ManifestValidationError, match="Sidecar source identity"):
        validate_ready_eligibility(release, releases, sources)


def test_sqlite_journal_state_cannot_publish(prepared_release):
    release, releases, sources, manifest = prepared_release
    journal = Path(f"{release / manifest['outputs']['dictionary']['path']}-wal")
    journal.write_bytes(b"uncommitted-state")
    with pytest.raises(ManifestValidationError, match="unexpected -wal state"):
        validate_ready_eligibility(release, releases, sources)


def test_symlinked_release_directory_cannot_publish(prepared_release):
    release, releases, sources, _ = prepared_release
    alias = releases / "candidate-release-alias"
    alias.symlink_to(release, target_is_directory=True)
    with pytest.raises(ManifestValidationError, match="escapes"):
        validate_ready_eligibility(alias, releases, sources)


def test_symlinked_rollback_ready_marker_cannot_publish(prepared_release, tmp_path):
    release, releases, sources, manifest = prepared_release
    rollback = releases / manifest["rollback"]["previous_build_id"]
    ready = rollback / "READY"
    external_ready = tmp_path / "external-ready"
    external_ready.write_text(ready.read_text(encoding="utf-8"), encoding="utf-8")
    ready.unlink()
    ready.symlink_to(external_ready)
    with pytest.raises(ManifestValidationError, match="rollback release is missing"):
        validate_ready_eligibility(release, releases, sources)


@pytest.mark.parametrize("damage", ["missing", "declared_unverified", "actual_unverified"])
def test_missing_or_unverified_rollback_blocks_cutover(prepared_release, damage):
    release, releases, sources, manifest = prepared_release
    if damage == "missing":
        manifest["rollback"]["previous_build_id"] = "missing-rollback-001"
    elif damage == "declared_unverified":
        manifest["rollback"]["validated"] = False
    else:
        rollback = releases / manifest["rollback"]["previous_build_id"]
        verification_path = rollback / "rollback-verification.json"
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        verification["validated"] = False
        verification_bytes = json.dumps(verification, sort_keys=True).encode()
        verification_path.write_bytes(verification_bytes)
        (rollback / "READY").write_text(
            hashlib.sha256(verification_bytes).hexdigest(),
            encoding="utf-8",
        )
    _write_manifest(release, manifest)
    validate_manifest_structure(manifest)
    with pytest.raises(ManifestValidationError, match="rollback"):
        validate_ready_eligibility(release, releases, sources)
