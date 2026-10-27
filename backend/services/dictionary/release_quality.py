"""Read-only quality, query-plan, and performance gates for a candidate release."""

import json
import re
import sqlite3
import time
from pathlib import Path

from services.dictionary.provider import SCHEMA_VERSION, SQLiteDictionaryProvider
from services.dictionary.release_manifest import dictionary_statistics
from services.dictionary.service import DictionaryService

GOLD_SET_VERSION = 1
COLD_LOOKUP_LIMIT_SECONDS = 1.0
HOT_LOOKUP_LIMIT_SECONDS = 0.5
_LANGUAGES = ("en", "de")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")

# Every provider lookup query must be served by the named index; a plain
# ``SCAN <table>`` on these large tables is a release-blocking regression.
REQUIRED_QUERY_PLANS = {
    "lemma": (
        "SELECT id FROM entries WHERE language = ? AND normalized_lemma = ? ORDER BY id LIMIT 20",
        ("en", "x"),
        "entries",
        "entries_lookup",
    ),
    "form": (
        "SELECT DISTINCT entries.id FROM forms JOIN entries ON entries.id = forms.entry_id "
        "WHERE entries.language = ? AND forms.normalized_form = ? ORDER BY entries.id LIMIT 20",
        ("en", "x"),
        "forms",
        "forms_lookup",
    ),
    "component": (
        "SELECT forms.id FROM form_components JOIN forms ON forms.id = form_components.form_id "
        "JOIN entries ON entries.id = forms.entry_id "
        "WHERE entries.language = ? AND form_components.component = ? "
        "ORDER BY forms.entry_id, forms.id LIMIT 100",
        ("en", "x"),
        "form_components",
        "form_components_lookup",
    ),
    "sense": (
        "SELECT * FROM senses WHERE entry_id = ? ORDER BY sense_index LIMIT 100",
        (1,),
        "senses",
        "sqlite_autoindex_senses_1",
    ),
    "pronunciation": (
        "SELECT * FROM pronunciations WHERE entry_id = ? ORDER BY pronunciation_index LIMIT 20",
        (1,),
        "pronunciations",
        "sqlite_autoindex_pronunciations_1",
    ),
}


class QualityGateError(RuntimeError):
    """A candidate failed a release-blocking quality gate."""


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def validate_candidate_database(database: Path) -> dict:
    """Integrity, foreign keys, schema, query plans, and bounded JSON fields."""
    for suffix in ("-journal", "-wal", "-shm"):
        if Path(f"{database}{suffix}").exists():
            raise QualityGateError(f"candidate has unexpected {suffix} state")
    connection = _readonly(database)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise QualityGateError("candidate integrity_check failed")
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if foreign_key_errors:
            raise QualityGateError("candidate foreign_key_check failed")
        version = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if version is None or version[0] != str(SCHEMA_VERSION):
            raise QualityGateError("candidate schema version is unsupported")
        for key in ("source_url", "content_license"):
            if connection.execute("SELECT 1 FROM metadata WHERE key = ?", (key,)).fetchone() is None:
                raise QualityGateError(f"candidate metadata {key} is missing")
        plans = {}
        for name, (query, parameters, table, index) in REQUIRED_QUERY_PLANS.items():
            details = [str(row[3]) for row in connection.execute(f"EXPLAIN QUERY PLAN {query}", parameters)]
            plan = " | ".join(details)
            if f"SCAN {table}" in plan or not any(
                detail.startswith(f"SEARCH {table} ") and index in detail for detail in details
            ):
                raise QualityGateError(f"{name} lookup does not use {index}")
            plans[name] = plan
        json_rows = 0
        for examples_json, translations_json in connection.execute(
            "SELECT examples_json, translations_json FROM senses"
        ):
            examples = json.loads(examples_json)
            translations = json.loads(translations_json)
            if not isinstance(examples, list) or len(examples) > 5 or not all(isinstance(v, str) for v in examples):
                raise QualityGateError("sense examples_json is malformed or unbounded")
            if not isinstance(translations, dict) or any(
                not isinstance(values, list) or len(values) > 10 or not all(isinstance(v, str) for v in values)
                for values in translations.values()
            ):
                raise QualityGateError("sense translations_json is malformed or unbounded")
            json_rows += 1
        for (tags_json,) in connection.execute("SELECT tags_json FROM forms"):
            if not isinstance(json.loads(tags_json), list):
                raise QualityGateError("form tags_json is malformed")
    except (json.JSONDecodeError, sqlite3.DatabaseError) as exc:
        raise QualityGateError(f"candidate cannot be validated: {type(exc).__name__}") from exc
    finally:
        connection.close()
    return {
        "integrity_check": "ok",
        "foreign_key_error_count": 0,
        "required_query_plans": "passed",
        "query_plans": plans,
        "json_rows_checked": json_rows,
    }


