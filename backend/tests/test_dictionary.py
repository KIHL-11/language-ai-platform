import gzip
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import dictionary_api
from services.api import library_api, study_bank_api
from services.dictionary.provider import (
    SQLiteDictionaryProvider,
    import_kaikki_sources,
    safe_dictionary_source_url,
    safe_wikimedia_audio_url,
)
from services.dictionary.service import DictionaryService


FIXTURES = Path(__file__).parent / "fixtures"
TEST_CONTENT_LICENSE = "Synthetic test fixture; not upstream dictionary content"


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
