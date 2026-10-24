import gzip
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import dictionary_api
from services.api import library_api, study_bank_api
from services.dictionary.provider import (
    ProviderEntry,
    ProviderSense,
    SQLiteDictionaryProvider,
    import_kaikki_sources,
    safe_dictionary_source_url,
    safe_wikimedia_audio_url,
)
from services.dictionary.service import DictionaryService
from scripts.diagnose_dictionary_quality import diagnose


FIXTURES = Path(__file__).parent / "fixtures"
TEST_CONTENT_LICENSE = "Synthetic test fixture; not upstream dictionary content"


class FakeDictionaryProvider:
    configured = True

    def __init__(self, entries: list[ProviderEntry]):
        self.entries = entries

    def lookup(self, language: str, surface: str, sentence: str) -> list[ProviderEntry]:
        return self.entries


class LazyFormsProvider(FakeDictionaryProvider):
    def __init__(self, entries: list[ProviderEntry]):
        super().__init__(entries)
        self.form_requests: list[int] = []

    def forms_for_entry(self, entry_id: int) -> tuple[str, ...]:
        self.form_requests.append(entry_id)
        return (f"form-for-{entry_id}",)


def provider_entry(
    entry_id: int,
    lemma: str,
    language: str,
    definition: str,
    *,
    part_of_speech: str = "noun",
    match_kind: str = "lemma",
    examples: tuple[str, ...] = (),
    translations: dict[str, tuple[str, ...]] | None = None,
    source_url: str | None = None,
) -> ProviderEntry:
    return ProviderEntry(
        entry_id=entry_id,
        language=language,
        lemma=lemma,
        part_of_speech=part_of_speech,
        definition_language=language,
        source_url=source_url or f"https://{language}.wiktionary.org/wiki/{lemma}",
        source_name="Wiktionary data extracted by Wiktextract/Kaikki",
        license=TEST_CONTENT_LICENSE,
        senses=(ProviderSense(
            definition=definition,
            examples=examples,
            translations=translations or {},
        ),),
        forms=(),
        pronunciations=(),
        match_kind=match_kind,
    )


@pytest.fixture()
def dictionary(tmp_path):
    database = tmp_path / "dictionary.sqlite3"
    count = import_kaikki_sources(
        [
            (FIXTURES / "kaikki_en_fixture.jsonl", "en", "https://kaikki.org/dictionary/English/"),
            (FIXTURES / "kaikki_de_fixture.jsonl", "de", "https://kaikki.org/dewiktionary/Deutsch/"),
        ],
        database,
        content_license=TEST_CONTENT_LICENSE,
    )
    assert count == 8
    return DictionaryService(SQLiteDictionaryProvider(database))


def test_english_and_german_lemma_lookup_have_three_source_attributed_levels(dictionary):
    english = dictionary.lookup("en", "go", "We go to work by train.")
    assert (english.lemma, english.part_of_speech) == ("go", "verb")
    assert english.explanations.monolingual.value == "To move from one place to another."
    assert english.explanations.cross_language.value == "gehen"
    assert english.explanations.chinese.value == "去"
    assert english.source_attribution[0].license == TEST_CONTENT_LICENSE

    german = dictionary.lookup("de", "herausfinden", "Wir wollen herausfinden, was passiert.")
    assert (german.lemma, german.part_of_speech) == ("herausfinden", "verb")
    assert german.explanations.monolingual.value.startswith("Etwas entdecken")
    assert german.explanations.cross_language.value == "find out; discover"
    assert german.explanations.chinese.value == "查明; 弄清楚"


@pytest.mark.parametrize(
    ("language", "surface", "sentence", "lemma"),
    [
        ("en", "went", "We went home.", "go"),
        ("en", "running", "She is running.", "run"),
        ("en", "children", "The children laughed.", "child"),
        ("de", "ging", "Er ging nach Hause.", "gehen"),
        ("de", "Häuser", "Die Häuser stehen am Fluss.", "Haus"),
        ("de", "heraus", "Ich finde es heraus.", "herausfinden"),
    ],
)
def test_inflections_and_separated_verbs_use_imported_lexical_forms(
    dictionary, language, surface, sentence, lemma
):
    assert dictionary.lookup(language, surface, sentence).lemma == lemma


