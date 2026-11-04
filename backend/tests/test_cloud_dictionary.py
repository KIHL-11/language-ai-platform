"""B-3A: cloud dictionary on a synthetic EN/DE fixture; Personal untouched."""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import dictionary_api
from services.auth.identity import (
    AuthenticatedPrincipal,
    DeploymentMode,
    RequestIdentity,
    get_request_identity,
)
from services.dictionary.cloud import (
    SYNTHETIC_FIXTURE_PATH,
    NoPronunciationProvider,
    SyntheticFixtureDictionaryProvider,
    cloud_dictionary_from_environment,
)
from services.dictionary.service import DictionaryService


client = TestClient(app_module.app)


@pytest.fixture
def synthetic():
    return DictionaryService(
        SyntheticFixtureDictionaryProvider(), pronunciation_provider=NoPronunciationProvider()
    )


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.delenv("CLOUD_DICTIONARY_SOURCE", raising=False)
    yield
    app_module.app.dependency_overrides.pop(get_request_identity, None)
    if hasattr(app_module.app.state, "cloud_dictionary_service"):
        delattr(app_module.app.state, "cloud_dictionary_service")


@contextmanager
def _cloud(user_id: str | None = "owner-a"):
    principal = None if user_id is None else AuthenticatedPrincipal(
        user_id=user_id, session_id=f"session-{user_id}", authentication_method="test"
    )
    app_module.app.dependency_overrides[get_request_identity] = lambda: RequestIdentity(
        mode=DeploymentMode.CLOUD, principal=principal
    )
    try:
        yield
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)


class _UntouchableLocalDictionary:
    def __getattr__(self, name):
        raise AssertionError(f"cloud lookup reached the Personal dictionary ({name})")


def _lookup(word, language="en", sentence=""):
    return client.post(
        "/api/dictionary/lookup",
        json={"language": language, "word": word, "sentence": sentence, "include_inflections": True},
    )


# --- the synthetic fixture through the shared DictionaryService -------------


@pytest.mark.parametrize(
    "language,word,sentence,lemma,pos,chinese,cross",
    [
        ("en", "runs", "She runs to the station every morning.", "run", "verb", "跑", "laufen"),
        ("en", "ran", "They ran a small bakery.", "run", "verb", "经营", "führen"),
        ("en", "better", "", "good", "adjective", "好的", "gut"),
        ("en", "Tenants", "", "tenant", "noun", "租户", "Mieter"),
        ("de", "geht", "", "gehen", "verb", "走", "to go"),
        ("de", "Häuser", "", "Haus", "noun", "房子", "house"),
        ("de", "sprach", "", "sprechen", "verb", "说", "to speak"),
    ],
)
def test_words_and_inflections_resolve_with_meanings_and_examples(
    synthetic, language, word, sentence, lemma, pos, chinese, cross
):
    result = synthetic.lookup(language, word, sentence, include_inflections=True)

    assert (result.status, result.lemma, result.part_of_speech) == ("found", lemma, pos)
    assert chinese in result.explanations.chinese.value
    assert cross in result.explanations.cross_language.value
    assert result.explanations.monolingual.status == "available"
    assert result.examples
    assert result.inflections_status == "available"
    assert result.pronunciations == []
    assert result.source_attribution[0].source == "Language AI synthetic fixture"
    assert "synthetic" in result.source_attribution[0].license


def test_context_selects_the_sense_used_in_the_sentence(synthetic):
    river = synthetic.lookup("en", "bank", "We sat on the river bank.")
    money = synthetic.lookup("en", "bank", "I opened an account at the bank.")
    bench = synthetic.lookup("de", "Bank", "Wir sitzen auf der Bank im Park.")

    assert "河岸" in river.explanations.chinese.value
    assert "银行" in money.explanations.chinese.value
    assert "长椅" in bench.explanations.chinese.value


