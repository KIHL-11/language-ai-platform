"""Build one immutable, validated dictionary release candidate.

``prepare_release`` never stops or switches the backend, never opens the
installed dictionary for writing, and never downloads anything. It imports only
into a brand-new ``dictionary_releases/<build-id>`` directory and writes
``READY`` last, after the read-only READY-eligibility validator succeeds.
"""

import hashlib
import json
import platform
import resource
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from services.dictionary.forms_sidecar import (
    FORMS_INDEX_SCHEMA_VERSION,
    build_forms_index,
    lookup_forms,
)
from services.dictionary.import_telemetry import ImportTelemetry
from services.dictionary.provider import SCHEMA_VERSION, import_kaikki_sources
from services.dictionary.release_manifest import (
    ManifestValidationError,
    dictionary_statistics,
    file_sha256,
    validate_manifest_structure,
    validate_published_release,
    validate_ready_eligibility,
    validate_rollback_release,
    validate_source_records,
    verified_content_license,
    verify_source_artifacts,
)
from services.dictionary.release_quality import (
    QualityGateError,
    chinese_script_profile,
    common_word_coverage,
    evaluate_gold_cases,
    load_gold_set,
    measure_performance,
    structural_comparison,
    validate_candidate_database,
)
from services.dictionary.release_store import (
    DICTIONARY_NAME,
    GIB,
    SIDECAR_NAME,
    ReleaseLayout,
    ReleaseStoreError,
    canonical_json,
    create_release_directory,
    current_build_id,
    disk_budget,
    require_free_space,
    seal_release,
    write_new_file_atomic,
)

REPOSITORY_URL = "https://github.com/KIHL-11/language-ai-platform.git"
IMPORT_COMMAND = "backend/scripts/import_kaikki_dictionary.py"
QUALITY_REPORT_NAME = "quality-report.json"
BUILD_REPORT_NAME = "build-report.json"
FAILURE_NAME = "build-failure.json"
MANIFEST_NAME = "manifest.json"


class PrepareError(RuntimeError):
    """The candidate cannot be prepared; no READY marker was written."""


@dataclass(frozen=True, slots=True)
class RepositoryState:
    url: str
    git_sha: str
    worktree_clean: bool
    python_version: str


def repository_state(repository_root: Path) -> RepositoryState:
    """Read the importer commit and worktree cleanliness from Git (read-only)."""
    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    return RepositoryState(
        url=REPOSITORY_URL,
        git_sha=git("rev-parse", "HEAD"),
        worktree_clean=git("status", "--porcelain", "--untracked-files=normal") == "",
        python_version=platform.python_version(),
    )