@pytest.mark.parametrize(
    ("language", "surface", "sentence", "lemma", "definition"),
    [
        ("en", "went", "We went home.", "go", "To move from one place to another."),
        ("en", "children", "The children laughed.", "child", "A young human being."),
        ("de", "ging", "Er ging nach Hause.", "gehen", "Sich zu Fuß bewegen."),
        ("de", "Häuser", "Die Häuser stehen am Fluss.", "Haus", "Ein Gebäude zum Wohnen."),
    ],
)
def test_source_forms_beat_unrelated_exact_entries_without_suffix_guessing(
    language, surface, sentence, lemma, definition
):
    service = DictionaryService(FakeDictionaryProvider([
        provider_entry(1, surface, language, "An unrelated lexical entry."),
        provider_entry(
            2,
            lemma,
            language,
            definition,
            part_of_speech="verb" if lemma in {"go", "gehen"} else "noun",
            match_kind="inflection",
        ),
    ]))

    result = service.lookup(language, surface, sentence)

    assert result.lemma == lemma
    assert result.surface_form == surface
    assert result.status == "found"
    assert result.context.selection_method == "morphology"


def test_context_ranks_senses_across_entries_instead_of_using_the_first_entry():
    service = DictionaryService(FakeDictionaryProvider([
        provider_entry(
            1,
            "bank",
            "en",
            "A financial institution where people keep money.",
            examples=("She deposited money at the bank.",),
        ),
        provider_entry(
            2,
            "bank",
            "en",
            "The land alongside a river.",
            examples=("We sat beside the river bank.",),
        ),
    ]))

    result = service.lookup("en", "bank", "We sat beside the river bank.")

    assert result.explanations.monolingual.value == "The land alongside a river."
    assert result.context.selection_method == "lexical_overlap"
    assert result.status == "found"
    assert [value.definition for value in result.alternate_senses] == [
        "A financial institution where people keep money."
    ]


def test_display_forms_are_loaded_only_for_the_selected_entry():
    provider = LazyFormsProvider([
        provider_entry(1, "bank", "en", "A financial institution."),
        provider_entry(
            2,
            "bank",
            "en",
            "The land alongside a river.",
            examples=("We sat beside the river bank.",),
        ),
    ])
    service = DictionaryService(provider)

    result = service.lookup("en", "bank", "We sat beside the river bank.")

    assert result.lemma == "bank"
    assert result.inflections == []
    assert provider.form_requests == []

    detailed = service.lookup(
        "en",
        "bank",
        "We sat beside the river bank.",
        include_inflections=True,
    )

    assert detailed.inflections == ["form-for-2"]
    assert provider.form_requests == [2]


def test_duplicate_source_entries_do_not_create_false_ambiguity():
    duplicate = provider_entry(
        1,
        "bank",
        "en",
        "The land alongside a river.",
        examples=("They sat on the bank.",),
    )
    service = DictionaryService(FakeDictionaryProvider([
        duplicate,
        provider_entry(
            2,
            "bank",
            "en",
            "The land alongside a river.",
            examples=("Grass grows on a river bank.",),
        ),
    ]))

    result = service.lookup("en", "bank", "That bank is over there.")

    assert result.status == "found"
    assert result.alternate_senses == []


def test_distinct_unranked_meanings_remain_legitimately_ambiguous():
    service = DictionaryService(FakeDictionaryProvider([
        provider_entry(1, "bank", "en", "A financial institution."),
        provider_entry(2, "bank", "en", "Land at the edge of water."),
    ]))

    result = service.lookup("en", "bank", "That bank is over there.")

    assert result.status == "ambiguous"
    assert result.context.selection_method == "ambiguous"


def test_all_distinct_source_senses_remain_available_as_alternatives():
    base = provider_entry(1, "bank", "en", "placeholder")
    entry = replace(base, senses=tuple(
        ProviderSense(f"Meaning {index}.", (), {}) for index in range(12)
    ) + (ProviderSense("The edge of a river.", (), {}),))
    service = DictionaryService(FakeDictionaryProvider([entry]))

    result = service.lookup("en", "bank", "We sat beside the river bank.")

    assert result.explanations.monolingual.value == "The edge of a river."
    assert len(result.alternate_senses) == 12


def test_sentence_initial_greeting_prefers_interjection_part_of_speech():
    service = DictionaryService(FakeDictionaryProvider([
        provider_entry(1, "Hallo", "de", "Ein männlicher Vorname."),
        provider_entry(
            2,
            "Hallo",
            "de",
            "Eine Grußformel.",
            part_of_speech="intj",
        ),
    ]))

    result = service.lookup("de", "Hallo", "Hallo, wie geht es dir?")

    assert result.part_of_speech == "intj"
    assert result.explanations.monolingual.value == "Eine Grußformel."
    assert result.context.selection_method == "part_of_speech"
    assert result.status == "found"


