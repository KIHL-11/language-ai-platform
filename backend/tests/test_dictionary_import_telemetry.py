import json
import os
import sqlite3
from pathlib import Path

import pytest

from services.dictionary import provider as provider_module
from services.dictionary.import_telemetry import ENTRY_ACCEPTED, ENTRY_REJECTED, ImportTelemetry
from services.dictionary.provider import (
    _aligned_entry_translations,
    _translation_values,
    import_kaikki_sources,
)
from services.dictionary.release_manifest import validate_manifest_structure

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = FIXTURES / "migration"
SOURCES = [
    (MIGRATION / "kaikki_migration_en.jsonl", "en", "https://kaikki.org/en/fixture"),
    (MIGRATION / "kaikki_migration_de.jsonl", "de", "https://kaikki.org/de/fixture"),
]


def _dump(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return list(connection.iterdump())
    finally:
        connection.close()


def _import(tmp_path: Path, name: str, telemetry=None, sources=SOURCES) -> Path:
    database = tmp_path / name
    import_kaikki_sources(sources, database, content_license="Fixture", telemetry=telemetry)
    return database


def test_telemetry_observes_without_changing_imported_rows(tmp_path):
    plain = _import(tmp_path, "plain.sqlite3")
    instrumented = _import(tmp_path, "instrumented.sqlite3", ImportTelemetry())
    assert _dump(plain) == _dump(instrumented)


def test_legacy_fixtures_import_identically_with_telemetry(tmp_path):
    sources = [
        (FIXTURES / "kaikki_en_fixture.jsonl", "en", "https://kaikki.org/en"),
        (FIXTURES / "kaikki_de_fixture.jsonl", "de", "https://kaikki.org/de"),
    ]
    assert _dump(_import(tmp_path, "a.sqlite3", sources=sources)) == _dump(
        _import(tmp_path, "b.sqlite3", ImportTelemetry(), sources=sources)
    )


def test_telemetry_reconciles_every_translation_record(tmp_path):
    telemetry = ImportTelemetry()
    _import(tmp_path, "dictionary.sqlite3", telemetry)
    totals = telemetry.manifest_telemetry()
    sense = totals["sense_local"]
    entry = totals["entry_level"]
    assert sense["seen"] == sense["retained"] + sense["deduplicated"] + sense["truncated"]
    assert entry["seen"] == entry["accepted"]["total"] + entry["rejected"]["total"]
    assert entry["accepted"]["total"] == sum(entry["accepted"][key] for key in ENTRY_ACCEPTED)
    assert entry["rejected"]["total"] == sum(entry["rejected"][key] for key in ENTRY_REJECTED)

    report = telemetry.report()
    pairs = {(value["edition"], value["target_language"]): value for value in report["by_edition_and_target"]}
    english_chinese = pairs[("en", "zh-CN")]
    # house: 12 distinct direct values -> 10 kept, 2 over the per-language limit;
    # die: one exact duplicate; the gloss-less house sense discards one value.
    assert english_chinese["sense_local"] == {
        "seen": 15, "retained": 12, "deduplicated": 1, "truncated": 2,
        "discarded_with_invalid_sense": 1,
    }
    rejected = english_chinese["entry_level"]["rejected"]
    assert rejected["generic_stopword_or_lemma_only"] == 1  # get: "something"
    assert rejected["unsupported_morphology"] == 1  # bank: "riverside" vs "river"
    german_chinese = pairs[("de", "zh-CN")]["entry_level"]["rejected"]
    assert german_chinese["unsupported_morphology"] == 1  # Haustür: "Eingangstür" vs "Eingang"
    assert german_chinese["zero_evidence"] == 1  # Haustür: "Haus" matches no definition token
    assert report["records"]["en"]["skipped_language"] == 1
    assert report["records"]["de"]["skipped_language"] == 1
    assert report["records"]["en"]["blank_lines"] == 1
    assert report["records"]["en"]["senses_without_definition"] == 1
    # Aggregates equal the sum of the per-pair breakdown.
    assert sense["seen"] == sum(value["sense_local"]["seen"] for value in pairs.values())
    assert entry["seen"] == sum(value["entry_level"]["seen"] for value in pairs.values())


def test_manifest_telemetry_shape_is_accepted_by_manifest_v1(tmp_path):
    telemetry = ImportTelemetry()
    _import(tmp_path, "dictionary.sqlite3", telemetry)
    example = Path(__file__).resolve().parents[2] / "docs" / "DICTIONARY-BUILD-MANIFEST-v1.example.json"
    manifest = json.loads(example.read_text(encoding="utf-8"))
    validate_manifest_structure(manifest)
    manifest["statistics"]["alignment_telemetry"] = telemetry.manifest_telemetry()
    validate_manifest_structure(manifest)


@pytest.mark.parametrize(
    ("records", "definitions", "expected"),
    [
        ([("zh-CN", "一", "")], ["Only sense."], ("unscoped_single_sense", 0)),
        ([("zh-CN", "一", "")], ["First.", "Second."], ("ambiguous_or_tied", None)),
        ([("zh-CN", "一", "the of something")], ["First thing.", "Second item."], ("generic_stopword_or_lemma_only", None)),
        ([("zh-CN", "一", "money")], ["A river side.", "A grassy slope."], ("zero_evidence", None)),
        ([("zh-CN", "一", "money")], ["A river side."], ("single_sense_mismatch", None)),
        ([("zh-CN", "一", "riverside")], ["Land by a river.", "A slope."], ("unsupported_morphology", None)),
        ([("zh-CN", "一", "river")], ["Land by a river.", "A river crossing."], ("ambiguous_or_tied", None)),
        ([("zh-CN", "一", "understand")], ["To understand fully.", "To obtain."], ("reliable_single_token_head", 0)),
        ([("zh-CN", "一", "people money")], ["Where people keep money.", "Land beside water."], ("unique_multi_token", 0)),
        ([("zh-CN", "一", "fast car")], ["A quick car.", "A slope."], ("weak_single_token_evidence", None)),
        ([("zh-CN", "一", "car")], ["A quick car.", "A slope."], ("weak_single_token_evidence", None)),
        ([("zh-CN", "一", "anything")], [], ("zero_evidence", None)),
    ],
)
def test_alignment_decisions_classify_each_record(records, definitions, expected):
    decisions = []
    aligned = _aligned_entry_translations(records, definitions, language="en", lemma="bank", decisions=decisions)
    assert decisions == [expected]
    assert aligned == _aligned_entry_translations(records, definitions, language="en", lemma="bank")
    outcome, winner = expected
    if winner is None:
        assert all(not values for values in aligned)
    else:
        assert aligned[winner] == [("zh-CN", "一")]


def test_translation_value_outcomes_report_limit_and_duplicates():
    outcomes = {}
    direct = [("zh-CN", f"词{index}") for index in range(10)] + [("zh-CN", "词0")]
    output = _translation_values([("zh-CN", "新"), ("zh-CN", "词1"), ("de", "Wort")], direct, outcomes=outcomes)
    assert output["zh-CN"] == [f"词{index}" for index in range(10)]
    assert output["de"] == ["Wort"]
    assert outcomes["sense"] == ["stored"] * 10 + ["duplicate"]
    assert outcomes["entry"] == ["over_limit", "duplicate", "stored"]


def test_entry_level_over_limit_is_rejected_not_hidden(tmp_path):
    source = tmp_path / "limit.jsonl"
    source.write_text(json.dumps({
        "word": "many", "lang_code": "en", "pos": "adj",
        "senses": [{"glosses": ["A large number."],
                    "translations": [{"code": "zh", "word": f"多{index}"} for index in range(10)]}],
        "translations": [{"code": "zh", "word": "许多"}, {"code": "zh", "word": "多0"}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    telemetry = ImportTelemetry()
    import_kaikki_sources([(source, "en", "https://kaikki.org/x")], tmp_path / "d.sqlite3",
                          content_license="Fixture", telemetry=telemetry)
    entry = telemetry.report()["by_edition_and_target"][0]["entry_level"]
    assert entry["seen"] == 2
    assert entry["rejected"]["over_limit"] == 1
    assert entry["accepted"]["unscoped_single_sense"] == 1
    assert entry["accepted_duplicate_of_existing"] == 1


def test_importer_fsyncs_database_and_directory_before_and_after_publication(tmp_path, monkeypatch):
    calls = []
    real_fsync = os.fsync

    def recording_fsync(descriptor):
        calls.append(os.fstat(descriptor).st_mode)
        return real_fsync(descriptor)

    monkeypatch.setattr(provider_module.os, "fsync", recording_fsync)
    _import(tmp_path, "dictionary.sqlite3")
    import stat as stat_module

    assert any(stat_module.S_ISREG(mode) for mode in calls)
    assert any(stat_module.S_ISDIR(mode) for mode in calls)
    assert not (tmp_path / "dictionary.sqlite3.tmp").exists()


def test_failed_import_leaves_no_candidate_and_no_temporary(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"word":"ok","lang_code":"en","senses":[]}\n{broken\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        import_kaikki_sources([(bad, "en", "https://kaikki.org/x")], tmp_path / "d.sqlite3",
                              content_license="Fixture", telemetry=ImportTelemetry())
    assert not (tmp_path / "d.sqlite3").exists()
    assert not (tmp_path / "d.sqlite3.tmp").exists()


def test_unknown_telemetry_outcomes_are_rejected():
    telemetry = ImportTelemetry()
    with pytest.raises(ValueError):
        telemetry.entry_level("en", "zh-CN", "guessed")
    with pytest.raises(ValueError):
        telemetry.sense_local("en", "zh-CN", "dropped")
    with pytest.raises(ValueError):
        telemetry.record("en", "mystery")
