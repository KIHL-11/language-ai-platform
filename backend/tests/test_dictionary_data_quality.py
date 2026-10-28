"""Dictionary Data Quality (SQLite, sense-level) and old-vs-new comparison."""

import gzip
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from services.dictionary.data_quality import (
    DataQualityError,
    broadcast_profile,
    evaluate_data_quality,
    load_data_quality_cases,
)
from services.dictionary.provider import import_kaikki_sources
from services.dictionary.release_comparison import (
    compare_releases,
    importer_effect_breakdown,
    markdown_summary,
    raw_alignment_causes,
)
from services.dictionary.release_quality import load_gold_set

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = FIXTURES / "migration"
DOCS = Path(__file__).resolve().parents[2] / "docs"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compare_dictionary_releases.py"
SOURCES = [
    (MIGRATION / "kaikki_migration_en.jsonl", "en", "https://kaikki.org/en"),
    (MIGRATION / "kaikki_migration_de.jsonl", "de", "https://kaikki.org/de"),
]

CASES = {
    "data_quality_version": 1,
    "scope": "synthetic_fixture",
    "approved": True,
    "cases": [
        {"id": "get-understand", "language": "en", "lemma": "get", "part_of_speech": "verb",
         "sense": {"definition_contains_any": ["understand"]},
         "chinese_allowed": ["明白"], "chinese_forbidden": ["得到", "变得"]},
        {"id": "get-obtain", "language": "en", "lemma": "get",
         "sense": {"definition_contains_any": ["obtain"]},
         "chinese_allowed": ["得到"], "chinese_forbidden": ["明白"]},
        {"id": "bank-river", "language": "en", "lemma": "bank",
         "sense": {"definition_contains_any": ["river"]},
         "chinese_allowed": ["河岸", "岸"], "chinese_forbidden": ["银行"]},
        {"id": "die", "language": "en", "lemma": "die", "sense": "all",
         "chinese_allowed": ["死", "死亡"], "chinese_forbidden": ["骰子"]},
        {"id": "entwicklung-gap", "language": "de", "lemma": "Entwicklung", "sense": "all",
         "chinese_allowed": ["发展"]},
        {"id": "haustuer", "language": "de", "lemma": "Haustür", "sense": "all",
         "chinese_forbidden": ["家门", "前门"]},
    ],
}


def _build(path: Path) -> Path:
    import_kaikki_sources(SOURCES, path, content_license="Fixture")
    return path


def _cases_file(tmp_path: Path, payload: dict = CASES) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _set_chinese(database: Path, lemma: str, values_by_sense: dict[int, list[str]]) -> None:
    connection = sqlite3.connect(database)
    entry_id = connection.execute("SELECT id FROM entries WHERE lemma = ?", (lemma,)).fetchone()[0]
    for sense_index, values in values_by_sense.items():
        row = connection.execute(
            "SELECT translations_json FROM senses WHERE entry_id = ? AND sense_index = ?",
            (entry_id, sense_index),
        ).fetchone()
        translations = json.loads(row[0])
        if values:
            translations["zh-CN"] = values
        else:
            translations.pop("zh-CN", None)
        connection.execute(
            "UPDATE senses SET translations_json = ? WHERE entry_id = ? AND sense_index = ?",
            (json.dumps(translations, ensure_ascii=False), entry_id, sense_index),
        )
    connection.commit()
    connection.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------- data quality

def test_clean_candidate_passes_sense_level_data_quality(tmp_path):
    database = _build(tmp_path / "d.sqlite3")
    before = _sha(database)
    result = evaluate_data_quality(database, load_data_quality_cases(_cases_file(tmp_path), allow_fixture=True))
    by_id = {case["id"]: case for case in result["cases"]}
    assert result["data_quality_failures"] == 0
    assert result["incorrect_associations"] == 0
    assert by_id["get-understand"]["chinese_values"] == ["明白"]
    assert by_id["entwicklung-gap"]["classification"] == "coverage_gap"
    assert by_id["haustuer"]["classification"] == "correct"
    assert _sha(database) == before  # read-only


