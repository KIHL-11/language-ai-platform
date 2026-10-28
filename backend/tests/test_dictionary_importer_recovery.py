"""DQ-02 Phase 7B importer recovery: positive, negative and regression cases."""

import json
import sqlite3
from pathlib import Path

import pytest

from services.dictionary.import_telemetry import ENTRY_ACCEPTED, ImportTelemetry
from services.dictionary.provider import import_kaikki_sources
from services.dictionary.recovery import (
    chinese_script_rejection,
    equivalence_groups,
    single_sense_entry_is_safe,
    single_translation_table,
)
from services.dictionary.release_manifest import validate_manifest_structure

MIGRATION = Path(__file__).parent / "fixtures" / "migration"
DOCS = Path(__file__).resolve().parents[2] / "docs"
METHODS = {"sense_local", *ENTRY_ACCEPTED}


def _import(tmp_path: Path, rows: list[dict], *, recovery: bool = True, telemetry=None, language="en") -> Path:
    source = tmp_path / f"source-{recovery}.jsonl"
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    database = tmp_path / f"dictionary-{recovery}.sqlite3"
    import_kaikki_sources([(source, language, "https://kaikki.org/x")], database,
                          content_license="Fixture", telemetry=telemetry, recovery=recovery)
    return database


def _senses(database: Path) -> list[tuple[str, int, str, list[str]]]:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return [
            (lemma, index, definition, json.loads(translations).get("zh-CN", []))
            for lemma, index, definition, translations in connection.execute(
                "SELECT entries.lemma, senses.sense_index, senses.definition, senses.translations_json "
                "FROM senses JOIN entries ON entries.id = senses.entry_id ORDER BY entries.id, senses.sense_index"
            )
        ]


def _provenance(database: Path) -> list[tuple]:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return connection.execute(
            "SELECT entries.lemma, sense_index, translation_provenance.language, value, method, recovered_from "
            "FROM translation_provenance JOIN entries ON entries.id = translation_provenance.entry_id "
            "ORDER BY entries.id, sense_index, value"
        ).fetchall()


# --------------------------------------------------------- pure rule functions

@pytest.mark.parametrize(
    ("glosses", "expected"),
    [
        # parent + subsenses that repeat the parent gloss -> one group led by the parent
        ([["To die."], ["To die.", "Of plants."], ["To die.", "Of machines."]], [0, 0, 0]),
        # sibling subsenses without the bare parent -> grouped under the first
        ([["To die.", "Of plants."], ["To die.", "Of machines."]], [0, 0]),
        # identical text, no subsense nesting -> independent senses, never merged
        ([["To die."], ["To die."]], [0, 1]),
        # two bare parents plus a child -> ambiguous nesting, not merged
        ([["To die."], ["To die."], ["To die.", "Of plants."]], [0, 1, 2]),
        # different parent glosses stay separate
        ([["A bank."], ["A bank.", "Of rivers."], ["A shore."]], [0, 0, 2]),
    ],
)
def test_equivalence_groups_require_explicit_nesting(glosses, expected):
    assert equivalence_groups(glosses) == expected


def test_script_rejection_rules():
    assert chinese_script_rejection("zh-CN", "щён") == "dungan_cyrillic"
    assert chinese_script_rejection("zh-CN", "shjon", ["Dungan"]) == "dungan_cyrillic"
    assert chinese_script_rejection("zh-CN", "yínháng") is None  # approved scope: Dungan only
    assert chinese_script_rejection("zh-CN", "DNA") is None
    assert chinese_script_rejection("zh-CN", "〇") is None and chinese_script_rejection("zh-CN", "\U00030EDE") is None
    assert chinese_script_rejection("zh-CN", "銀行 /银行") is None
    assert chinese_script_rejection("zh-CN", "щён 欲") is None  # contains Han: kept
    assert chinese_script_rejection("de", "щён") is None  # only zh-CN is filtered


def test_single_sense_safety_helpers():
    assert single_sense_entry_is_safe([{"glosses": ["x"]}], 1)
    assert not single_sense_entry_is_safe([{"glosses": ["x"]}, {"tags": ["form-of"]}], 1)
    assert not single_sense_entry_is_safe([{"glosses": ["x"]}, {"glosses": ["y"]}], 2)
    assert single_translation_table(["River  Shore", "river shore"])
    assert not single_translation_table(["", "river shore"])


# ------------------------------------------------------ equivalent-group ties

