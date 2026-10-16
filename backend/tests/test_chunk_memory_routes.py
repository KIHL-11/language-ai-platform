from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app as app_module
from services.api import chunk_api
from services.retrieval.chunk_memory import ChunkMemoryService
from services.retrieval.chunk_store import ChunkStore


client = TestClient(app_module.app)


def service(tmp_path, monkeypatch):
    identifiers = iter(f"route-{index}" for index in range(1, 20))
    value = ChunkMemoryService(
        ChunkStore(tmp_path / "chunk_bank.json"),
        clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
        id_factory=lambda: next(identifiers),
    )
    monkeypatch.setattr(chunk_api, "chunk_memory_service", value)
    return value


def create_payload(**overrides):
    payload = {
        "user_id": "user-api",
        "text": "Would you like to join us?",
        "meaning": "你愿意加入我们吗？",
        "language": "en",
        "source_lesson_id": "lesson-old",
        "source_media_id": "youtube:old",
        "source_stage": "active_recall",
    }
    payload.update(overrides)
    return payload


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
                "chunks": [
                    {
                        "text": "Let's meet tomorrow.",
                        "meaning": "我们明天见吧。",
                    }
                ],
            }
        ],
        "ai": {"provider": "test", "status": "complete"},
    }


def test_create_upsert_and_list_chunk_memory(tmp_path, monkeypatch):
    service(tmp_path, monkeypatch)

    first = client.post("/api/chunks/memory", json=create_payload())
    duplicate = client.post(
        "/api/chunks/memory",
        json=create_payload(text=" would you like to join us! "),
    )
    listed = client.get(
        "/api/chunks/memory",
        params={"user_id": "user-api", "language": "en"},
    )

    assert first.status_code == duplicate.status_code == listed.status_code == 200
    assert first.json()["created"] is True
    assert duplicate.json()["created"] is False
    assert duplicate.json()["chunk"]["id"] == first.json()["chunk"]["id"]
    assert [item["id"] for item in listed.json()["chunks"]] == [
        first.json()["chunk"]["id"]
    ]


def test_store_lesson_chunks_for_user_is_idempotent(tmp_path, monkeypatch):
    service(tmp_path, monkeypatch)
    payload = {
        "user_id": "user-api",
        "lesson": lesson_payload(),
        "source_media_id": "youtube:lesson",
    }

    first = client.post("/api/chunks/memory/from-lesson", json=payload)
    second = client.post("/api/chunks/memory/from-lesson", json=payload)

    assert first.json() == {"added": 1, "total": 1}
    assert second.json() == {"added": 0, "total": 1}


def test_store_lesson_chunks_skips_noise_without_blocking_valid_chunks(
    tmp_path, monkeypatch
):
    value = service(tmp_path, monkeypatch)
    payload = {
        "user_id": "user-api",
        "lesson": lesson_payload(),
        "source_media_id": "youtube:lesson",
    }
    payload["lesson"]["sentences"][0]["chunks"] = [
        {"text": "[music]", "meaning": ""},
        {"text": "&gt;&gt; Sounds good.", "meaning": "好主意。"},
    ]

    response = client.post("/api/chunks/memory/from-lesson", json=payload)

    assert response.status_code == 200
    assert response.json() == {"added": 1, "total": 1}
    assert [chunk.text for chunk in value.list_chunks("user-api")] == [
        "Sounds good."
    ]


def test_direct_memory_save_rejects_invalid_sanitized_text(tmp_path, monkeypatch):
    service(tmp_path, monkeypatch)

    response = client.post(
        "/api/chunks/memory", json=create_payload(text="[music]")
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "chunk text must contain reusable spoken language"
    }


def test_active_recall_endpoint_excludes_current_lesson(tmp_path, monkeypatch):
    service(tmp_path, monkeypatch)
    current = client.post(
        "/api/chunks/memory",
        json=create_payload(text="Current", source_lesson_id="current"),
    ).json()["chunk"]
    prior = client.post(
        "/api/chunks/memory",
        json=create_payload(text="Prior", source_lesson_id="prior"),
    ).json()["chunk"]

    response = client.get(
        "/api/chunks/memory/active-recall",
        params={
            "user_id": "user-api",
            "language": "en",
            "current_lesson_id": "current",
            "limit": 5,
        },
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["chunks"]] == [prior["id"]]
    assert current["id"] != prior["id"]


def test_practice_and_archive_endpoints_are_user_scoped(tmp_path, monkeypatch):
    service(tmp_path, monkeypatch)
    chunk = client.post(
        "/api/chunks/memory", json=create_payload()
    ).json()["chunk"]

    practiced = client.patch(
        f"/api/chunks/memory/{chunk['id']}/practice",
        json={"user_id": "user-api", "outcome": "success"},
    )
    forbidden = client.patch(
        f"/api/chunks/memory/{chunk['id']}/practice",
        json={"user_id": "another-user", "outcome": "failure"},
    )
    archived = client.patch(
        f"/api/chunks/memory/{chunk['id']}",
        json={"user_id": "user-api", "active": False},
    )
    selected = client.get(
        "/api/chunks/memory/active-recall",
        params={"user_id": "user-api", "language": "en"},
    )

    assert practiced.status_code == 200
    assert practiced.json()["practice_count"] == 1
    assert practiced.json()["success_count"] == 1
    assert forbidden.status_code == 404
    assert forbidden.json() == {"detail": "Chunk memory not found"}
    assert archived.json()["active"] is False
    assert selected.json() == {"chunks": []}


def test_chunk_memory_request_validation_and_empty_behavior(tmp_path, monkeypatch):
    service(tmp_path, monkeypatch)

    empty = client.get(
        "/api/chunks/memory/active-recall",
        params={"user_id": "missing", "language": "en"},
    )
    invalid_outcome = client.patch(
        "/api/chunks/memory/missing/practice",
        json={"user_id": "user-api", "outcome": "perfect"},
    )
    punctuation_only = client.post(
        "/api/chunks/memory",
        json=create_payload(text="?!"),
    )
    excessive_limit = client.get(
        "/api/chunks/memory/active-recall",
        params={"user_id": "user-api", "language": "en", "limit": 21},
    )

    assert empty.status_code == 200
    assert empty.json() == {"chunks": []}
    assert invalid_outcome.status_code == 422
    assert punctuation_only.status_code == 422
    assert excessive_limit.status_code == 422


def test_chunk_memory_list_has_bounded_pagination(tmp_path, monkeypatch):
    service(tmp_path, monkeypatch)
    for text in ("One", "Two", "Three"):
        response = client.post(
            "/api/chunks/memory", json=create_payload(text=text)
        )
        assert response.status_code == 200

    page = client.get(
        "/api/chunks/memory",
        params={"user_id": "user-api", "offset": 1, "limit": 1},
    )
    too_large = client.get(
        "/api/chunks/memory",
        params={"user_id": "user-api", "limit": 101},
    )

    assert [chunk["text"] for chunk in page.json()["chunks"]] == ["Two"]
    assert too_large.status_code == 422