def test_broadcast_value_on_wrong_sense_is_an_incorrect_association(tmp_path):
    database = _build(tmp_path / "d.sqlite3")
    # Old-importer style: every get value on every sense.
    _set_chinese(database, "get", {0: ["得到", "明白", "变得"], 1: ["得到", "明白", "变得"], 2: ["得到", "明白", "变得"]})
    result = evaluate_data_quality(database, load_data_quality_cases(_cases_file(tmp_path), allow_fixture=True))
    by_id = {case["id"]: case for case in result["cases"]}
    assert by_id["get-understand"]["classification"] == "incorrect_association"
    assert by_id["get-understand"]["forbidden_found"] == ["变得", "得到"]
    assert by_id["get-obtain"]["forbidden_found"] == ["明白"]
    assert result["incorrect_associations"] == 2


def test_values_outside_reviewed_set_and_missing_entries_fail(tmp_path):
    database = _build(tmp_path / "d.sqlite3")
    _set_chinese(database, "bank", {1: ["河边"]})
    payload = json.loads(json.dumps(CASES))
    payload["cases"].append({"id": "missing", "language": "en", "lemma": "nonexistentword", "sense": "all"})
    payload["cases"].append({"id": "required", "language": "de", "lemma": "Entwicklung", "sense": "all",
                             "chinese_required": True})
    result = evaluate_data_quality(database, load_data_quality_cases(_cases_file(tmp_path, payload), allow_fixture=True))
    by_id = {case["id"]: case for case in result["cases"]}
    assert by_id["bank-river"]["classification"] == "unreviewed_association"
    assert by_id["missing"]["classification"] == "missing_entry"
    assert by_id["required"]["classification"] == "missing_required"
    assert result["data_quality_failures"] == 3


def test_case_file_validation_and_approval(tmp_path):
    with pytest.raises(DataQualityError, match="synthetic fixture"):
        load_data_quality_cases(_cases_file(tmp_path))
    draft = DOCS / "DICTIONARY-DATA-QUALITY-CASES-v1.draft.json"
    with pytest.raises(DataQualityError, match="approved"):
        load_data_quality_cases(draft)
    assert load_data_quality_cases(draft, require_approval=False)["scope"] == "production"
    bad = json.loads(json.dumps(CASES))
    bad["cases"][0]["sense"] = {"definition_contains_any": []}
    with pytest.raises(DataQualityError, match="sense"):
        load_data_quality_cases(_cases_file(tmp_path, bad), allow_fixture=True)


def test_context_gold_set_is_unchanged_nine_cases():
    gold = json.loads((DOCS / "DICTIONARY-GOLD-CASES-v1.draft.json").read_text(encoding="utf-8"))
    assert [case["id"] for case in gold["cases"]] == [
        "en-get-understand", "en-get-obtain", "en-got-become", "de-komme-kommen", "de-entwicklung",
        "en-bank-river", "en-bank-financial", "en-die-not-stopword", "de-compound-no-fabrication",
    ]


def test_broadcast_profile_flags_repeated_values(tmp_path):
    clean = _build(tmp_path / "clean.sqlite3")
    noisy = tmp_path / "noisy.sqlite3"
    shutil.copy(clean, noisy)
    _set_chinese(noisy, "get", {0: ["得到"], 1: ["得到"], 2: ["得到"]})
    assert broadcast_profile(clean)["en"]["entries_with_broadcast"] == 0
    profile = broadcast_profile(noisy)["en"]
    assert profile["entries_with_broadcast"] == 1
    assert profile["broadcast_values"] == 3


# ------------------------------------------------------------- comparison

