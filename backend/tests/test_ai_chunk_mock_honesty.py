"""CH-01 Step 1: the offline mock never invents a candidate from whatever text
comes first, and every batch response says how it was proposed (CH-00 R1)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.ai_chunks.adapter import DeterministicMockChunkAdapter, ProposedChunk, SourceSegment
from services.ai_chunks.service import AiChunkOrganizerService
from services.ai_chunks.store import InMemoryAiChunkStore
from services.api import ai_chunk_api
from services.schemas.ai_chunks import ChunkSuggestionRequest
from services.schemas.lesson import Lesson
from test_ai_chunk_organizer import NOW, USER_A, manual_request, organizer  # noqa: F401  (fixture)
from test_durable_ai_chunk_candidates import (  # noqa: F401  (fixture)
    OWNER_A,
    _as_owner,
    cloud_candidates,
)


client = TestClient(app_module.app)

# Synthetic 40-sentence transcripts. The filler holds none of the mock's
# built-in phrases; the useful phrases sit at the start, middle and end.
FILLER = {
    "en": "Today we talk about item number {n} in the studio.",
    "de": "Heute sprechen wir im Studio über Punkt Nummer {n}.",
}
PLACED = {
    "en": {1: "As far as I know, the studio opens early.",
           20: "Honestly, it depends on the guest and the topic.",
           38: "We look forward to your questions next week."},
    "de": {1: "Auf jeden Fall beginnen wir pünktlich.",
           20: "Es kommt darauf an, wer heute zu Gast ist.",
           38: "Zum Schluss: Sie dürfen sich freuen auf die nächste Folge."},
}
EXPECTED = {
    "en": {"as far as i know": 1, "it depends on": 20, "look forward to": 38},
    "de": {"auf jeden fall": 1, "es kommt darauf an": 20, "sich freuen auf": 38},
}


def _transcript(language, placed=True):
    texts = [FILLER[language].format(n=n) for n in range(1, 41)]
    if placed:
        for sentence_id, text in PLACED[language].items():
            texts[sentence_id - 1] = text
    return texts


def _segments(texts):
    return [SourceSegment(segment_id=f"sentence:{n}", text=text) for n, text in enumerate(texts, start=1)]


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_mock_proposes_nothing_rather_than_the_first_words(language):
    result = DeterministicMockChunkAdapter().propose(
        language=language, segments=_segments(_transcript(language, placed=False)), max_candidates=8,
    )

    assert result == []


@pytest.mark.parametrize("language", ["en", "de"])
def test_phrases_in_the_middle_and_end_are_found_like_the_first(language):
    result = DeterministicMockChunkAdapter().propose(
        language=language, segments=_segments(_transcript(language)), max_candidates=8,
    )

    found = {value.text: int(value.evidence_segment_id.split(":")[1]) for value in result}
    assert found == EXPECTED[language]


@pytest.mark.xfail(strict=True, reason=(
    "Pre-existing, reported, out of Step 1 scope: normalize_phrase casefolds "
    "'ß' to 'ss' but the built-in key keeps 'ß', so it can never match."
))
def test_the_mock_matches_im_grossen_und_ganzen():
    texts = _transcript("de", placed=False)
    texts[19] = "Im Großen und Ganzen war es eine gute Folge."

    result = DeterministicMockChunkAdapter().propose(language="de", segments=_segments(texts), max_candidates=8)

    assert [value.text for value in result] == ["im großen und ganzen"]


def test_moving_a_phrase_to_the_end_does_not_drop_it():
    texts = _transcript("en", placed=False)
    texts[39] = "Honestly, it depends on the guest and the topic."

    [only] = DeterministicMockChunkAdapter().propose(language="en", segments=_segments(texts), max_candidates=8)

    assert (only.text, only.evidence_segment_id) == ("it depends on", "sentence:40")


def test_a_manual_text_without_known_phrases_gives_an_empty_unknown_batch(organizer):
    service, _, _ = organizer
    ai_chunk_api._service = service
    try:
        created = client.post("/api/ai/chunk-suggestions", json=manual_request(
            text="Today we talk about item number one in the studio.",
        ).model_dump(mode="json"))
        read = client.get(f"/api/ai/chunk-suggestions/{created.json()['batch_id']}", params={"user_id": USER_A})
    finally:
        ai_chunk_api._service = None

    assert created.status_code == 200
    assert created.json()["candidates"] == []
    # R-M11: an empty batch records no generation source, so it is unknown.
    assert created.json()["proposal_mode"] == "unknown"
    assert read.json()["proposal_mode"] == "unknown"


def test_a_library_passage_takes_a_late_phrase_and_never_the_first_sentence(tmp_path):
    from services.library.store import LearningLibraryStore
    from services.study_bank.store import StudyBankStore

    texts = _transcript("en", placed=False)[:12]
    texts[11] = "We look forward to your questions next week."
    library = LearningLibraryStore(tmp_path / "library.json", media_root=tmp_path / "media", clock=lambda: NOW)
    item = library.save_item(Lesson.model_validate({
        "id": "honesty-lesson", "title": "Synthetic podcast", "source_url": "https://example.invalid/podcast",
        "source_language": "en", "target_language": "zh-CN", "level": "B2", "source": "subtitles",
        "sentences": [{"id": n, "text": text, "translation": "", "start": float(n), "end": n + 0.9}
                      for n, text in enumerate(texts, start=1)],
        "training_order": ["active_recall", "blind_listening", "comprehension", "mini_dictation",
                           "shadowing", "retell", "live_dialogue"],
        "ai": {"provider": "mock", "status": "complete"},
    }))
    service = AiChunkOrganizerService(library, StudyBankStore(tmp_path / "study.json", clock=lambda: NOW),
                                      store=InMemoryAiChunkStore(), clock=lambda: NOW)

    batch = service.suggest(USER_A, ChunkSuggestionRequest.model_validate({
        "user_id": USER_A, "source_language": "en",
        "source": {"source_type": "library_passage", "library_item_id": item.id, "sentence_ids": list(range(1, 13))},
        "max_candidates": 8, "idempotency_key": "honesty-library-01",
    }))

    assert [(value.text, value.evidence.sentence_id) for value in batch.candidates] == [("look forward to", 12)]


class _StubModelAdapter:
    model_id = "stub-model"
    proposal_version = "1"

    def propose(self, *, language, segments, max_candidates):
        return [ProposedChunk(text="look forward to", evidence_segment_id=segments[0].segment_id)]


def test_an_untrusted_non_mock_adapter_is_labelled_unknown(tmp_path):
    from services.library.store import LearningLibraryStore
    from services.study_bank.store import StudyBankStore

    service = AiChunkOrganizerService(
        LearningLibraryStore(tmp_path / "library.json", media_root=tmp_path / "media", clock=lambda: NOW),
        StudyBankStore(tmp_path / "study.json", clock=lambda: NOW),
        adapter=_StubModelAdapter(), store=InMemoryAiChunkStore(), clock=lambda: NOW,
    )

    batch = service.describe(service.suggest(USER_A, manual_request(text="We look forward to it.")))

    assert batch.proposal_mode == "unknown"  # R-M11: no model id is trusted yet
    assert [value.text for value in batch.candidates] == ["look forward to"]


def test_empty_cloud_batches_are_labelled_unknown(cloud_candidates):
    _, _, study, library, candidates = cloud_candidates
    item = library.create_for_owner(OWNER_A, Lesson.model_validate({
        "id": "honesty-cloud", "title": "Synthetic cloud podcast", "source_url": "https://example.invalid/cloud",
        "source_language": "en", "target_language": "zh-CN", "level": "B2", "source": "subtitles",
        "sentences": [{"id": 1, "text": "Today we talk about the studio.", "translation": "", "start": 0.0, "end": 2.0}],
        "training_order": ["active_recall", "blind_listening", "comprehension", "mini_dictation",
                           "shadowing", "retell", "live_dialogue"],
        "ai": {"provider": "mock", "status": "complete"},
    }))
    body = {
        "user_id": OWNER_A, "source_language": "en", "max_candidates": 8, "idempotency_key": "honesty-cloud-0001",
        "source": {"source_type": "library_passage", "library_item_id": item.id, "sentence_ids": [1]},
    }

    with _as_owner(OWNER_A, candidates, study, library):
        created = client.post("/api/ai/chunk-suggestions", json=body)
        read = client.get(f"/api/ai/chunk-suggestions/{created.json()['batch_id']}", params={"user_id": OWNER_A})

    assert created.status_code == 200
    assert (created.json()["candidates"], created.json()["proposal_mode"]) == ([], "unknown")
    assert read.json()["proposal_mode"] == "unknown"