PARENT_CHILD_DIE = {
    "word": "die", "lang_code": "en", "pos": "verb",
    "senses": [
        {"glosses": ["To stop living."]},
        {"glosses": ["To stop living.", "Of plants: to wither."]},
        {"glosses": ["To stop living.", "Of machines: to stop working."]},
        {"glosses": ["To be very eager."]},
    ],
    "translations": [
        {"code": "zh", "word": "死", "sense": "to stop living"},
        {"code": "zh", "word": "渴望", "sense": "to be very eager"},
    ],
}


def test_tie_inside_parent_child_group_goes_to_the_parent_only(tmp_path):
    strict = _senses(_import(tmp_path, [PARENT_CHILD_DIE], recovery=False))
    repaired_db = _import(tmp_path, [PARENT_CHILD_DIE])
    repaired = _senses(repaired_db)
    assert [values for *_, values in strict] == [[], [], [], ["渴望"]]
    assert [values for *_, values in repaired] == [["死"], [], [], ["渴望"]]  # no re-broadcast to children
    assert ("die", 0, "zh-CN", "死", "equivalent_group", None) in _provenance(repaired_db)


def test_identical_independent_senses_are_not_merged(tmp_path):
    row = {
        "word": "fair", "lang_code": "en", "pos": "noun",
        "senses": [{"glosses": ["A gathering."]}, {"glosses": ["A gathering."]}],
        "translations": [{"code": "zh", "word": "集市", "sense": "gathering"}],
    }
    assert [values for *_, values in _senses(_import(tmp_path, [row]))] == [[], []]


def test_tie_across_different_groups_stays_rejected(tmp_path):
    row = {
        "word": "bank", "lang_code": "en", "pos": "noun",
        "senses": [{"glosses": ["A river edge."]}, {"glosses": ["A river edge.", "Of lakes."]},
                   {"glosses": ["A river crossing."]}],
        "translations": [{"code": "zh", "word": "岸", "sense": "river"}],
    }
    assert [values for *_, values in _senses(_import(tmp_path, [row]))] == [[], [], []]


def test_groups_never_cross_part_of_speech_entries(tmp_path):
    rows = [
        {"word": "run", "lang_code": "en", "pos": "verb",
         "senses": [{"glosses": ["To move fast."]}, {"glosses": ["To manage."]}],
         "translations": [{"code": "zh", "word": "跑"}]},
        {"word": "run", "lang_code": "en", "pos": "noun",
         "senses": [{"glosses": ["To move fast."]}]},
    ]
    stored = _senses(_import(tmp_path, rows))
    # unscoped translation on a two-meaning verb entry: still rejected; the noun is untouched
    assert [values for *_, values in stored] == [[], [], []]


def test_unscoped_translation_on_a_single_group_entry_goes_to_the_parent(tmp_path):
    row = {
        "word": "raven", "lang_code": "en", "pos": "noun",
        "senses": [{"glosses": ["A large black bird."]},
                   {"glosses": ["A large black bird.", "Specifically, Corvus corax."]}],
        "translations": [{"code": "zh", "word": "渡鸦"}],
    }
    database = _import(tmp_path, [row])
    assert [values for *_, values in _senses(database)] == [["渡鸦"], []]
    assert _provenance(database)[0][4] == "equivalent_group"


# --------------------------------------------------------- single-sense fallback

def test_single_sense_fallback_requires_one_raw_sense(tmp_path):
    row = {
        "word": "millisecond", "lang_code": "en", "pos": "noun",
        "senses": [{"glosses": ["One thousandth of a second."]}, {"tags": ["no-gloss"]}],
        "translations": [{"code": "zh", "word": "毫秒", "sense": "unit of time"}],
    }
    # A second raw sense without a gloss: the table may describe it, so no fallback.
    assert _senses(_import(tmp_path, [row]))[0][3] == []
    del row["senses"][1]
    (tmp_path / "single").mkdir()
    database = _import(tmp_path / "single", [row])
    assert _senses(database)[0][3] == ["毫秒"]
    assert _provenance(database) == [("millisecond", 0, "zh-CN", "毫秒", "single_sense_fallback", "single_sense_mismatch")]