def test_importer_effect_breakdown_classifies_lost_entries(tmp_path):
    candidate = _build(tmp_path / "candidate.sqlite3")
    counterfactual = tmp_path / "old.sqlite3"
    shutil.copy(candidate, counterfactual)
    # candidate loses everything for: get (broadcast), house (single target), go (single-sense)
    _set_chinese(counterfactual, "get", {0: ["得到"], 1: ["得到"], 2: ["得到"]})
    _set_chinese(candidate, "get", {0: [], 1: [], 2: []})
    _set_chinese(candidate, "go", {0: []})
    _set_chinese(counterfactual, "Haustür", {0: ["家门"]})
    breakdown = importer_effect_breakdown(counterfactual, candidate)
    en = breakdown["en"]["lost_by_category"]
    assert en["broadcast_to_distinct_senses"] == 1
    assert en["single_sense_entry"] == 1
    assert breakdown["de"]["lost_by_category"]["single_target_sense_rejected"] == 1
    assert breakdown["en"]["entries_lost_all"] == 2


def test_importer_effect_breakdown_detects_identical_definition_ties(tmp_path):
    source = tmp_path / "tie.jsonl"
    source.write_text(json.dumps({
        "word": "die", "lang_code": "en", "pos": "verb",
        "senses": [{"glosses": ["To stop living."]}, {"glosses": ["To stop living.", "Of plants."]}],
        "translations": [{"code": "zh", "word": "死", "sense": "to stop living"}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    candidate = tmp_path / "candidate.sqlite3"
    # Phase 7A strict output: the tie is dropped, which is what the classifier must explain.
    import_kaikki_sources([(source, "en", "https://kaikki.org/x")], candidate, content_license="Fixture",
                          recovery=False)
    counterfactual = tmp_path / "old.sqlite3"
    shutil.copy(candidate, counterfactual)
    _set_chinese(counterfactual, "die", {0: ["死"], 1: ["死"]})
    breakdown = importer_effect_breakdown(counterfactual, candidate)
    assert breakdown["en"]["lost_by_category"]["identical_definition_tie"] == 1


def test_breakdown_refuses_databases_from_different_snapshots(tmp_path):
    candidate = _build(tmp_path / "candidate.sqlite3")
    other = tmp_path / "other.sqlite3"
    import_kaikki_sources([(FIXTURES / "kaikki_en_fixture.jsonl", "en", "https://kaikki.org/x")],
                          other, content_license="Fixture")
    with pytest.raises(ValueError, match="same snapshot"):
        importer_effect_breakdown(other, candidate)


def test_raw_alignment_causes_explain_each_rejected_translation(tmp_path):
    source = tmp_path / "en.jsonl.gz"
    source.write_bytes(gzip.compress((MIGRATION / "kaikki_migration_en.jsonl").read_bytes()))
    causes = raw_alignment_causes({"en": source}, {"en": {"get", "bank"}})
    get = causes["en"]["get"]
    assert get["zh_records"] == 4
    assert get["strict_decisions"] == {"unique_multi_token": 2, "reliable_single_token_head": 1,
                                       "generic_stopword_or_lemma_only": 1}
    # The old importer accepted all four; one only through the generic token "something".
    assert get["old_importer_accepts"] == 4
    assert get["old_matches_only_stopword_or_generic"] == 1
    rejected = {example["zh"]: example for example in get["examples"]}
    assert rejected["搞"]["strict_decision"] == "generic_stopword_or_lemma_only"
    assert rejected["搞"]["old_overlap_tokens"] == ["something"]
    assert rejected["明白"]["strict_sense"].startswith("To understand")
    bank = causes["en"]["bank"]
    assert bank["strict_decisions"] == {"unique_multi_token": 2, "unsupported_morphology": 1}
    assert bank["old_importer_accepts"] == 2  # "riverside" shares no exact token


def test_compare_releases_reports_two_independent_verdicts(tmp_path):
    candidate = _build(tmp_path / "candidate.sqlite3")
    baseline = tmp_path / "baseline.sqlite3"
    shutil.copy(candidate, baseline)
    _set_chinese(baseline, "get", {0: ["得到", "明白"], 1: ["得到", "明白"], 2: ["得到", "明白"]})
    _set_chinese(baseline, "Entwicklung", {0: ["发展"]})
    gold = load_gold_set(MIGRATION / "gold_fixture.json", allow_fixture=True)
    cases = load_data_quality_cases(_cases_file(tmp_path), allow_fixture=True)
    hashes = {path.name: _sha(path) for path in (baseline, candidate)}
    report = compare_releases(baseline=baseline, candidate=candidate, counterfactual=baseline,
                              data_quality_cases=cases, gold=gold,
                              common_words={"en": ["get", "go"], "de": ["Entwicklung", "Haus"]})
    verdicts = report["verdicts"]
    assert verdicts["dictionary_data_quality"]["passed"] is True
    assert verdicts["dictionary_data_quality"]["baseline_incorrect_associations"] == 2
    assert verdicts["context_sense_selection"]["passed"] is True
    # counterfactual == baseline here, so Entwicklung's loss is attributed to the importer.
    assert report["common_words"]["de"]["lost"] == [
        {"word": "Entwicklung", "baseline": ["发展"], "cause": "importer", "counterfactual": ["发展"]}
    ]
    assert report["coverage_decomposition"]["snapshot_effect"]["de"]["entries_with_chinese"]["absolute"] == 0
    assert report["coverage_decomposition"]["importer_effect"]["de"]["entries_with_chinese"]["absolute"] == -1
    assert report["importer_effect_breakdown"]["de"]["lost_by_category"]["single_sense_entry"] == 1
    assert "Dictionary Data Quality:** PASS" in markdown_summary(report)
    assert {path.name: _sha(path) for path in (baseline, candidate)} == hashes


def test_compare_cli_refuses_to_write_into_release_directory(tmp_path):
    candidate = _build(tmp_path / "candidate.sqlite3")
    output = tmp_path / "dictionary_releases" / "x"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--baseline", str(candidate), "--candidate", str(candidate),
         "--data-quality-cases", str(DOCS / "DICTIONARY-DATA-QUALITY-CASES-v1.draft.json"),
         "--gold-set", str(MIGRATION / "gold_fixture.json"), "--output-dir", str(output)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1 and "dictionary_releases" in result.stderr
    assert not output.exists()


def test_recovery_analysis_counts_added_and_script_removed_values(tmp_path):
    from services.dictionary.release_comparison import recovery_analysis

    rows = [
        {"word": "raven", "lang_code": "en", "pos": "noun",
         "senses": [{"glosses": ["A black bird."]}, {"glosses": ["A black bird.", "Corvus corax."]}],
         "translations": [{"code": "zh", "word": "渡鸦"}]},
        {"word": "want", "lang_code": "en", "pos": "verb",
         "senses": [{"glosses": ["To desire."], "translations": [{"code": "zh", "word": "щён"}, {"code": "zh", "word": "要"}]}]},
    ]
    source = tmp_path / "rows.jsonl"
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    strict = tmp_path / "strict.sqlite3"
    repaired = tmp_path / "repaired.sqlite3"
    import_kaikki_sources([(source, "en", "https://kaikki.org/x")], strict, content_license="F", recovery=False)
    import_kaikki_sources([(source, "en", "https://kaikki.org/x")], repaired, content_license="F")
    result = recovery_analysis(strict, repaired)
    en = result["summary"]["en"]
    assert en["entries_gained"] == 1 and en["values_added"] == 1
    assert en["values_removed_by_script_filter"] == 1 and en["values_removed_other"] == 0
    assert result["zh_values_by_method"]["en"]["equivalent_group"] == {"-": 1}
    assert result["review_samples"]["equivalent_group"][0]["zh"] == "渡鸦"
    other = tmp_path / "other.sqlite3"
    import_kaikki_sources([(source, "en", "https://kaikki.org/x")], other, content_license="F")
    connection = sqlite3.connect(other)
    connection.execute("DELETE FROM senses WHERE sense_index = 1")
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="same snapshot"):
        recovery_analysis(strict, other)