def test_real_source_phrase_greeting_beats_noun_only_in_greeting_context():
    service = DictionaryService(FakeDictionaryProvider([
        provider_entry(
            1,
            "Hallo",
            "de",
            "Ein lauter Zuruf.",
            part_of_speech="noun",
        ),
        provider_entry(
            2,
            "hallo",
            "de",
            "Eine Begrüßungsformel.",
            part_of_speech="phrase",
        ),
    ]))

    greeting = service.lookup("de", "Hallo", "Hallo, wie geht es dir?")
    noun = service.lookup("de", "Hallo", "Das Hallo war laut.")

    assert greeting.part_of_speech == "phrase"
    assert greeting.explanations.monolingual.value == "Eine Begrüßungsformel."
    assert greeting.context.selection_method == "part_of_speech"
    assert noun.part_of_speech == "noun"
    assert noun.explanations.monolingual.value == "Ein lauter Zuruf."


def test_sentence_initial_punctuation_does_not_promote_unrelated_phrase():
    service = DictionaryService(FakeDictionaryProvider([
        provider_entry(1, "Hallo", "de", "Ein lauter Zuruf."),
        provider_entry(
            2,
            "hallo",
            "de",
            "Eine feste Redewendung.",
            part_of_speech="phrase",
        ),
    ]))

    result = service.lookup("de", "Hallo", "Hallo, das ist ungewöhnlich.")

    assert result.part_of_speech == "noun"


def test_single_word_lookup_rejects_unmatched_multiword_lemma_but_keeps_real_phrase():
    service = DictionaryService(FakeDictionaryProvider([
        provider_entry(1, "go", "en", "To move from one place to another.", part_of_speech="verb"),
        provider_entry(
            2,
            "go jump in the lake",
            "en",
            "A dismissive phrase used when telling someone to go away from work.",
            part_of_speech="phrase",
            match_kind="inflection",
        ),
    ]))

    ordinary = service.lookup("en", "go", "We go to work by train.")
    phrase = service.lookup("en", "go", "You can go jump in the lake.")

    assert ordinary.lemma == "go"
    assert ordinary.lemma_candidates == ["go"]
    assert phrase.lemma == "go jump in the lake"


def test_separated_form_requires_particle_at_clause_boundary(tmp_path):
    input_path = tmp_path / "separated.jsonl"
    values = [
        {
            "word": "gehen",
            "lang_code": "de",
            "pos": "verb",
            "senses": [{"glosses": ["Sich zu Fuß bewegen."]}],
            "forms": [{"form": "ging", "tags": ["past"]}],
        },
        {
            "word": "nachgehen",
            "lang_code": "de",
            "pos": "verb",
            "senses": [{"glosses": ["Eine Sache untersuchen."]}],
            "forms": [{"form": "ging nach", "tags": ["past"]}],
        },
    ]
    input_path.write_text(
        "".join(f"{json.dumps(value)}\n" for value in values),
        encoding="utf-8",
    )
    database = tmp_path / "dictionary.sqlite3"
    import_kaikki_sources(
        [(input_path, "de", "https://kaikki.org/dewiktionary/")],
        database,
        content_license=TEST_CONTENT_LICENSE,
    )
    provider = SQLiteDictionaryProvider(database)

    home = provider.lookup("de", "ging", "Er ging nach Hause.")
    investigation = provider.lookup("de", "ging", "Er ging der Sache nach.")

    assert [(entry.lemma, entry.match_kind) for entry in home] == [
        ("gehen", "inflection")
    ]
    assert [(entry.lemma, entry.match_kind) for entry in investigation] == [
        ("gehen", "inflection"),
        ("nachgehen", "separated_form"),
    ]