def test_single_sense_mismatch_with_several_tables_is_not_recovered(tmp_path):
    row = {
        "word": "bank", "lang_code": "en", "pos": "noun",
        "senses": [{"glosses": ["A financial institution."]}],
        "translations": [{"code": "zh", "word": "银行", "sense": "financial institution"},
                         {"code": "zh", "word": "河岸", "sense": "river shore"}],
    }
    assert _senses(_import(tmp_path, [row]))[0][3] == ["银行"]


@pytest.mark.parametrize(
    "other_table",
    [
        # a second table with no zh/de/en records still proves a second meaning (review H1)
        {"code": "es", "word": "banco", "sense": "financial establishment"},
        # ...as does a table whose only Chinese value is filtered Dungan (review H1)
        {"code": "zh", "word": "щён", "sense": "financial establishment"},
    ],
)
def test_hidden_second_tables_block_mismatch_recovery(tmp_path, other_table):
    row = {
        "word": "bank", "lang_code": "en", "pos": "noun",
        "senses": [{"glosses": ["An institution where one can deposit money."]}],
        "translations": [{"code": "zh", "word": "河岸", "sense": "edge of a river"}, other_table],
    }
    assert _senses(_import(tmp_path, [row]))[0][3] == []
    # Control: with only the zh table the same entry is a single table and recovers.
    (tmp_path / "control").mkdir()
    control = dict(row, translations=row["translations"][:1])
    assert _senses(_import(tmp_path / "control", [control]))[0][3] == ["河岸"]


@pytest.mark.parametrize(
    ("hints", "definition"),
    [
        (["literal", "figurative"], "A large wild cat."),  # generic-label-only hints (review M1)
        (["edge of a river", "money"], "A place for money near the river."),  # weak token (review M1)
    ],
)
def test_weak_or_generic_recovery_requires_a_single_table(tmp_path, hints, definition):
    row = {
        "word": "lion", "lang_code": "en", "pos": "noun",
        "senses": [{"glosses": [definition]}],
        "translations": [{"code": "zh", "word": f"译{index}", "sense": hint} for index, hint in enumerate(hints)],
    }
    assert _senses(_import(tmp_path, [row]))[0][3] == []
    single = dict(row, translations=[{"code": "zh", "word": "狮子", "sense": "literal"}])
    (tmp_path / "single").mkdir()
    assert _senses(_import(tmp_path / "single", [single]))[0][3] == ["狮子"]


def test_verb_style_table_under_a_noun_is_not_recovered_as_mismatch(tmp_path):
    row = {
        "word": "fly", "lang_code": "en", "pos": "noun",
        "senses": [{"glosses": ["A small winged insect."]}],
        "translations": [{"code": "zh", "word": "飞", "sense": "to travel through the air"}],
    }
    assert _senses(_import(tmp_path, [row]))[0][3] == []  # likely misfiled verb table (review M2)
    verb = dict(row, pos="verb", senses=[{"glosses": ["To move through the air with wings."]}])
    (tmp_path / "verb").mkdir()
    assert _senses(_import(tmp_path / "verb", [verb]))[0][3] == ["飞"]


def test_recovered_telemetry_counts_only_values_that_reached_storage(tmp_path):
    row = {
        "word": "many", "lang_code": "en", "pos": "adj",
        "senses": [{"glosses": ["A large number."],
                    "translations": [{"code": "zh", "word": f"多{index}"} for index in range(10)]}],
        "translations": [{"code": "zh", "word": "许多", "sense": "much"}],
    }
    telemetry = ImportTelemetry()
    _import(tmp_path, [row], telemetry=telemetry)
    assert telemetry.report()["single_sense_fallback_recovered_from"] == []  # over limit (review L1)


def test_raw_cause_analysis_matches_importer_decisions(tmp_path):
    import gzip

    from services.dictionary.release_comparison import raw_alignment_causes

    row = {
        "word": "bank", "lang_code": "en", "pos": "noun",
        "senses": [{"glosses": ["An institution where one can deposit money."]}],
        "translations": [{"code": "zh", "word": "河岸", "sense": "edge of river"},
                         {"code": "de", "word": "Bank", "sense": "financial establishment"},
                         {"code": "zh", "word": "щён", "sense": "edge of river"},
                         {"code": "zh", "word": "shjon", "sense": "edge of river", "tags": ["Dungan"]}],
    }
    source = tmp_path / "en.jsonl.gz"
    source.write_bytes(gzip.compress((json.dumps(row, ensure_ascii=False) + "\n").encode()))
    causes = raw_alignment_causes({"en": source}, {"en": {"bank"}})["en"]["bank"]
    assert causes["repaired_decisions"] == {"single_sense_mismatch": 1, "dungan_filtered": 2}  # review M3
    assert _senses(_import(tmp_path, [row]))[0][3] == []

    row = {
        "word": "many", "lang_code": "en", "pos": "adj",
        "senses": [{"glosses": ["A large number."],
                    "translations": [{"code": "zh", "word": f"多{index}"} for index in range(10)]}],
        "translations": [{"code": "zh", "word": "许多", "sense": "much"}],
    }
    telemetry = ImportTelemetry()
    database = _import(tmp_path, [row], telemetry=telemetry)
    assert len(_senses(database)[0][3]) == 10
    entry = telemetry.report()["by_edition_and_target"][0]["entry_level"]
    assert entry["rejected"]["over_limit"] == 1
    assert entry["accepted"]["single_sense_fallback"] == 0


