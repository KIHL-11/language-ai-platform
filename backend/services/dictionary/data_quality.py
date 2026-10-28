"""Dictionary Data Quality: sense-level checks read straight from SQLite.

This is deliberately independent of ``DictionaryService`` and its contextual
sense selection. A case names a lemma and identifies one source sense by its
definition text; the check then inspects exactly the Chinese values stored on
that sense. It answers "is the stored data correct?", not "does the lookup
engine pick the right sense for this sentence?" (that is the Context Sense
Selection gold set in ``release_quality``).
"""

import json
import re
import sqlite3
from pathlib import Path

from services.dictionary.provider import normalize_lexical

DATA_QUALITY_VERSION = 1
_LANGUAGES = ("en", "de")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


class DataQualityError(RuntimeError):
    """The data-quality case file is malformed or not approved."""


def _strings(value, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise DataQualityError(f"{field} must be a list of non-empty strings")
    return value


def load_data_quality_cases(path: Path, *, require_approval: bool = True, allow_fixture: bool = False) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise DataQualityError("data-quality cases must be a regular file under 1 MiB")
    try:
        cases = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataQualityError("data-quality cases are malformed") from exc
    if not isinstance(cases, dict) or cases.get("data_quality_version") != DATA_QUALITY_VERSION:
        raise DataQualityError("data-quality case version is unsupported")
    if cases.get("scope") not in {"production", "synthetic_fixture"}:
        raise DataQualityError("data-quality scope is invalid")
    if cases["scope"] == "synthetic_fixture" and not allow_fixture:
        raise DataQualityError("synthetic fixture data-quality cases cannot gate a production build")
    if require_approval and cases.get("approved") is not True:
        raise DataQualityError("data-quality cases have not been approved by the Owner")
    values = cases.get("cases")
    if not isinstance(values, list) or not values:
        raise DataQualityError("data-quality set has no cases")
    seen = set()
    for index, case in enumerate(values):
        field = f"cases[{index}]"
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in seen:
            raise DataQualityError(f"{field}.id is missing or duplicated")
        seen.add(case["id"])
        if case.get("language") not in _LANGUAGES or not isinstance(case.get("lemma"), str):
            raise DataQualityError(f"{field} needs language and lemma")
        if "part_of_speech" in case and not isinstance(case["part_of_speech"], str):
            raise DataQualityError(f"{field}.part_of_speech must be a string")
        scope = case.get("sense")
        if scope != "all" and not (isinstance(scope, dict) and _strings(scope.get("definition_contains_any"), f"{field}.sense.definition_contains_any")):
            raise DataQualityError(f'{field}.sense must be "all" or {{"definition_contains_any": [...]}}')
        _strings(case.get("chinese_allowed"), f"{field}.chinese_allowed")
        _strings(case.get("chinese_forbidden"), f"{field}.chinese_forbidden")
        if type(case.get("chinese_required", False)) is not bool:
            raise DataQualityError(f"{field}.chinese_required must be boolean")
    return cases


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _senses(connection: sqlite3.Connection, case: dict) -> list[dict]:
    rows = connection.execute(
        """SELECT entries.id, entries.lemma, entries.part_of_speech, senses.sense_index,
                  senses.definition, senses.translations_json
           FROM entries JOIN senses ON senses.entry_id = entries.id
           WHERE entries.language = ? AND entries.normalized_lemma = ?
           ORDER BY entries.id, senses.sense_index""",
        (case["language"], normalize_lexical(case["lemma"])),
    ).fetchall()
    output = []
    for entry_id, lemma, pos, sense_index, definition, translations_json in rows:
        if lemma != case["lemma"]:
            continue  # exact surface lemma (e.g. "bank" vs "Bank")
        if "part_of_speech" in case and pos != case["part_of_speech"]:
            continue
        output.append({
            "entry_id": int(entry_id),
            "part_of_speech": pos,
            "sense_index": int(sense_index),
            "definition": definition,
            "chinese": json.loads(translations_json).get("zh-CN", []),
        })
    return output


def evaluate_data_quality(database: Path, cases: dict) -> dict:
    """Evaluate every case against stored senses. Never consults DictionaryService."""
    connection = _readonly(database)
    try:
        results = []
        failures = incorrect = gaps = reviewed = 0
        for case in cases["cases"]:
            all_senses = _senses(connection, case)
            scope = case["sense"]
            if scope == "all":
                targets = all_senses
            else:
                needles = [value.casefold() for value in scope["definition_contains_any"]]
                targets = [
                    sense for sense in all_senses
                    if any(needle in sense["definition"].casefold() for needle in needles)
                ]
            allowed = case.get("chinese_allowed") or []
            forbidden = case.get("chinese_forbidden") or []
            reasons = []
            classification = "correct"
            if not all_senses:
                reasons.append("lemma not found")
                classification = "missing_entry"
            elif not targets:
                reasons.append("target sense not found")
                classification = "missing_sense"
            values = sorted({value for sense in targets for value in sense["chinese"]})
            wrong = sorted({value for value in values for bad in forbidden if bad in value})
            unreviewed = sorted(
                value for value in values if allowed and value not in allowed and value not in wrong
            )
            if values and (allowed or forbidden):
                reviewed += 1
            if wrong:
                reasons.append("forbidden Chinese value stored on the target sense")
                classification = "incorrect_association"
                incorrect += 1
            elif unreviewed:
                reasons.append("Chinese value outside the reviewed set")
                classification = "unreviewed_association"
            if targets and not values:
                if case.get("chinese_required"):
                    reasons.append("required Chinese value is missing")
                    classification = "missing_required"
                elif allowed:
                    # A missing value is a coverage gap only where one is expected;
                    # forbidden-only ("no fabrication") cases are satisfied by absence.
                    gaps += 1
                    if classification == "correct":
                        classification = "coverage_gap"
            passed = not reasons
            failures += 0 if passed else 1
            results.append({
                "id": case["id"],
                "language": case["language"],
                "lemma": case["lemma"],
                "passed": passed,
                "classification": classification,
                "failure_reasons": reasons,
                "target_senses": [
                    {key: sense[key] for key in ("part_of_speech", "sense_index", "definition", "chinese")}
                    for sense in targets[:10]
                ],
                "chinese_values": values,
                "forbidden_found": wrong,
                "unreviewed_values": unreviewed,
            })
        return {
            "data_quality_failures": failures,
            "incorrect_associations": incorrect,
            "reviewed_associations": reviewed,
            "coverage_gaps": gaps,
            "cases": results,
        }
    finally:
        connection.close()


def broadcast_profile(database: Path) -> dict:
    """How often one Chinese value is stored on several senses of the same entry.

    Broadcasting an entry-level translation to many senses was the old
    importer's failure mode; a high rate is a data-quality smell.
    """
    connection = _readonly(database)
    try:
        profile = {language: {"entries_with_chinese": 0, "entries_with_broadcast": 0,
                              "chinese_values": 0, "broadcast_values": 0, "non_han_values": 0}
                   for language in _LANGUAGES}
        current = None
        language = None
        counts: dict[str, int] = {}

        def flush():
            if current is None or language not in profile or not counts:
                return
            bucket = profile[language]
            bucket["entries_with_chinese"] += 1
            repeated = [value for value, count in counts.items() if count > 1]
            if repeated:
                bucket["entries_with_broadcast"] += 1
            bucket["broadcast_values"] += sum(counts[value] for value in repeated)

        for entry_id, entry_language, translations_json in connection.execute(
            "SELECT senses.entry_id, entries.language, senses.translations_json FROM senses "
            "JOIN entries ON entries.id = senses.entry_id ORDER BY senses.entry_id, senses.sense_index"
        ):
            if entry_id != current:
                flush()
                current, language, counts = entry_id, str(entry_language), {}
            for value in json.loads(translations_json).get("zh-CN", []):
                counts[value] = counts.get(value, 0) + 1
                if language in profile:
                    profile[language]["chinese_values"] += 1
                    if not _HAN.search(value):
                        profile[language]["non_han_values"] += 1
        flush()
        for bucket in profile.values():
            entries = bucket["entries_with_chinese"]
            values = bucket["chinese_values"]
            bucket["broadcast_entry_percent"] = round(100 * bucket["entries_with_broadcast"] / entries, 4) if entries else 0.0
            bucket["broadcast_value_percent"] = round(100 * bucket["broadcast_values"] / values, 4) if values else 0.0
        return profile
    finally:
        connection.close()