def _string_list(value, field: str, *, required: bool = False) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise QualityGateError(f"{field} must be a list of non-empty strings")
    return value


def load_gold_set(path: Path, *, allow_fixture: bool = False) -> dict:
    """Load a versioned, Owner-approved gold set; fixtures need explicit opt-in."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise QualityGateError("gold set must be a regular file under 1 MiB")
    try:
        gold = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityGateError("gold set is malformed") from exc
    if not isinstance(gold, dict) or gold.get("gold_set_version") != GOLD_SET_VERSION:
        raise QualityGateError("gold set version is unsupported")
    if gold.get("scope") not in {"production", "synthetic_fixture"}:
        raise QualityGateError("gold set scope is invalid")
    if gold["scope"] == "synthetic_fixture" and not allow_fixture:
        raise QualityGateError("a synthetic fixture gold set cannot gate a production build")
    if gold.get("approved") is not True:
        raise QualityGateError("gold set has not been approved by the Owner")
    cases = gold.get("cases")
    if not isinstance(cases, list) or not cases:
        raise QualityGateError("gold set has no cases")
    identifiers = set()
    for index, case in enumerate(cases):
        field = f"cases[{index}]"
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in identifiers:
            raise QualityGateError(f"{field}.id is missing or duplicated")
        identifiers.add(case["id"])
        if case.get("language") not in _LANGUAGES:
            raise QualityGateError(f"{field}.language is invalid")
        for key in ("word", "sentence"):
            if not isinstance(case.get(key), str):
                raise QualityGateError(f"{field}.{key} is required")
        expect = case.get("expect")
        if not isinstance(expect, dict):
            raise QualityGateError(f"{field}.expect is required")
        for key in ("definition_contains_any", "definition_excludes", "chinese_any", "chinese_forbidden"):
            _string_list(expect.get(key), f"{field}.expect.{key}")
        if "lemma" in expect and not isinstance(expect["lemma"], str):
            raise QualityGateError(f"{field}.expect.lemma must be a string")
        if type(expect.get("chinese_may_be_missing", False)) is not bool:
            raise QualityGateError(f"{field}.expect.chinese_may_be_missing must be boolean")
    for group in ("common_words", "performance_terms"):
        value = gold.get(group)
        if not isinstance(value, dict) or set(value) != set(_LANGUAGES):
            raise QualityGateError(f"{group} must list en and de words")
        for language in _LANGUAGES:
            _string_list(value[language], f"{group}.{language}", required=True)
    for language in _LANGUAGES:
        if len(set(gold["performance_terms"][language])) < 2:
            raise QualityGateError("performance_terms need at least two distinct words per language")
    return gold


def _service(database: Path, sidecar: Path | None) -> DictionaryService:
    return DictionaryService(SQLiteDictionaryProvider(database, sidecar))


def evaluate_gold_cases(database: Path, sidecar: Path | None, gold: dict) -> dict:
    service = _service(database, sidecar)
    results = []
    failures = 0
    incorrect = 0
    reviewed = 0
    coverage_gaps = 0
    for case in gold["cases"]:
        expect = case["expect"]
        response = service.lookup(case["language"], case["word"], case["sentence"])
        definition = response.explanations.monolingual.value or ""
        chinese = response.explanations.chinese.value
        reasons = []
        classification = "correct"
        if response.status != "found":
            reasons.append(f"lookup status {response.status}")
            classification = "lookup_failure"
        if "lemma" in expect and response.lemma != expect["lemma"]:
            reasons.append("unexpected lemma")
        if "part_of_speech" in expect and response.part_of_speech != expect["part_of_speech"]:
            reasons.append("unexpected part of speech")
        folded = definition.casefold()
        contains = expect.get("definition_contains_any") or []
        if contains and not any(value.casefold() in folded for value in contains):
            reasons.append("selected definition is not the expected sense")
        if any(value.casefold() in folded for value in expect.get("definition_excludes") or []):
            reasons.append("selected definition is a forbidden sense")
        forbidden = expect.get("chinese_forbidden") or []
        allowed = expect.get("chinese_any") or []
        if chinese:
            if allowed or forbidden:
                reviewed += 1
            if any(value in chinese for value in forbidden):
                reasons.append("forbidden Chinese translation associated")
                classification = "incorrect_association"
                incorrect += 1
            elif allowed and not any(value in chinese for value in allowed):
                reasons.append("Chinese translation is not in the reviewed set")
                classification = "incorrect_association"
                incorrect += 1
        elif allowed and not expect.get("chinese_may_be_missing", False):
            reasons.append("expected Chinese translation is missing")
        elif allowed:
            coverage_gaps += 1
            if classification == "correct":
                classification = "coverage_gap"
        if reasons and classification in {"correct", "coverage_gap"}:
            classification = "sense_or_lemma_failure"
        passed = not reasons
        failures += 0 if passed else 1
        results.append({
            "id": case["id"],
            "language": case["language"],
            "word": case["word"],
            "sentence": case["sentence"],
            "passed": passed,
            "classification": classification,
            "failure_reasons": reasons,
            "status": response.status,
            "selected_lemma": response.lemma,
            "part_of_speech": response.part_of_speech,
            "definition": definition or None,
            "chinese": chinese,
            "chinese_status": response.explanations.chinese.status,
            "selection_method": response.context.selection_method,
            "lemma_candidates": response.lemma_candidates,
            "alternate_senses": [value.definition for value in response.alternate_senses[:5]],
            "forbidden_chinese": forbidden,
        })
    return {
        "gold_case_failures": failures,
        "incorrect_associations": incorrect,
        "reviewed_associations": reviewed,
        "wrong_association_rate": (incorrect / reviewed) if reviewed else 0.0,
        "coverage_gaps": coverage_gaps,
        "cases": results,
    }


def common_word_coverage(database: Path, gold: dict) -> dict:
    service = _service(database, None)
    output = {}
    for language in _LANGUAGES:
        words = gold["common_words"][language]
        covered = sum(
            1 for word in words
            if service.lookup(language, word, "").explanations.chinese.value
        )
        output[language] = {
            "words": len(words),
            "with_chinese": covered,
            "percent": round(100 * covered / len(words), 6),
        }
    return output


def chinese_script_profile(database: Path) -> dict:
    """Han-script versus Cyrillic-only values among stored zh-CN translations."""
    profile = {language: {"han": 0, "cyrillic_only": 0, "other": 0} for language in _LANGUAGES}
    connection = _readonly(database)
    try:
        for language, translations_json in connection.execute(
            "SELECT entries.language, senses.translations_json FROM senses "
            "JOIN entries ON entries.id = senses.entry_id"
        ):
            for value in json.loads(translations_json).get("zh-CN", []):
                bucket = profile.setdefault(str(language), {"han": 0, "cyrillic_only": 0, "other": 0})
                if _HAN.search(value):
                    bucket["han"] += 1
                elif _CYRILLIC.search(value):
                    bucket["cyrillic_only"] += 1
                else:
                    bucket["other"] += 1
    finally:
        connection.close()
    return profile


def measure_performance(database: Path, sidecar: Path, gold: dict) -> dict:
    """Cold (fresh service, distinct words) and hot (cache hit) lookup timings."""
    measurements = {}
    for language in _LANGUAGES:
        terms = list(dict.fromkeys(gold["performance_terms"][language]))
        service = _service(database, sidecar)
        cold = []
        for term in terms:
            started = time.perf_counter()
            service.lookup(language, term, "", include_inflections=True)
            cold.append(time.perf_counter() - started)
        hot = []
        for term in terms:
            started = time.perf_counter()
            service.lookup(language, term, "", include_inflections=True)
            hot.append(time.perf_counter() - started)
        measurements[language] = {
            "terms": len(terms),
            "cold_max_seconds": round(max(cold), 6),
            "hot_max_seconds": round(max(hot), 6),
        }
    passed = all(
        value["cold_max_seconds"] < COLD_LOOKUP_LIMIT_SECONDS
        and value["hot_max_seconds"] < HOT_LOOKUP_LIMIT_SECONDS
        for value in measurements.values()
    )
    return {
        "cold_limit_seconds": COLD_LOOKUP_LIMIT_SECONDS,
        "hot_limit_seconds": HOT_LOOKUP_LIMIT_SECONDS,
        "passed": passed,
        "languages": measurements,
    }


def _delta(old, new):
    if isinstance(old, dict):
        return {key: _delta(old.get(key), new.get(key)) for key in new}
    absolute = new - old
    percent = round(100 * absolute / old, 6) if old else None
    return {"baseline": old, "candidate": new, "absolute": absolute, "percent": percent}


def structural_comparison(baseline_database: Path, candidate_database: Path) -> dict:
    baseline = dictionary_statistics(baseline_database)
    candidate = dictionary_statistics(candidate_database)
    return {
        key: _delta(baseline[key], candidate[key])
        for key in ("entries", "senses", "forms", "pronunciations", "chinese_coverage")
    }