def test_greeting_query_retains_late_interjection_without_general_pos_bias(tmp_path):
    input_path = tmp_path / "greeting.jsonl"
    values = [
        {
            "word": "Hallo",
            "lang_code": "de",
            "pos": "noun",
            "senses": [{"glosses": [f"Substantivbedeutung {index}."]}],
        }
        for index in range(25)
    ]
    values.append({
        "word": "Hallo",
        "lang_code": "de",
        "pos": "intj",
        "senses": [{"glosses": ["Eine Grußformel."]}],
    })
    input_path.write_text(
        "".join(f"{json.dumps(value)}\n" for value in values),
        encoding="utf-8",
    )
    database = tmp_path / "dictionary.sqlite3"
    import_kaikki_sources(
        [(input_path, "de", "https://kaikki.org/dewiktionary/")],
        database,
        content_license=TEST_CONTENT_LICENSE,
    )
    service = DictionaryService(SQLiteDictionaryProvider(database))

    greeting = service.lookup("de", "Hallo", "Hallo, wie geht es dir?")
    ordinary_noun = service.lookup("de", "Hallo", "Das Hallo war laut.")

    assert greeting.part_of_speech == "intj"
    assert greeting.explanations.monolingual.value == "Eine Grußformel."
    assert ordinary_noun.part_of_speech == "noun"


def test_alternate_sense_payload_is_bounded():
    base = provider_entry(1, "bank", "en", "placeholder")
    entry = replace(base, senses=tuple(
        ProviderSense(f"Meaning {index}.", (), {}) for index in range(75)
    ))
    service = DictionaryService(FakeDictionaryProvider([entry]))

    result = service.lookup("en", "bank", "That bank is over there.")

    assert len(result.alternate_senses) == 50


def test_quality_diagnostics_are_read_only_and_report_indexed_query_plans(dictionary):
    database = dictionary.provider.path
    before = database.stat()

    results = diagnose(database)

    after = database.stat()
    assert [(value["language"], value["surface"]) for value in results] == [
        ("en", "go"),
        ("en", "went"),
        ("en", "children"),
        ("en", "bank"),
        ("de", "ging"),
        ("de", "Hallo"),
        ("de", "Häuser"),
        ("de", "herausfinden"),
    ]
    assert before.st_size == after.st_size
    assert before.st_mtime_ns == after.st_mtime_ns
    for result in results:
        assert result["provider_elapsed_ms"] >= 0
        assert result["core_cold_elapsed_ms"] >= 0
        assert result["core_hot_elapsed_ms"] >= 0
        assert result["optional_forms_elapsed_ms"] >= 0
        assert result["provider_candidate_count"] <= 40
        assert set(result["queries"]) == {
            "exact", "inflection", "separated_component", "hydration"
        }
        assert all(
            query["count"] >= 0 and query["plan"]
            for name, query in result["queries"].items()
            if name != "hydration"
        )
        assert all(result["queries"]["hydration"].values())


def test_chinese_translation_rejects_cyrillic_and_prefers_source_simplified_variants():
    entry = provider_entry(
        1,
        "bank",
        "en",
        "A financial institution.",
        translations={"zh-CN": ("банк", "銀行／银行", "兒童; 儿童")},
    )
    service = DictionaryService(FakeDictionaryProvider([entry]))

    result = service.lookup("en", "bank", "A bank.")

    assert result.explanations.chinese.value == "银行; 儿童; 銀行"
    assert "банк" not in result.explanations.chinese.value
    assert result.explanations.chinese.source_url == entry.source_url


def test_provider_keeps_source_form_candidates_when_exact_duplicates_hit_limit(tmp_path):
    input_path = tmp_path / "many.jsonl"
    values = [
        {
            "word": "went",
            "lang_code": "en",
            "pos": "noun",
            "senses": [{"glosses": [f"Unrelated entry {index}."]}],
        }
        for index in range(25)
    ]
    values.append({
        "word": "go",
        "lang_code": "en",
        "pos": "verb",
        "senses": [{"glosses": ["To move from one place to another."]}],
        "forms": [{"form": "went", "tags": ["past"]}],
    })
    input_path.write_text(
        "".join(f"{json.dumps(value)}\n" for value in values),
        encoding="utf-8",
    )
    database = tmp_path / "dictionary.sqlite3"
    import_kaikki_sources(
        [(input_path, "en", "https://kaikki.org/dictionary/")],
        database,
        content_license=TEST_CONTENT_LICENSE,
    )

    service = DictionaryService(SQLiteDictionaryProvider(database))
    result = service.lookup("en", "went", "We went home.")

    assert result.lemma == "go"
    assert result.context.selection_method == "morphology"


def test_lemma_resolution_does_not_guess_suffixes_without_a_source_form(dictionary):
    result = dictionary.lookup("en", "inventedworded", "They inventedworded it.")

    assert result.status == "not_found"
    assert result.lemma is None


