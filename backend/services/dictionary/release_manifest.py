"""Read-only validation for dictionary release manifests and READY eligibility."""

import hashlib
import gzip
import json
import math
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from services.dictionary.forms_sidecar import FORMS_INDEX_SCHEMA_VERSION, lookup_forms
from services.dictionary.import_telemetry import ENTRY_REJECTED as _REJECTION_KEYS
from services.dictionary.provider import SCHEMA_VERSION


_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_LANGUAGES = ("en", "de")
_COUNT_KEYS = (*_LANGUAGES, "total")


class ManifestValidationError(ValueError):
    """A manifest is malformed or not eligible for READY publication."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _object(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{field} must be an object")
    return value


def _exact_keys(value: dict, expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ManifestValidationError(f"{field} has missing or unknown fields")


def _integer(value, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ManifestValidationError(f"{field} must be an integer >= {minimum}")
    return value


def _number(value, field: str, *, minimum: float = 0, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ManifestValidationError(f"{field} must be a finite number")
    number = float(value)
    if number < minimum or (maximum is not None and number > maximum):
        raise ManifestValidationError(f"{field} is outside its allowed range")
    return number


def _timestamp(value, field: str) -> None:
    if not isinstance(value, str):
        raise ManifestValidationError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManifestValidationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ManifestValidationError(f"{field} must include a timezone")


def _date_or_none(value, status, field: str) -> None:
    if status not in {"verified", "unverified"}:
        raise ManifestValidationError(f"{field}_status is invalid")
    if value is None:
        if status == "verified":
            raise ManifestValidationError(f"verified {field} cannot be null")
        return
    if not isinstance(value, str):
        raise ManifestValidationError(f"{field} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ManifestValidationError(f"{field} must be an ISO date") from exc


def _https_uri(value, field: str) -> None:
    if not isinstance(value, str):
        raise ManifestValidationError(f"{field} must be an HTTPS URI")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ManifestValidationError(f"{field} must be a non-credential HTTPS URI")


def _safe_name(value, field: str, *, exact: str | None = None) -> str:
    if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value) or value in {".", ".."}:
        raise ManifestValidationError(f"{field} must be a safe filename")
    if exact is not None and value != exact:
        raise ManifestValidationError(f"{field} must be {exact}")
    return value


def _hash(value, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ManifestValidationError(f"{field} has an invalid hash")
    return value


def _counts(value, field: str) -> dict[str, int]:
    record = _object(value, field)
    _exact_keys(record, set(_COUNT_KEYS), field)
    output = {key: _integer(record[key], f"{field}.{key}") for key in _COUNT_KEYS}
    if output["total"] != output["en"] + output["de"]:
        raise ManifestValidationError(f"{field}.total is inconsistent")
    return output


def _rates(value, field: str) -> dict[str, float]:
    record = _object(value, field)
    _exact_keys(record, set(_COUNT_KEYS), field)
    return {
        key: _number(record[key], f"{field}.{key}", maximum=100)
        for key in _COUNT_KEYS
    }


def validate_source_records(sources: object) -> dict[str, dict]:
    """Structure-check the English and German source records; return them by language."""
    if not isinstance(sources, list) or len(sources) != 2:
        raise ManifestValidationError("sources must contain English and German")
    languages = []
    source_by_language = {}
    artifact_names = set()
    for index, source_value in enumerate(sources):
        field = f"sources[{index}]"
        source = _object(source_value, field)
        _exact_keys(source, {"edition", "entry_language", "acquisition_url", "resolved_url", "rolling_url", "snapshot_identity", "dates", "http_evidence", "license", "artifact_name"}, field)
        language = source["entry_language"]
        if language not in _LANGUAGES:
            raise ManifestValidationError(f"{field}.entry_language is invalid")
        languages.append(language)
        source_by_language[language] = source
        if not isinstance(source["edition"], str) or not source["edition"]:
            raise ManifestValidationError(f"{field}.edition is required")
        _https_uri(source["acquisition_url"], f"{field}.acquisition_url")
        _https_uri(source["resolved_url"], f"{field}.resolved_url")
        if type(source["rolling_url"]) is not bool:
            raise ManifestValidationError(f"{field}.rolling_url must be boolean")
        identity = _object(source["snapshot_identity"], f"{field}.snapshot_identity")
        _exact_keys(identity, {"sha256", "size_bytes"}, f"{field}.snapshot_identity")
        _hash(identity["sha256"], f"{field}.snapshot_identity.sha256", _SHA256)
        _integer(identity["size_bytes"], f"{field}.snapshot_identity.size_bytes")
        dates = _object(source["dates"], f"{field}.dates")
        _exact_keys(dates, {"dump_date", "dump_date_status", "extraction_date", "extraction_date_status", "retrieved_at"}, f"{field}.dates")
        _date_or_none(dates["dump_date"], dates["dump_date_status"], f"{field}.dates.dump_date")
        _date_or_none(dates["extraction_date"], dates["extraction_date_status"], f"{field}.dates.extraction_date")
        _timestamp(dates["retrieved_at"], f"{field}.dates.retrieved_at")
        http = _object(source["http_evidence"], f"{field}.http_evidence")
        _exact_keys(http, {"etag", "last_modified", "content_length"}, f"{field}.http_evidence")
        if http["etag"] is not None and not isinstance(http["etag"], str):
            raise ManifestValidationError(f"{field}.http_evidence.etag is invalid")
        if http["last_modified"] is not None and not isinstance(http["last_modified"], str):
            raise ManifestValidationError(f"{field}.http_evidence.last_modified is invalid")
        if http["content_length"] is not None:
            _integer(http["content_length"], f"{field}.http_evidence.content_length")
        license_value = _object(source["license"], f"{field}.license")
        _exact_keys(license_value, {"label", "notice_url", "attribution", "verified", "verified_at"}, f"{field}.license")
        for key in ("label", "attribution"):
            if not isinstance(license_value[key], str) or not license_value[key]:
                raise ManifestValidationError(f"{field}.license.{key} is required")
        _https_uri(license_value["notice_url"], f"{field}.license.notice_url")
        if type(license_value["verified"]) is not bool:
            raise ManifestValidationError(f"{field}.license.verified must be boolean")
        if license_value["verified_at"] is None:
            if license_value["verified"]:
                raise ManifestValidationError(f"{field}.license.verified_at is required")
        else:
            _timestamp(license_value["verified_at"], f"{field}.license.verified_at")
        artifact_name = _safe_name(source["artifact_name"], f"{field}.artifact_name")
        if not artifact_name.endswith(".jsonl.gz"):
            raise ManifestValidationError(f"{field}.artifact_name must end in .jsonl.gz")
        artifact_names.add(artifact_name)
    if set(languages) != set(_LANGUAGES):
        raise ManifestValidationError("sources must contain one en and one de artifact")
    if len(artifact_names) != len(_LANGUAGES):
        raise ManifestValidationError("source artifact names must be distinct")
    return source_by_language


def validate_manifest_structure(payload: object) -> dict:
    """Validate exchange structure; examples are intentionally allowed here."""
    manifest = _object(payload, "manifest")
    required = {
        "manifest_version", "example_only", "status", "build_id", "created_at",
        "repository", "sources", "import", "statistics", "validation", "outputs",
        "rollback",
    }
    _exact_keys(manifest, required, "manifest")
    if manifest["manifest_version"] != 1 or type(manifest["example_only"]) is not bool:
        raise ManifestValidationError("manifest version/example flag is invalid")
    if manifest["status"] not in {"building", "candidate_validated", "failed"}:
        raise ManifestValidationError("manifest status is invalid")
    _safe_name(manifest["build_id"], "build_id")
    _timestamp(manifest["created_at"], "created_at")

    repository = _object(manifest["repository"], "repository")
    _exact_keys(repository, {"url", "importer_git_sha", "worktree_clean", "dictionary_schema_version", "forms_sidecar_schema_version", "python_version"}, "repository")
    _https_uri(repository["url"], "repository.url")
    _hash(repository["importer_git_sha"], "repository.importer_git_sha", _SHA1)
    if repository["worktree_clean"] is not True:
        raise ManifestValidationError("repository.worktree_clean must be true")
    _integer(repository["dictionary_schema_version"], "repository.dictionary_schema_version", minimum=1)
    _integer(repository["forms_sidecar_schema_version"], "repository.forms_sidecar_schema_version", minimum=1)
    if not isinstance(repository["python_version"], str) or not repository["python_version"]:
        raise ManifestValidationError("repository.python_version is required")

    source_by_language = validate_source_records(manifest["sources"])

    import_value = _object(manifest["import"], "import")
    _exact_keys(import_value, {"command", "arguments", "started_at", "completed_at", "elapsed_seconds", "peak_rss_bytes"}, "import")
    _safe_name(Path(str(import_value["command"])).name, "import.command")
    if not isinstance(import_value["command"], str) or import_value["command"] != "backend/scripts/import_kaikki_dictionary.py":
        raise ManifestValidationError("import.command is invalid")
    arguments = _object(import_value["arguments"], "import.arguments")
    _exact_keys(
        arguments,
        {
            "database",
            "content_license",
            "english_input",
            "english_source_url",
            "german_input",
            "german_source_url",
        },
        "import.arguments",
    )
    if arguments["database"] != "dictionary.sqlite3":
        raise ManifestValidationError("import.arguments.database is invalid")
    if not isinstance(arguments["content_license"], str) or not arguments["content_license"]:
        raise ManifestValidationError("import.arguments.content_license is required")
    for language, prefix in (("en", "english"), ("de", "german")):
        source = source_by_language[language]
        if arguments[f"{prefix}_input"] != source["artifact_name"]:
            raise ManifestValidationError(f"import.arguments.{prefix}_input does not match its source")
        if arguments[f"{prefix}_source_url"] != source["acquisition_url"]:
            raise ManifestValidationError(f"import.arguments.{prefix}_source_url does not match its source")
    _timestamp(import_value["started_at"], "import.started_at")
    _timestamp(import_value["completed_at"], "import.completed_at")
    _number(import_value["elapsed_seconds"], "import.elapsed_seconds")
    if import_value["peak_rss_bytes"] is not None:
        _integer(import_value["peak_rss_bytes"], "import.peak_rss_bytes")

    statistics = _object(manifest["statistics"], "statistics")
    _exact_keys(statistics, {"entries", "senses", "forms", "pronunciations", "chinese_coverage", "alignment_telemetry"}, "statistics")
    for key in ("entries", "senses", "forms", "pronunciations"):
        _counts(statistics[key], f"statistics.{key}")
    coverage = _object(statistics["chinese_coverage"], "statistics.chinese_coverage")
    _exact_keys(coverage, {"entries_with_chinese", "entry_percent", "senses_with_chinese", "sense_percent"}, "statistics.chinese_coverage")
    _counts(coverage["entries_with_chinese"], "statistics.chinese_coverage.entries_with_chinese")
    _rates(coverage["entry_percent"], "statistics.chinese_coverage.entry_percent")
    _counts(coverage["senses_with_chinese"], "statistics.chinese_coverage.senses_with_chinese")
    _rates(coverage["sense_percent"], "statistics.chinese_coverage.sense_percent")
    for count_key, base_key in (
        ("entries_with_chinese", "entries"),
        ("senses_with_chinese", "senses"),
    ):
        for language in _COUNT_KEYS:
            if coverage[count_key][language] > statistics[base_key][language]:
                raise ManifestValidationError(f"statistics.chinese_coverage.{count_key} exceeds {base_key}")
    telemetry = _object(statistics["alignment_telemetry"], "statistics.alignment_telemetry")
    _exact_keys(telemetry, {"instrumentation_status", "sense_local", "entry_level"}, "statistics.alignment_telemetry")
    if telemetry["instrumentation_status"] not in {"example", "complete"}:
        raise ManifestValidationError("alignment telemetry status is invalid")
    sense_local = _object(telemetry["sense_local"], "statistics.alignment_telemetry.sense_local")
    _exact_keys(sense_local, {"seen", "retained", "deduplicated", "truncated"}, "statistics.alignment_telemetry.sense_local")
    for key, value in sense_local.items():
        _integer(value, f"statistics.alignment_telemetry.sense_local.{key}")
    if sum(sense_local[key] for key in ("retained", "deduplicated", "truncated")) > sense_local["seen"]:
        raise ManifestValidationError("sense-local telemetry exceeds translations seen")
    entry_level = _object(telemetry["entry_level"], "statistics.alignment_telemetry.entry_level")
    _exact_keys(entry_level, {"seen", "accepted", "rejected"}, "statistics.alignment_telemetry.entry_level")
    _integer(entry_level["seen"], "statistics.alignment_telemetry.entry_level.seen")
    accepted = _object(entry_level["accepted"], "statistics.alignment_telemetry.entry_level.accepted")
    _exact_keys(accepted, {"unique_multi_token", "reliable_single_token_head", "unscoped_single_sense", "total"}, "statistics.alignment_telemetry.entry_level.accepted")
    accepted_values = {key: _integer(value, f"statistics.alignment_telemetry.entry_level.accepted.{key}") for key, value in accepted.items()}
    if accepted_values["total"] != sum(accepted_values[key] for key in accepted_values if key != "total"):
        raise ManifestValidationError("accepted telemetry total is inconsistent")
    rejected = _object(entry_level["rejected"], "statistics.alignment_telemetry.entry_level.rejected")
    _exact_keys(rejected, {*_REJECTION_KEYS, "total"}, "statistics.alignment_telemetry.entry_level.rejected")
    rejected_values = {key: _integer(value, f"statistics.alignment_telemetry.entry_level.rejected.{key}") for key, value in rejected.items()}
    if rejected_values["total"] != sum(rejected_values[key] for key in _REJECTION_KEYS):
        raise ManifestValidationError("rejected telemetry total is inconsistent")
    if entry_level["seen"] != accepted_values["total"] + rejected_values["total"]:
        raise ManifestValidationError("entry-level telemetry does not reconcile")

    validation = _object(manifest["validation"], "validation")
    _exact_keys(validation, {"integrity_check", "foreign_key_error_count", "required_query_plans", "gold_case_failures", "forms_sidecar_status", "quality_report", "snapshot_comparison"}, "validation")
    if validation["integrity_check"] != "ok" or validation["required_query_plans"] != "passed" or validation["forms_sidecar_status"] != "available":
        raise ManifestValidationError("validation gates are incomplete")
    _integer(validation["foreign_key_error_count"], "validation.foreign_key_error_count")
    _integer(validation["gold_case_failures"], "validation.gold_case_failures")
    _output_record(validation["quality_report"], "validation.quality_report", exact_name="quality-report.json")
    snapshot = _object(validation["snapshot_comparison"], "validation.snapshot_comparison")
    _exact_keys(snapshot, {"same_source_hashes_as_baseline", "attribution"}, "validation.snapshot_comparison")
    if type(snapshot["same_source_hashes_as_baseline"]) is not bool or not isinstance(snapshot["attribution"], str) or not snapshot["attribution"]:
        raise ManifestValidationError("snapshot comparison is invalid")

    outputs = _object(manifest["outputs"], "outputs")
    _exact_keys(outputs, {"dictionary", "forms_sidecar"}, "outputs")
    _output_record(outputs["dictionary"], "outputs.dictionary", exact_name="dictionary.sqlite3")
    sidecar = _output_record(outputs["forms_sidecar"], "outputs.forms_sidecar", exact_name="dictionary.forms.sqlite3", sidecar=True)
    if type(sidecar["source_identity_verified"]) is not bool:
        raise ManifestValidationError("forms Sidecar identity flag must be boolean")

    rollback = _object(manifest["rollback"], "rollback")
    _exact_keys(rollback, {"required", "previous_build_id", "validated"}, "rollback")
    if rollback["required"] is not True or type(rollback["validated"]) is not bool:
        raise ManifestValidationError("rollback declaration is invalid")
    _safe_name(rollback["previous_build_id"], "rollback.previous_build_id")
    return manifest


def _output_record(value, field: str, *, exact_name: str, sidecar: bool = False) -> dict:
    record = _object(value, field)
    keys = {"path", "sha256", "size_bytes"} | ({"source_identity_verified"} if sidecar else set())
    _exact_keys(record, keys, field)
    _safe_name(record["path"], f"{field}.path", exact=exact_name)
    _hash(record["sha256"], f"{field}.sha256", _SHA256)
    _integer(record["size_bytes"], f"{field}.size_bytes")
    return record


def dictionary_statistics(database: Path) -> dict:
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        def grouped(table: str, join: str = "") -> dict[str, int]:
            rows = connection.execute(
                f"SELECT entries.language, count(*) count FROM {table} {join} "
                "GROUP BY entries.language"
            ).fetchall()
            result = {language: 0 for language in _LANGUAGES}
            result.update({str(row["language"]): int(row["count"]) for row in rows})
            result["total"] = result["en"] + result["de"]
            return result

        entries = grouped("entries")
        senses = grouped("senses", "JOIN entries ON entries.id = senses.entry_id")
        forms = grouped("forms", "JOIN entries ON entries.id = forms.entry_id")
        pronunciations = grouped("pronunciations", "JOIN entries ON entries.id = pronunciations.entry_id")
        entry_ids = {language: set() for language in _LANGUAGES}
        sense_counts = {language: 0 for language in _LANGUAGES}
        for row in connection.execute(
            "SELECT entries.language, senses.entry_id, senses.translations_json "
            "FROM senses JOIN entries ON entries.id = senses.entry_id"
        ):
            translations = json.loads(row["translations_json"])
            if translations.get("zh-CN"):
                language = str(row["language"])
                entry_ids[language].add(int(row["entry_id"]))
                sense_counts[language] += 1
        entry_chinese = {language: len(entry_ids[language]) for language in _LANGUAGES}
        sense_chinese = {language: sense_counts[language] for language in _LANGUAGES}
        entry_chinese["total"] = sum(entry_chinese.values())
        sense_chinese["total"] = sum(sense_chinese.values())

        def percentages(numerators, denominators):
            return {
                key: round(100 * numerators[key] / denominators[key], 6)
                if denominators[key] else 0.0
                for key in _COUNT_KEYS
            }

        return {
            "entries": entries,
            "senses": senses,
            "forms": forms,
            "pronunciations": pronunciations,
            "chinese_coverage": {
                "entries_with_chinese": entry_chinese,
                "entry_percent": percentages(entry_chinese, entries),
                "senses_with_chinese": sense_chinese,
                "sense_percent": percentages(sense_chinese, senses),
            },
        }
    finally:
        connection.close()


def _contained_child(root: Path, name: str, field: str) -> Path:
    _safe_name(name, field)
    root = root.resolve(strict=True)
    child = root / name
    if child.is_symlink() or not child.is_dir() or child.resolve().parent != root:
        raise ManifestValidationError(f"{field} is not a direct release directory")
    return child


def _verified_file(root: Path, record: dict, field: str) -> Path:
    path = root / record["path"]
    if path.is_symlink() or not path.is_file() or path.resolve().parent != root.resolve():
        raise ManifestValidationError(f"{field} escapes its approved directory")
    size = path.stat().st_size
    if size <= 0 or record["size_bytes"] <= 0 or size != record["size_bytes"]:
        raise ManifestValidationError(f"{field} size is invalid")
    expected = record["sha256"]
    if expected == "0" * 64 or file_sha256(path) != expected:
        raise ManifestValidationError(f"{field} SHA-256 is invalid")
    return path


def _verify_gzip_complete(path: Path, field: str) -> None:
    try:
        with gzip.open(path, "rb") as source:
            for _ in iter(lambda: source.read(1024 * 1024), b""):
                pass
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise ManifestValidationError(f"{field} is not a complete gzip artifact") from exc


def _sqlite_health(database: Path, sidecar: Path) -> None:
    for base in (database, sidecar):
        for suffix in ("-journal", "-wal", "-shm"):
            if Path(f"{base}{suffix}").exists():
                raise ManifestValidationError(f"published SQLite artifact has unexpected {suffix} state")
    try:
        connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only = ON")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ManifestValidationError("dictionary integrity_check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ManifestValidationError("dictionary foreign_key_check failed")
            version = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(SCHEMA_VERSION):
                raise ManifestValidationError("dictionary schema version is unsupported")
        finally:
            connection.close()
        sidecar_connection = sqlite3.connect(f"file:{sidecar.resolve().as_posix()}?mode=ro", uri=True)
        try:
            if sidecar_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ManifestValidationError("forms Sidecar integrity_check failed")
        finally:
            sidecar_connection.close()
    except sqlite3.Error as exc:
        raise ManifestValidationError("dictionary or forms Sidecar is not readable SQLite") from exc
    if lookup_forms(database, sidecar, 0).status != "available":
        raise ManifestValidationError("forms Sidecar source identity is invalid")


def _direct_regular_file(release: Path, name: str) -> Path | None:
    path = release / name
    if path.is_symlink():
        raise ManifestValidationError(f"{name} must not be a symlink")
    if not path.exists():
        return None
    if not path.is_file() or path.resolve().parent != release.resolve():
        raise ManifestValidationError(f"{name} escapes its release directory")
    return path


def _ready_matches(release: Path, raw: bytes, field: str) -> None:
    ready_path = release / "READY"
    if (
        ready_path.is_symlink()
        or not ready_path.is_file()
        or ready_path.resolve().parent != release.resolve()
    ):
        raise ManifestValidationError(f"{field} is missing verification or READY")
    if ready_path.stat().st_size > 128:
        raise ManifestValidationError(f"{field} READY marker is unreasonably large")
    if ready_path.read_text(encoding="utf-8").strip() != hashlib.sha256(raw).hexdigest():
        raise ManifestValidationError(f"{field} READY does not match its verification record")


def _validate_published_candidate_as_rollback(release: Path, build_id: str) -> None:
    manifest_path = _direct_regular_file(release, "manifest.json")
    if manifest_path is None or manifest_path.stat().st_size > 1024 * 1024:
        raise ManifestValidationError("rollback release is missing verification or READY")
    raw = manifest_path.read_bytes()
    _ready_matches(release, raw, "rollback release")
    try:
        manifest = validate_manifest_structure(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise ManifestValidationError("rollback manifest is malformed") from exc
    if (
        manifest["build_id"] != build_id
        or manifest["example_only"] is not False
        or manifest["status"] != "candidate_validated"
        or manifest["outputs"]["forms_sidecar"]["source_identity_verified"] is not True
    ):
        raise ManifestValidationError("rollback release is not validated")
    database = _verified_file(release, manifest["outputs"]["dictionary"], "rollback.dictionary")
    sidecar = _verified_file(release, manifest["outputs"]["forms_sidecar"], "rollback.forms_sidecar")
    _sqlite_health(database, sidecar)


def validate_rollback_release(releases_root: Path, build_id: str) -> Path:
    """Read-only proof that a release can be switched back to and served.

    A rollback is either a legacy conversion with ``rollback-verification.json``
    or a previously published candidate whose ``READY`` matches its manifest.
    """
    release = _contained_child(releases_root, build_id, "rollback.previous_build_id")
    verification_path = _direct_regular_file(release, "rollback-verification.json")
    if verification_path is None:
        _validate_published_candidate_as_rollback(release, build_id)
        return release
    if verification_path.stat().st_size > 1024 * 1024:
        raise ManifestValidationError("rollback verification metadata is unreasonably large")
    raw = verification_path.read_bytes()
    _ready_matches(release, raw, "rollback release")
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestValidationError("rollback verification is malformed") from exc
    record = _object(record, "rollback verification")
    _exact_keys(record, {"verification_version", "build_id", "validated", "dictionary", "forms_sidecar"}, "rollback verification")
    if record["verification_version"] != 1 or record["build_id"] != build_id or record["validated"] is not True:
        raise ManifestValidationError("rollback release is not validated")
    dictionary_record = _output_record(record["dictionary"], "rollback.dictionary", exact_name="dictionary.sqlite3")
    sidecar_record = _output_record(record["forms_sidecar"], "rollback.forms_sidecar", exact_name="dictionary.forms.sqlite3", sidecar=True)
    if sidecar_record["source_identity_verified"] is not True:
        raise ManifestValidationError("rollback forms Sidecar is not verified")
    database = _verified_file(release, dictionary_record, "rollback.dictionary")
    sidecar = _verified_file(release, sidecar_record, "rollback.forms_sidecar")
    _sqlite_health(database, sidecar)
    return release


def verified_content_license(sources: list[dict]) -> str:
    """The single verified license label the v1 importer can record."""
    labels = {source["license"]["label"] for source in sources}
    if len(labels) != 1:
        raise ManifestValidationError(
            "source licenses are incompatible with the single-license importer; "
            "an Owner schema/importer decision is required"
        )
    return labels.pop()


def verify_source_artifacts(sources: list[dict], source_root: Path) -> dict[str, Path]:
    """Read-only: verify license, placeholder, size, SHA-256 and gzip trailer per source."""
    source_root = source_root.resolve(strict=True)
    artifacts = {}
    for source in sources:
        identity = source["snapshot_identity"]
        if identity["size_bytes"] <= 0 or identity["sha256"] == "0" * 64:
            raise ManifestValidationError("source artifact identity is a placeholder")
        if source["license"]["verified"] is not True:
            raise ManifestValidationError("source license is not verified")
        artifact = _verified_file(
            source_root,
            {"path": source["artifact_name"], **identity},
            f"source artifact {source['entry_language']}",
        )
        _verify_gzip_complete(artifact, f"source artifact {source['entry_language']}")
        artifacts[source["entry_language"]] = artifact
    return artifacts


def validate_ready_eligibility(release_dir: Path, releases_root: Path, source_root: Path) -> dict:
    """Validate a candidate without writing READY or changing any release."""
    releases_root = releases_root.resolve(strict=True)
    source_root = source_root.resolve(strict=True)
    if release_dir.is_symlink() or release_dir.resolve().parent != releases_root:
        raise ManifestValidationError("release directory escapes the approved root")
    manifest_path = release_dir / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ManifestValidationError("manifest.json is missing")
    if manifest_path.stat().st_size > 1024 * 1024:
        raise ManifestValidationError("manifest.json exceeds the 1 MiB limit")
    try:
        manifest = validate_manifest_structure(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as exc:
        raise ManifestValidationError("manifest.json cannot be read") from exc
    if manifest["build_id"] != release_dir.name:
        raise ManifestValidationError("manifest build_id does not match release directory")
    if manifest["example_only"] is not False or manifest["status"] != "candidate_validated":
        raise ManifestValidationError("examples and non-validated manifests cannot publish READY")
    if manifest["validation"]["gold_case_failures"] != 0:
        raise ManifestValidationError("gold case failures block READY publication")
    if manifest["outputs"]["forms_sidecar"]["source_identity_verified"] is not True:
        raise ManifestValidationError("forms Sidecar identity is not verified")
    if manifest["repository"]["dictionary_schema_version"] != SCHEMA_VERSION:
        raise ManifestValidationError("dictionary schema version does not match this importer")
    if manifest["repository"]["forms_sidecar_schema_version"] != FORMS_INDEX_SCHEMA_VERSION:
        raise ManifestValidationError("forms Sidecar schema version does not match this builder")
    if manifest["repository"]["importer_git_sha"] == "0" * 40:
        raise ManifestValidationError("importer Git SHA is a placeholder")
    if manifest["validation"]["foreign_key_error_count"] != 0:
        raise ManifestValidationError("foreign key failures block READY publication")
    telemetry = manifest["statistics"]["alignment_telemetry"]
    if telemetry["instrumentation_status"] != "complete":
        raise ManifestValidationError("complete alignment telemetry is required")
    sense_local = telemetry["sense_local"]
    if sense_local["seen"] != sense_local["retained"] + sense_local["deduplicated"] + sense_local["truncated"]:
        raise ManifestValidationError("sense-local telemetry does not account for every translation")
    if any(
        manifest["statistics"][group][language] <= 0
        for group in ("entries", "senses")
        for language in _LANGUAGES
    ):
        raise ManifestValidationError("candidate dictionary statistics are empty")
    license_labels = {source["license"]["label"] for source in manifest["sources"]}
    if len(license_labels) != 1 or manifest["import"]["arguments"]["content_license"] not in license_labels:
        raise ManifestValidationError("source licenses are incompatible with the importer metadata")
    verify_source_artifacts(manifest["sources"], source_root)
    database = _verified_file(release_dir, manifest["outputs"]["dictionary"], "outputs.dictionary")
    sidecar = _verified_file(release_dir, manifest["outputs"]["forms_sidecar"], "outputs.forms_sidecar")
    quality = _verified_file(release_dir, manifest["validation"]["quality_report"], "validation.quality_report")
    if quality.stat().st_size > 16 * 1024 * 1024:
        raise ManifestValidationError("quality report exceeds the 16 MiB limit")
    try:
        quality_payload = json.loads(quality.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ManifestValidationError("quality report is malformed") from exc
    quality_failures = quality_payload.get("gold_case_failures") if isinstance(quality_payload, dict) else None
    if type(quality_failures) is not int or quality_failures != 0:
        raise ManifestValidationError("quality report contains gold case failures")
    _sqlite_health(database, sidecar)
    try:
        actual_statistics = dictionary_statistics(database)
    except (sqlite3.Error, json.JSONDecodeError, TypeError) as exc:
        raise ManifestValidationError("dictionary statistics cannot be read") from exc
    for key in ("entries", "senses", "forms", "pronunciations", "chinese_coverage"):
        if manifest["statistics"][key] != actual_statistics[key]:
            raise ManifestValidationError(f"statistics.{key} does not match the database")
    if manifest["rollback"]["validated"] is not True:
        raise ManifestValidationError("rollback release is not declared validated")
    if manifest["rollback"]["previous_build_id"] == manifest["build_id"]:
        raise ManifestValidationError("candidate cannot be its own rollback")
    validate_rollback_release(releases_root, manifest["rollback"]["previous_build_id"])
    return manifest


def validate_published_release(release_dir: Path, releases_root: Path, source_root: Path) -> dict:
    """READY eligibility plus proof that READY was written for this manifest."""
    manifest = validate_ready_eligibility(release_dir, releases_root, source_root)
    _ready_matches(release_dir, (release_dir / "manifest.json").read_bytes(), "candidate release")
    return manifest