def test_a_multi_sense_word_without_context_is_ambiguous(synthetic):
    assert synthetic.lookup("en", "run", "").status == "ambiguous"


def test_a_form_shared_by_two_lemmas_lists_both(synthetic):
    result = synthetic.lookup("en", "leaves", "He leaves the office at six.")

    assert set(result.lemma_candidates) == {"leaf", "leave"}


def test_phrases_and_unknown_words(synthetic):
    phrase = synthetic.lookup("en", "take part in", "Many students take part in the debate.")
    unknown = synthetic.lookup("de", "Quantencomputer", "")

    assert (phrase.status, phrase.lemma) == ("found", "take part in")
    assert unknown.status == "not_found"


def test_languages_never_cross(synthetic):
    assert synthetic.lookup("de", "run", "").status == "not_found"
    assert synthetic.lookup("en", "gehen", "").status == "not_found"


def test_the_packaged_fixture_is_synthetic_not_a_real_release():
    text = SYNTHETIC_FIXTURE_PATH.read_text(encoding="utf-8")

    assert '"fixture": "language-ai-cloud-synthetic-dictionary"' in text
    assert "kaikki" not in text.casefold() and "wiktionary" not in text.casefold()


# --- the route ---------------------------------------------------------------


def test_cloud_lookup_uses_the_synthetic_source_never_the_local_release(monkeypatch):
    monkeypatch.setenv("CLOUD_DICTIONARY_SOURCE", "synthetic-fixture")
    monkeypatch.setattr(dictionary_api, "dictionary_service", _UntouchableLocalDictionary())

    with _cloud():
        english = _lookup("ran", "en", "She ran to the station.")
        german = _lookup("Häuser", "de", "Die Häuser sind alt.")

    assert english.status_code == german.status_code == 200
    assert (english.json()["lemma"], german.json()["lemma"]) == ("run", "Haus")
    assert english.json()["source_attribution"][0]["source"] == "Language AI synthetic fixture"


@pytest.mark.parametrize("source", ["", "sqlite", "/Users/someone/dictionary.sqlite", "kaikki"])
def test_cloud_without_the_synthetic_source_fails_closed(monkeypatch, source):
    monkeypatch.setenv("CLOUD_DICTIONARY_SOURCE", source)
    monkeypatch.setattr(dictionary_api, "dictionary_service", _UntouchableLocalDictionary())

    with _cloud():
        response = _lookup("run")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_dictionary_not_configured"


def test_cloud_never_accepts_the_local_service_even_if_injected(monkeypatch):
    app_module.app.state.cloud_dictionary_service = dictionary_api.dictionary_service

    with _cloud():
        response = _lookup("run")

    assert response.status_code == 503


def test_cloud_lookup_requires_a_session(monkeypatch):
    monkeypatch.setenv("CLOUD_DICTIONARY_SOURCE", "synthetic-fixture")

    with _cloud(None):
        response = _lookup("run")

    assert response.status_code == 401


def test_personal_desktop_still_uses_its_local_dictionary(monkeypatch, synthetic):
    calls = []

    class LocalStub:
        def lookup(self, *args, **kwargs):
            calls.append(args)
            return synthetic.lookup(*args, **kwargs)

    monkeypatch.setattr(dictionary_api, "dictionary_service", LocalStub())
    monkeypatch.setenv("CLOUD_DICTIONARY_SOURCE", "synthetic-fixture")

    response = _lookup("ran", "en")

    assert response.status_code == 200
    assert calls == [("en", "ran", "")]


def test_environment_helper_only_knows_the_synthetic_source(monkeypatch):
    monkeypatch.setenv("CLOUD_DICTIONARY_SOURCE", "synthetic-fixture")
    assert isinstance(cloud_dictionary_from_environment(), DictionaryService)
    monkeypatch.setenv("CLOUD_DICTIONARY_SOURCE", "anything-else")
    assert cloud_dictionary_from_environment() is None