def test_multiple_meanings_use_context_when_grounded_and_show_ambiguity(dictionary):
    river = dictionary.lookup("en", "bank", "They rested beside the river.")
    assert river.context.selection_method == "lexical_overlap"
    assert river.explanations.monolingual.value == "The land alongside a river."
    assert river.explanations.cross_language.value == "Ufer"
    assert river.explanations.chinese.value == "河岸"
    assert river.alternate_senses[0].definition.startswith("A financial institution")

    unclear = dictionary.lookup("en", "bank", "That bank is over there.")
    assert unclear.status == "ambiguous"
    assert unclear.context.selection_method == "ambiguous"


def test_missing_fields_unknown_words_and_missing_database_are_explicit(dictionary, tmp_path):
    incomplete = dictionary.lookup("en", "coveragegap", "A coveragegap exists.")
    assert incomplete.explanations.cross_language.status == "unavailable"
    assert incomplete.explanations.chinese.status == "unavailable"
    assert all(value.audio_url is None for value in incomplete.pronunciations)

    assert dictionary.lookup("de", "unbekannteswort", "").status == "not_found"
    unavailable = DictionaryService(SQLiteDictionaryProvider(tmp_path / "missing.sqlite3"))
    assert unavailable.lookup("en", "word", "").status == "not_configured"


def test_pronunciation_metadata_and_tts_fallback_are_labeled(dictionary):
    result = dictionary.lookup("en", "go", "We go.")
    recording = next(value for value in result.pronunciations if not value.tts_fallback)
    assert recording.ipa == "/ɡoʊ/"
    assert recording.audio_url.startswith("https://upload.wikimedia.org/")
    assert {value.accent for value in result.pronunciations if value.tts_fallback} == {"en-GB"}

    german = dictionary.lookup("de", "Haus", "Das Haus.")
    assert any(value.accent == "de-DE" and value.tts_fallback for value in german.pronunciations)


def test_audio_allowlist_rejects_non_wikimedia_urls():
    assert safe_wikimedia_audio_url("https://upload.wikimedia.org/a.mp3")
    assert safe_wikimedia_audio_url("http://upload.wikimedia.org/a.mp3") is None
    assert safe_wikimedia_audio_url("https://example.invalid/a.mp3") is None
    assert safe_dictionary_source_url(
        "https://en.wiktionary.org/wiki/go", fallback="https://kaikki.org/"
    ).startswith("https://en.wiktionary.org/")
    assert safe_dictionary_source_url(
        "https://malicious.invalid/go", fallback="https://kaikki.org/"
    ) == "https://kaikki.org/"


