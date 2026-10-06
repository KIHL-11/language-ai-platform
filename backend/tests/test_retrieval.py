from datetime import datetime, timezone

import numpy as np
import pytest

from services.retrieval.chunk_store import ChunkStore, ChunkStoreError
from services.retrieval.retriever import ChunkRetriever, add_lesson_chunks
from services.schemas.lesson import Lesson
from services.schemas.retrieval import StoredChunk


def stored_chunk(
    chunk_id: str,
    text: str,
    *,
    language: str = "en",
    lesson_id: str = "lesson-1",
    sentence_id: int = 1,
) -> StoredChunk:
    return StoredChunk(
        id=chunk_id,
        text=text,
        meaning=f"Meaning of {text}",
        source_language=language,
        source_lesson_id=lesson_id,
        source_sentence_id=sentence_id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class FakeEmbedder:
    def __init__(self, vectors):
        self.vectors = vectors

    def embed(self, texts):
        return np.asarray([self.vectors[text] for text in texts], dtype=float)


def lesson_payload() -> Lesson:
    return Lesson.model_validate(
        {
            "id": "lesson-42",
            "title": "Making plans",
            "source_url": "https://example.com/lesson",
            "source_language": "en",
            "level": "B2",
            "source": "subtitles",
            "sentences": [
                {
                    "id": 1,
                    "text": "Are you free this weekend?",
                    "chunks": [
                        {"text": "Are you free this weekend?", "meaning": "你这周末有空吗？"},
                        {"text": "How about Saturday?", "meaning": "周六怎么样？"},
                    ],
                }
            ],
            "ai": {"provider": "test", "status": "complete"},
        }
    )


def test_empty_chunk_bank_is_created_and_returns_no_chunks(tmp_path):
    path = tmp_path / "chunk_bank.json"

    store = ChunkStore(path)

    assert path.exists()
    assert store.read_chunks() == []


def test_saving_chunks_persists_and_ignores_same_provenance_duplicate(tmp_path):
    path = tmp_path / "chunk_bank.json"
    store = ChunkStore(path)
    first = stored_chunk("chunk-1", "Are you free this weekend?")
    duplicate = stored_chunk("chunk-2", "  are you free this weekend?  ")

    added = store.add_chunks([first, duplicate])

    assert added == [first]
    assert ChunkStore(path).read_chunks() == [first]


def test_same_text_from_different_sentence_keeps_both_provenance_records(tmp_path):
    store = ChunkStore(tmp_path / "chunk_bank.json")
    first = stored_chunk("chunk-1", "Sounds good.", sentence_id=1)
    second = stored_chunk("chunk-2", "Sounds good.", sentence_id=2)

    assert store.add_chunks([first, second]) == [first, second]


def test_malformed_chunk_bank_raises_clear_error(tmp_path):
    path = tmp_path / "chunk_bank.json"
    path.write_text("{not-json", encoding="utf-8")
    store = ChunkStore(path)

    with pytest.raises(ChunkStoreError, match="Malformed chunk bank JSON"):
        store.read_chunks()


def test_retrieval_filters_language_honors_top_k_and_orders_by_cosine(tmp_path):
    store = ChunkStore(tmp_path / "chunk_bank.json")
    store.add_chunks(
        [
            stored_chunk("plan", "Are you free this weekend?"),
            stored_chunk("meet", "Let's meet tomorrow.", sentence_id=2),
            stored_chunk("train", "The train is delayed.", sentence_id=3),
            stored_chunk("german", "Hast du dieses Wochenende Zeit?", language="de"),
        ]
    )
    embedder = FakeEmbedder(
        {
            "making plans with a friend": [1.0, 0.0],
            "Are you free this weekend?": [0.95, 0.05],
            "Let's meet tomorrow.": [0.8, 0.2],
            "The train is delayed.": [0.0, 1.0],
        }
    )
    retriever = ChunkRetriever(store, embedder)

    results = retriever.retrieve_chunks(
        "making plans with a friend", source_language="en", top_k=2
    )

    assert [result.id for result in results] == ["plan", "meet"]
    assert len(results) == 2
    assert all(result.source_language == "en" for result in results)
    assert results[0].score > results[1].score


def test_add_lesson_chunks_preserves_lesson_sentence_and_language(tmp_path):
    store = ChunkStore(tmp_path / "chunk_bank.json")

    added = add_lesson_chunks(lesson_payload(), store)

    assert len(added) == 2
    assert {chunk.source_lesson_id for chunk in added} == {"lesson-42"}
    assert {chunk.source_sentence_id for chunk in added} == {1}
    assert {chunk.source_language for chunk in added} == {"en"}

