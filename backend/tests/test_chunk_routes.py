from datetime import datetime, timezone

import numpy as np
from fastapi.testclient import TestClient

import app as app_module
from services.api import chunk_api
from services.retrieval.chunk_store import ChunkStore
from services.retrieval.retriever import ChunkRetriever
from services.schemas.retrieval import StoredChunk


client = TestClient(app_module.app)


def lesson_payload():
    return {
        "id": "lesson-api",
        "title": "Plans",
        "source_url": "https://example.com/plans",
        "source_language": "en",
        "level": "B1",
        "source": "subtitles",
        "sentences": [
            {
                "id": 1,
                "text": "Let's meet tomorrow.",
                "chunks": [{"text": "Let's meet tomorrow.", "meaning": "我们明天见吧。"}],
            }
        ],
        "ai": {"provider": "test", "status": "complete"},
    }


def test_from_lesson_stores_existing_lesson_chunks(tmp_path, monkeypatch):
    store = ChunkStore(tmp_path / "chunk_bank.json")
    monkeypatch.setattr(chunk_api, "chunk_store", store)

    response = client.post("/api/chunks/from-lesson", json=lesson_payload())

    assert response.status_code == 200
    assert response.json() == {"added": 1, "total": 1}
    assert store.read_chunks()[0].source_lesson_id == "lesson-api"


def test_search_request_validation_rejects_bad_inputs():
    assert client.post(
        "/api/chunks/search",
        json={"query": "", "source_language": "en", "top_k": 5},
    ).status_code == 422
    assert client.post(
        "/api/chunks/search",
        json={"query": "plans", "source_language": "fr", "top_k": 5},
    ).status_code == 422
    assert client.post(
        "/api/chunks/search",
        json={"query": "plans", "source_language": "en", "top_k": 0},
    ).status_code == 422


def test_search_route_returns_ranked_results(tmp_path, monkeypatch):
    class FakeEmbedder:
        def embed(self, texts):
            vectors = {
                "making plans": [1.0, 0.0],
                "Let's meet tomorrow.": [0.9, 0.1],
                "The train is delayed.": [0.0, 1.0],
            }
            return np.asarray([vectors[text] for text in texts], dtype=float)

    store = ChunkStore(tmp_path / "chunk_bank.json")
    now = datetime.now(timezone.utc)
    store.add_chunks(
        [
            StoredChunk(
                id="plans",
                text="Let's meet tomorrow.",
                meaning="我们明天见吧。",
                source_language="en",
                source_lesson_id="lesson-api",
                source_sentence_id=1,
                created_at=now,
            ),
            StoredChunk(
                id="train",
                text="The train is delayed.",
                meaning="火车晚点了。",
                source_language="en",
                source_lesson_id="lesson-api",
                source_sentence_id=2,
                created_at=now,
            ),
        ]
    )
    monkeypatch.setattr(
        chunk_api,
        "chunk_retriever",
        ChunkRetriever(store, FakeEmbedder()),
    )

    response = client.post(
        "/api/chunks/search",
        json={"query": "making plans", "source_language": "en", "top_k": 1},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["text"] == "Let's meet tomorrow."
    assert response.json()["results"][0]["source_sentence_id"] == 1
    assert response.json()["results"][0]["score"] > 0.9