def test_lookup_cache_avoids_repeated_provider_reads(dictionary, monkeypatch):
    original = dictionary.provider.lookup
    calls = 0

    def counted(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(dictionary.provider, "lookup", counted)
    dictionary.lookup("en", "went", "We went home.")
    dictionary.lookup("en", "went", "We went home.")
    assert calls == 1


def test_import_failure_is_atomic_and_does_not_replace_existing_index(tmp_path):
    database = tmp_path / "dictionary.sqlite3"
    import_kaikki_sources(
        [(FIXTURES / "kaikki_en_fixture.jsonl", "en", "https://kaikki.org/")],
        database,
        content_license=TEST_CONTENT_LICENSE,
    )
    previous = database.read_bytes()
    malformed = tmp_path / "bad.jsonl"
    malformed.write_text('{"word":', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        import_kaikki_sources(
            [(malformed, "en", "https://kaikki.org/")],
            database,
            content_license=TEST_CONTENT_LICENSE,
        )
    assert database.read_bytes() == previous


def _write_gzip_jsonl(path: Path, values: list[dict]) -> bytes:
    jsonl = "".join(
        f"{json.dumps(value, ensure_ascii=False, separators=(',', ':'))}\n"
        for value in values
    ).encode()
    compressed = gzip.compress(jsonl, mtime=0)
    path.write_bytes(compressed)
    return compressed


def test_gzip_import_streams_and_rejects_entries_from_the_wrong_edition_language(tmp_path):
    english = tmp_path / "english.jsonl.gz"
    german = tmp_path / "german.jsonl.gz"
    database = tmp_path / "dictionary.sqlite3"
    _write_gzip_jsonl(english, [
        {"word": "hello", "lang_code": "en", "senses": [{"glosses": ["A greeting."]}]},
        {"word": "Hallo", "lang_code": "de", "senses": [{"glosses": ["A greeting."]}]},
    ])
    _write_gzip_jsonl(german, [
        {"word": "Haus", "lang_code": "de", "senses": [{"glosses": ["Ein Gebäude."]}]},
        {"word": "house", "lang_code": "en", "senses": [{"glosses": ["A building."]}]},
    ])

    count = import_kaikki_sources(
        [
            (english, "en", "https://kaikki.org/dictionary/"),
            (german, "de", "https://kaikki.org/dewiktionary/"),
        ],
        database,
        content_license=TEST_CONTENT_LICENSE,
    )

    assert count == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT language, definition_language, lemma FROM entries ORDER BY language"
        ).fetchall() == [("de", "de", "Haus"), ("en", "en", "hello")]


@pytest.mark.parametrize("damage", ["truncated", "corrupt"])
def test_invalid_gzip_is_detected_without_replacing_existing_database(tmp_path, damage):
    database = tmp_path / "dictionary.sqlite3"
    import_kaikki_sources(
        [(FIXTURES / "kaikki_en_fixture.jsonl", "en", "https://kaikki.org/")],
        database,
        content_license=TEST_CONTENT_LICENSE,
    )
    previous = database.read_bytes()
    compressed = tmp_path / "damaged.jsonl.gz"
    valid = _write_gzip_jsonl(compressed, [
        {"word": "safe", "lang_code": "en", "senses": [{"glosses": ["Not imported."]}]},
    ])
    compressed.write_bytes(valid[:-8] if damage == "truncated" else b"not a gzip stream")

    with pytest.raises(ValueError, match="Corrupt or incomplete gzip"):
        import_kaikki_sources(
            [(compressed, "en", "https://kaikki.org/dictionary/")],
            database,
            content_license=TEST_CONTENT_LICENSE,
        )

    assert database.read_bytes() == previous
    assert not database.with_suffix(".sqlite3.tmp").exists()


def test_lookup_api_validates_input_and_does_not_touch_learning_stores(dictionary, monkeypatch):
    monkeypatch.setattr(dictionary_api, "dictionary_service", dictionary)
    monkeypatch.setattr(
        study_bank_api.study_bank_store,
        "save_items",
        lambda *_: pytest.fail("Dictionary lookup must not mutate Study Bank/SRS"),
    )
    monkeypatch.setattr(
        library_api.library_store,
        "save_item",
        lambda *_: pytest.fail("Dictionary lookup must not mutate Library progress"),
    )
    client = TestClient(app_module.app)
    response = client.post("/api/dictionary/lookup", json={
        "language": "de",
        "word": "Häuser",
        "sentence": "Die Häuser stehen am Fluss.",
        "target_language": "zh-CN",
    })
    assert response.status_code == 200
    assert response.json()["lemma"] == "Haus"
    assert response.json()["inflections"] == []
    detailed = client.post("/api/dictionary/lookup", json={
        "language": "de",
        "word": "Häuser",
        "sentence": "Die Häuser stehen am Fluss.",
        "target_language": "zh-CN",
        "include_inflections": True,
    })
    assert detailed.status_code == 200
    assert "Häuser" in detailed.json()["inflections"]
    assert client.post("/api/dictionary/lookup", json={
        "language": "de", "word": "!!!", "sentence": ""
    }).status_code == 422


def test_explicit_german_tts_uses_standard_german_voice(monkeypatch):
    captured = {}

    async def fake_synth(text, voice, rate):
        captured.update(text=text, voice=voice, rate=rate)
        return b"audio", []

    monkeypatch.setattr(app_module, "synth", fake_synth)
    app_module._cache.clear()
    response = TestClient(app_module.app).post("/api/tts", json={
        "text": "herausfinden", "accent": "de-DE", "gender": "female", "rate": 1,
    })
    assert response.status_code == 200
    assert captured == {"text": "herausfinden", "voice": "de-DE-KatjaNeural", "rate": 1.0}
    assert response.json()["mime"] == "audio/mpeg"


def test_fixture_is_declared_test_data_not_real_coverage():
    # The real-data smoke is intentionally separate; these fixtures only exercise the
    # documented Wiktextract shape and must never be reported as Kaikki coverage.
    first = json.loads((FIXTURES / "kaikki_en_fixture.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first["word"] == "go"