# ------------------------------------------------------------- script filter

def test_dungan_and_romanized_values_are_filtered_and_counted(tmp_path):
    row = {
        "word": "want", "lang_code": "en", "pos": "verb",
        "senses": [{"glosses": ["To desire."],
                    "translations": [{"code": "zh", "word": "щён"}, {"code": "zh", "word": "要"}]}],
        "translations": [{"code": "zh", "word": "ё", "sense": "to desire"},
                         {"code": "zh", "word": "shjon", "sense": "to desire", "tags": ["Dungan"]},
                         {"code": "zh", "word": "yào", "sense": "to desire"},
                         {"code": "zh", "word": "想要", "sense": "to desire"}],
    }
    telemetry = ImportTelemetry()
    repaired = _senses(_import(tmp_path, [row], telemetry=telemetry))
    assert repaired[0][3] == ["要", "yào", "想要"]  # romanization is outside the approved filter
    rejected = {(item["scope"], item["reason"]): item["count"] for item in telemetry.report()["script_rejected"]}
    assert rejected == {("sense_local", "dungan_cyrillic"): 1, ("entry_level", "dungan_cyrillic"): 2}
    strict = _senses(_import(tmp_path, [row], recovery=False))
    assert "щён" in strict[0][3] and "ё" in strict[0][3]  # strict mode reproduces Phase 7A


# ----------------------------------------------- telemetry and provenance

def test_fixture_import_reconciles_and_has_complete_provenance(tmp_path):
    telemetry = ImportTelemetry()
    source_rows = []
    for name, language in (("kaikki_migration_en.jsonl", "en"),):
        source_rows = [json.loads(line) for line in (MIGRATION / name).read_text().splitlines() if line.strip()]
    database = _import(tmp_path, source_rows + [PARENT_CHILD_DIE], telemetry=telemetry)
    totals = telemetry.manifest_telemetry()
    entry = totals["entry_level"]
    assert entry["seen"] == entry["accepted"]["total"] + entry["rejected"]["total"]
    assert entry["accepted"]["total"] == sum(entry["accepted"][key] for key in ENTRY_ACCEPTED)
    assert entry["accepted"]["equivalent_group"] == 1
    sense = totals["sense_local"]
    assert sense["seen"] == sense["retained"] + sense["deduplicated"] + sense["truncated"]
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        stored = sum(
            len(values)
            for (translations,) in connection.execute("SELECT translations_json FROM senses")
            for values in json.loads(translations).values()
        )
        rows = connection.execute("SELECT method FROM translation_provenance").fetchall()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(rows) == stored  # one provenance row per stored value
    assert {method for (method,) in rows} <= METHODS
    example = json.loads((DOCS / "DICTIONARY-BUILD-MANIFEST-v1.example.json").read_text())
    example["statistics"]["alignment_telemetry"] = totals
    validate_manifest_structure(example)


def test_recovery_only_changes_entries_it_is_meant_to_change(tmp_path):
    rows = [json.loads(line) for line in (MIGRATION / "kaikki_migration_en.jsonl").read_text().splitlines() if line.strip()]
    strict = {(lemma, index): values for lemma, index, _, values in _senses(_import(tmp_path, rows, recovery=False))}
    repaired = {(lemma, index): values for lemma, index, _, values in _senses(_import(tmp_path, rows))}
    changed = {key for key in strict if strict[key] != repaired[key]}
    # The synthetic fixture has no nested subsenses and no safe single-sense rejection
    # (house has a gloss-less second sense), so repaired output must equal strict output.
    assert changed == set()
