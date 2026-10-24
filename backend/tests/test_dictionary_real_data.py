"""Opt-in, read-only smoke tests for an installed Kaikki dictionary.

Run on the Mac with RUN_REAL_DICTIONARY_SMOKE=1. The SQLite provider opens the
configured database with mode=ro; these tests never import or rebuild data.
"""

import json
import os
import re

import pytest

from services.dictionary.provider import SQLiteDictionaryProvider
from services.dictionary.service import DictionaryService, default_dictionary_path


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_DICTIONARY_SMOKE") != "1",
    reason="set RUN_REAL_DICTIONARY_SMOKE=1 to inspect the installed dictionary read-only",
)


@pytest.fixture(scope="module")
def real_dictionary() -> DictionaryService:
    path = default_dictionary_path()
    if not path.is_file():
        pytest.skip(f"dictionary is not installed at {path}")
    return DictionaryService(SQLiteDictionaryProvider(path))


@pytest.mark.parametrize(
    ("language", "surface", "sentence", "expected_lemma"),
    [
        ("en", "go", "We go to work by train.", "go"),
        ("en", "went", "We went home.", "go"),
        ("en", "children", "The children laughed.", "child"),
        ("en", "bank", "We sat beside the river bank.", "bank"),
        ("de", "Hallo", "Hallo, wie geht es dir?", "Hallo"),
        ("de", "ging", "Er ging nach Hause.", "gehen"),
        ("de", "Häuser", "Die Häuser stehen am Fluss.", "Haus"),
        ("de", "herausfinden", "Wir wollen herausfinden, was passiert.", "herausfinden"),
    ],
)
def test_installed_dictionary_lemma_resolution(
    real_dictionary, language, surface, sentence, expected_lemma
):
    entries = real_dictionary.provider.lookup(language, surface, sentence)
    applicable = {
        entry.entry_id
        for entry in real_dictionary._candidate_entries(entries, sentence, surface)
    }
    ranked = real_dictionary._ranked_senses(entries, sentence, surface)
    best_by_entry = {}
    for candidate in ranked:
        best_by_entry.setdefault(candidate.entry.entry_id, candidate)
    evidence = []
    for entry in entries:
        candidate = best_by_entry.get(entry.entry_id)
        evidence.append({
            "lemma": entry.lemma,
            "part_of_speech": entry.part_of_speech,
            "match_kind": entry.match_kind,
            "applicable": entry.entry_id in applicable,
            "lexical_score": candidate.lexical_score if candidate else None,
            "morphology_score": candidate.morphology_score if candidate else None,
            "greeting_score": candidate.greeting_score if candidate else None,
            "noun_context_score": candidate.noun_context_score if candidate else None,
            "selected_definition": candidate.sense.definition if candidate else None,
        })
    result = real_dictionary.lookup(language, surface, sentence)
    print(json.dumps({
        "surface": surface,
        "lemma": result.lemma,
        "lemma_candidates": result.lemma_candidates,
        "part_of_speech": result.part_of_speech,
        "status": result.status,
        "selection_method": result.context.selection_method,
        "definition": result.explanations.monolingual.value,
        "cross_language": result.explanations.cross_language.value,
        "chinese": result.explanations.chinese.value,
        "alternate_sense_count": len(result.alternate_senses),
        "candidate_evidence": evidence,
    }, ensure_ascii=False, sort_keys=True))
    assert (result.lemma or "").casefold() == expected_lemma.casefold()
    assert result.surface_form == surface


def test_installed_dictionary_context_and_translation_quality(real_dictionary):
    bank = real_dictionary.lookup("en", "bank", "We sat beside the river bank.")
    assert "river" in (bank.explanations.monolingual.value or "").casefold()
    assert bank.status == "found"

    greeting = real_dictionary.lookup("de", "Hallo", "Hallo, wie geht es dir?")
    assert greeting.part_of_speech == "phrase"
    greeting_definition = (greeting.explanations.monolingual.value or "").casefold()
    assert any(value in greeting_definition for value in ("grüß", "gruß", "begrüß"))
    assert greeting.status in {"found", "ambiguous"}

    nominal = real_dictionary.lookup("de", "Hallo", "Das Hallo war laut.")
    assert nominal.part_of_speech == "noun"
    assert nominal.explanations.monolingual.value

    for result in (bank, greeting, nominal):
        chinese = result.explanations.chinese.value
        if chinese:
            assert not re.fullmatch(r"[\W\d_\u0400-\u052f]+", chinese)