def _utc(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def load_source_evidence(path: Path) -> list[dict]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise PrepareError("source evidence must be a regular file under 1 MiB")
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrepareError("source evidence is malformed") from exc
    if not isinstance(evidence, dict) or set(evidence) != {"evidence_version", "sources"}:
        raise PrepareError("source evidence must contain evidence_version and sources only")
    if evidence["evidence_version"] != 1:
        raise PrepareError("source evidence version is unsupported")
    by_language = validate_source_records(evidence["sources"])
    return [by_language["en"], by_language["de"]]


def _baseline_sources(release: Path) -> list[dict] | None:
    manifest_path = release / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8")).get("sources")
    except (OSError, json.JSONDecodeError):
        return None


def _snapshot_comparison(sources: list[dict], baseline: list[dict] | None) -> dict:
    if not baseline:
        return {
            "same_source_hashes_as_baseline": False,
            "attribution": "confounded by source snapshot: baseline raw snapshot is unavailable",
        }
    def hashes(values):
        return {
            value.get("entry_language"): value.get("snapshot_identity", {}).get("sha256")
            for value in values
            if isinstance(value, dict)
        }
    same = hashes(sources) == hashes(baseline)
    return {
        "same_source_hashes_as_baseline": same,
        "attribution": (
            "importer effect only: source snapshot hashes are unchanged"
            if same else "confounded by source snapshot"
        ),
    }


def _sample_entries(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        samples = {}
        for language in ("en", "de"):
            row = connection.execute(
                "SELECT forms.entry_id FROM forms JOIN entries ON entries.id = forms.entry_id "
                "WHERE entries.language = ? ORDER BY forms.id LIMIT 1",
                (language,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT id FROM entries WHERE language = ? ORDER BY id LIMIT 1", (language,)
                ).fetchone()
            if row is None:
                raise PrepareError(f"candidate has no {language} entries")
            samples[language] = int(row[0])
        return samples
    finally:
        connection.close()


def prepare_release(
    *,
    layout: ReleaseLayout,
    source_root: Path,
    source_evidence: Path,
    gold_set: Path,
    rollback_build_id: str,
    repository: RepositoryState,
    dry_run: bool = False,
    now: datetime | None = None,
    projected_dictionary_bytes: int = 0,
    projected_sidecar_bytes: int = 0,
    minimum_headroom_bytes: int = 10 * GIB,
    allow_fixture_gold: bool = False,
    disk_usage: Callable[[Path], object] = shutil.disk_usage,
    _fault: Callable[[str], None] | None = None,
) -> dict:
    """Prepare a candidate release. Returns a JSON-safe summary without absolute paths."""
    fault = _fault or (lambda stage: None)
    now = now or datetime.now(timezone.utc)

    # ---- read-only preflight -------------------------------------------------
    if not repository.worktree_clean:
        raise PrepareError("repository worktree is not clean; commit or stash before building")
    if len(repository.git_sha) != 40 or any(c not in "0123456789abcdef" for c in repository.git_sha):
        raise PrepareError("importer Git SHA must be a full 40-character commit")
    try:
        sources = load_source_evidence(source_evidence)
        content_license = verified_content_license(sources)
        artifacts = verify_source_artifacts(sources, source_root)
        gold = load_gold_set(gold_set, allow_fixture=allow_fixture_gold)
        rollback = validate_rollback_release(layout.releases, rollback_build_id)
    except (ManifestValidationError, QualityGateError) as exc:
        raise PrepareError(str(exc)) from exc
    try:
        current = current_build_id(layout)
    except ReleaseStoreError as exc:
        raise PrepareError(str(exc)) from exc
    if current is not None and current != rollback_build_id:
        raise PrepareError("rollback release must be the release dictionary_current points to")
    if current is None and not rollback_build_id.startswith("legacy-"):
        raise PrepareError("first migration must use the verified legacy-<sha> rollback release")

    rollback_database = rollback / DICTIONARY_NAME
    rollback_sidecar = rollback / SIDECAR_NAME
    budget = disk_budget(
        source_bytes=sum(source["snapshot_identity"]["size_bytes"] for source in sources),
        dictionary_bytes=max(rollback_database.stat().st_size, projected_dictionary_bytes),
        sidecar_bytes=max(rollback_sidecar.stat().st_size, projected_sidecar_bytes),
        minimum_headroom_bytes=minimum_headroom_bytes,
    )
    try:
        free_bytes = require_free_space(layout.releases, budget["required_free_bytes"], disk_usage=disk_usage)
    except ReleaseStoreError as exc:
        raise PrepareError(str(exc)) from exc
    identity_seed = ":".join([
        *(source["snapshot_identity"]["sha256"] for source in sources),
        repository.git_sha,
    ])
    build_id = f"{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{hashlib.sha256(identity_seed.encode()).hexdigest()[:12]}"
    plan = {
        "build_id": build_id,
        "rollback_build_id": rollback_build_id,
        "current_build_id": current,
        "importer_git_sha": repository.git_sha,
        "content_license": content_license,
        "disk_budget": budget,
        "free_bytes": free_bytes,
        "dry_run": dry_run,
    }
    if dry_run:
        return {**plan, "action": "would_prepare"}

    # ---- isolated build -------------------------------------------------------
    release = create_release_directory(layout, build_id)
    stage = "import"
    try:
        database = release / DICTIONARY_NAME
        telemetry = ImportTelemetry()
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        import_kaikki_sources(
            [
                (artifacts["en"], "en", sources[0]["acquisition_url"]),
                (artifacts["de"], "de", sources[1]["acquisition_url"]),
            ],
            database,
            content_license=content_license,
            telemetry=telemetry,
        )
        elapsed = time.perf_counter() - started
        completed_at = datetime.now(timezone.utc)
        peak_rss = _peak_rss_bytes()
        fault("after_import")

        stage = "candidate_validation"
        database_validation = validate_candidate_database(database)
        database_sha = file_sha256(database)
        database_stat = database.stat()
        fault("after_candidate_validation")

        stage = "sidecar"
        sidecar = release / SIDECAR_NAME
        build_forms_index(database, sidecar)
        lock = Path(f"{sidecar}.lock")
        if lock.is_file() and not lock.is_symlink():
            lock.unlink()
        fault("after_sidecar")
        after_stat = database.stat()
        if (
            file_sha256(database) != database_sha
            or (after_stat.st_mtime_ns, after_stat.st_size, after_stat.st_ino)
            != (database_stat.st_mtime_ns, database_stat.st_size, database_stat.st_ino)
        ):
            raise PrepareError("candidate dictionary changed while its Sidecar was built")
        sidecar_status = {
            language: lookup_forms(database, sidecar, entry_id).status
            for language, entry_id in _sample_entries(database).items()
        }
        if any(status != "available" for status in sidecar_status.values()):
            raise PrepareError("candidate forms Sidecar is not available for both languages")

        stage = "quality"
        gold_results = evaluate_gold_cases(database, sidecar, gold)
        performance = measure_performance(database, sidecar, gold)
        snapshot = _snapshot_comparison(sources, _baseline_sources(rollback))
        quality_report = {
            "quality_report_version": 1,
            "build_id": build_id,
            "rollback_build_id": rollback_build_id,
            "gold_set": {
                "scope": gold["scope"],
                "sha256": file_sha256(gold_set),
                "cases": len(gold["cases"]),
            },
            "gold_case_failures": gold_results["gold_case_failures"],
            "gold_cases": gold_results,
            "common_word_coverage": common_word_coverage(database, gold),
            "chinese_script_profile": chinese_script_profile(database),
            "structural_comparison": structural_comparison(rollback_database, database),
            "snapshot_comparison": snapshot,
            "performance": performance,
            "performance_environment": {
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "dictionary_bytes": database.stat().st_size,
                "sidecar_bytes": sidecar.stat().st_size,
                "filesystem_cache": "warm (built in this process)",
            },
        }
        quality_bytes = write_new_file_atomic(release, QUALITY_REPORT_NAME, canonical_json(quality_report))
        if gold_results["gold_case_failures"]:
            raise PrepareError(f"{gold_results['gold_case_failures']} gold case(s) failed")
        if not performance["passed"]:
            raise PrepareError("lookup performance gate failed")
        fault("after_quality")

        stage = "manifest"
        statistics = dictionary_statistics(database)
        statistics["alignment_telemetry"] = telemetry.manifest_telemetry()
        write_new_file_atomic(release, BUILD_REPORT_NAME, canonical_json({
            "build_report_version": 1,
            "build_id": build_id,
            "import_telemetry": telemetry.report(),
            "import_invoked_in_process": True,
            "elapsed_seconds": round(elapsed, 3),
            "peak_rss_bytes": peak_rss,
            "disk_budget": budget,
            "free_bytes_at_preflight": free_bytes,
            "database_validation": database_validation,
            "sidecar_status": sidecar_status,
        }))
        manifest = {
            "manifest_version": 1,
            "example_only": False,
            "status": "candidate_validated",
            "build_id": build_id,
            "created_at": _utc(now),
            "repository": {
                "url": repository.url,
                "importer_git_sha": repository.git_sha,
                "worktree_clean": True,
                "dictionary_schema_version": SCHEMA_VERSION,
                "forms_sidecar_schema_version": FORMS_INDEX_SCHEMA_VERSION,
                "python_version": repository.python_version,
            },
            "sources": sources,
            "import": {
                "command": IMPORT_COMMAND,
                "arguments": {
                    "database": DICTIONARY_NAME,
                    "content_license": content_license,
                    "english_input": sources[0]["artifact_name"],
                    "english_source_url": sources[0]["acquisition_url"],
                    "german_input": sources[1]["artifact_name"],
                    "german_source_url": sources[1]["acquisition_url"],
                },
                "started_at": _utc(started_at),
                "completed_at": _utc(completed_at),
                "elapsed_seconds": round(elapsed, 3),
                "peak_rss_bytes": peak_rss,
            },
            "statistics": statistics,
            "validation": {
                "integrity_check": database_validation["integrity_check"],
                "foreign_key_error_count": database_validation["foreign_key_error_count"],
                "required_query_plans": database_validation["required_query_plans"],
                "gold_case_failures": gold_results["gold_case_failures"],
                "forms_sidecar_status": "available",
                "quality_report": {
                    "path": QUALITY_REPORT_NAME,
                    "sha256": hashlib.sha256(quality_bytes).hexdigest(),
                    "size_bytes": len(quality_bytes),
                },
                "snapshot_comparison": snapshot,
            },
            "outputs": {
                "dictionary": {
                    "path": DICTIONARY_NAME,
                    "sha256": database_sha,
                    "size_bytes": database.stat().st_size,
                },
                "forms_sidecar": {
                    "path": SIDECAR_NAME,
                    "sha256": file_sha256(sidecar),
                    "size_bytes": sidecar.stat().st_size,
                    "source_identity_verified": True,
                },
            },
            "rollback": {
                "required": True,
                "previous_build_id": rollback_build_id,
                "validated": True,
            },
        }
        validate_manifest_structure(manifest)
        manifest_bytes = write_new_file_atomic(release, MANIFEST_NAME, canonical_json(manifest))
        fault("after_manifest")

        stage = "ready"
        validate_ready_eligibility(release, layout.releases, source_root)
        write_new_file_atomic(release, "READY", (hashlib.sha256(manifest_bytes).hexdigest() + "\n").encode())
        seal_release(release)
        validate_published_release(release, layout.releases, source_root)
    except (PrepareError, ManifestValidationError, QualityGateError, ReleaseStoreError,
            sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
        _record_failure(release, build_id, stage, exc)
        if isinstance(exc, PrepareError):
            raise
        raise PrepareError(f"{stage} failed: {exc}") from exc
    return {
        **plan,
        "action": "prepared",
        "gold_case_failures": 0,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "statistics": {
            key: statistics[key]["total"] for key in ("entries", "senses", "forms", "pronunciations")
        },
    }


def _record_failure(release: Path, build_id: str, stage: str, exc: BaseException) -> None:
    """Leave diagnostic evidence. Cutover refuses any release containing it."""
    try:
        write_new_file_atomic(release, FAILURE_NAME, canonical_json({
            "build_id": build_id,
            "stage": stage,
            "error_type": type(exc).__name__,
            "message": str(exc)[:2000],
        }))
    except (OSError, ReleaseStoreError):
        pass
