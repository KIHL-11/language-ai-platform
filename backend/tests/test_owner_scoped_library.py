from __future__ import annotations

import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import library_api
from services.auth.identity import (
    AuthenticatedPrincipal,
    DeploymentMode,
    RequestIdentity,
    get_request_identity,
)
from services.library.postgres import (
    FilesystemMediaObjectStore,
    PostgresOwnerScopedLibraryRepository,
)
from services.library.store import LearningLibraryStore
from services.schemas.lesson import Lesson


client = TestClient(app_module.app)
NOW = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)
OWNER_A = "synthetic-owner-a"
OWNER_B = "synthetic-owner-b"
MISSING_OWNER = "synthetic-owner-missing"
MIGRATIONS = Path(__file__).parents[1] / "migrations" / "postgresql"


def lesson(**updates) -> Lesson:
    payload = {
        "id": "cloud-lesson",
        "title": "Synthetic cloud lesson",
        "source_url": "https://www.youtube.com/watch?v=tenant_123",
        "source_language": "en",
        "target_language": "zh-CN",
        "level": "B2",
        "audio_url": None,
        "source": "subtitles",
        "sentences": [
            {
                "id": 1,
                "text": "Tenant data stays isolated.",
                "translation": "租户数据保持隔离。",
                "start": 0.5,
                "end": 3.5,
                "keywords": [],
                "chunks": [],
                "grammar": "",
                "ai_status": "ok",
            }
        ],
        "training_order": [
            "active_recall",
            "blind_listening",
            "comprehension",
            "mini_dictation",
            "shadowing",
            "retell",
            "live_dialogue",
        ],
        "ai": {"provider": "mock", "status": "complete"},
    }
    payload.update(updates)
    return Lesson.model_validate(payload)


def _dsn() -> str:
    value = os.getenv("TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("TEST_POSTGRES_DSN is required for the owner-scoped adapter tests")
    return value


def _execute_migration(dsn: str, filename: str) -> None:
    sql = (MIGRATIONS / filename).read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql, prepare=False)


def _provision_owner(dsn: str, owner_user_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO cloud_users (user_id, created_at) VALUES (%s, %s)",
            (owner_user_id, NOW),
        )


@pytest.fixture
def postgres_repository(tmp_path):
    dsn = _dsn()
    _execute_migration(dsn, "0001_owner_scoped_library.down.sql")
    _execute_migration(dsn, "0001_owner_scoped_library.up.sql")
    _provision_owner(dsn, OWNER_A)
    _provision_owner(dsn, OWNER_B)
    repository = PostgresOwnerScopedLibraryRepository(
        dsn,
        FilesystemMediaObjectStore(tmp_path / "cloud-media"),
        clock=lambda: NOW,
    )
    try:
        yield repository
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)
        if hasattr(app_module.app.state, "cloud_library_repository"):
            delattr(app_module.app.state, "cloud_library_repository")
        _execute_migration(dsn, "0001_owner_scoped_library.down.sql")


def _identity(owner_user_id: str) -> RequestIdentity:
    return RequestIdentity(
        mode=DeploymentMode.CLOUD,
        principal=AuthenticatedPrincipal(
            user_id=owner_user_id,
            session_id=f"session-{owner_user_id}",
            authentication_method="synthetic-test",
        ),
    )


@contextmanager
def _as_owner(owner_user_id: str, repository):
    missing = object()
    previous_repository = getattr(
        app_module.app.state, "cloud_library_repository", missing
    )
    app_module.app.state.cloud_library_repository = repository
    app_module.app.dependency_overrides[get_request_identity] = (
        lambda: _identity(owner_user_id)
    )
    try:
        yield
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)
        if previous_repository is missing:
            delattr(app_module.app.state, "cloud_library_repository")
        else:
            app_module.app.state.cloud_library_repository = previous_repository


def _create_via_api(owner_user_id: str, repository, value: Lesson | None = None):
    with _as_owner(owner_user_id, repository):
        response = client.post(
            "/api/library/items",
            json={"lesson": (value or lesson()).model_dump(mode="json")},
        )
    assert response.status_code == 200, response.text
    return response.json()


def test_postgres_migration_is_reversible():
    dsn = _dsn()
    _execute_migration(dsn, "0001_owner_scoped_library.down.sql")
    _execute_migration(dsn, "0001_owner_scoped_library.up.sql")
    with psycopg.connect(dsn) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name LIKE 'cloud_%'
                """
            )
        }
    assert tables == {
        "cloud_users",
        "cloud_library_items",
        "cloud_library_media_assets",
        "cloud_media_cleanup_outbox",
    }
    _execute_migration(dsn, "0001_owner_scoped_library.down.sql")
    with psycopg.connect(dsn) as connection:
        remaining = connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE 'cloud_%'
            """
        ).fetchone()[0]
    assert remaining == 0


def test_real_postgres_adapter_scopes_uniqueness_and_crud_by_owner(
    postgres_repository,
):
    first = postgres_repository.create_for_owner(OWNER_A, lesson())
    same_owner = postgres_repository.create_for_owner(
        OWNER_A, lesson(title="Regenerated")
    )
    other_owner = postgres_repository.create_for_owner(OWNER_B, lesson())

    assert same_owner.id == first.id
    assert same_owner.lesson.title == "Regenerated"
    assert other_owner.id != first.id
    assert [item.id for item in postgres_repository.list_for_owner(OWNER_A)] == [
        first.id
    ]
    assert [item.id for item in postgres_repository.list_for_owner(OWNER_B)] == [
        other_owner.id
    ]

    with pytest.raises(KeyError):
        postgres_repository.get_for_owner(OWNER_B, first.id)
    with pytest.raises(KeyError):
        postgres_repository.update_progress_for_owner(
            OWNER_B,
            first.id,
            first.progress.model_copy(update={"is_training": True}).model_dump(),
        )
    with pytest.raises(KeyError):
        postgres_repository.delete_for_owner(OWNER_B, first.id)

    assert postgres_repository.get_for_owner(OWNER_A, first.id).lesson.title == "Regenerated"


def test_concurrent_upserts_keep_one_item_per_owner(postgres_repository):
    with ThreadPoolExecutor(max_workers=8) as executor:
        items = list(
            executor.map(
                lambda _: postgres_repository.create_for_owner(OWNER_A, lesson()),
                range(16),
            )
        )

    assert len({item.id for item in items}) == 1
    assert len(postgres_repository.list_for_owner(OWNER_A)) == 1
    assert postgres_repository.list_for_owner(OWNER_B) == []


def test_cloud_api_list_create_update_delete_are_non_enumerating(
    postgres_repository,
):
    owner_a = _create_via_api(OWNER_A, postgres_repository)
    owner_b = _create_via_api(OWNER_B, postgres_repository)
    assert owner_a["id"] != owner_b["id"]

    with _as_owner(OWNER_B, postgres_repository):
        listed = client.get("/api/library/items", headers={"X-User-Id": OWNER_A})
        fetched = client.get(f"/api/library/items/{owner_a['id']}")
        progress = client.patch(
            f"/api/library/items/{owner_a['id']}/progress",
            json=owner_a["progress"],
        )
        listening = client.patch(
            f"/api/library/items/{owner_a['id']}/listening-progress",
            json=owner_a["listening_progress"],
        )
        deleted = client.delete(f"/api/library/items/{owner_a['id']}")

    assert [item["id"] for item in listed.json()["items"]] == [owner_b["id"]]
    assert {fetched.status_code, progress.status_code, listening.status_code, deleted.status_code} == {404}
    assert postgres_repository.get_for_owner(OWNER_A, owner_a["id"])

    own_progress = {
        **owner_a["progress"],
        "is_training": True,
        "stage_index": 1,
        "completed_stages": ["active_recall"],
    }
    own_listening = {
        **owner_a["listening_progress"],
        "last_position_seconds": 2.0,
        "current_sentence_id": 1,
        "mode": "blind",
    }
    with _as_owner(OWNER_A, postgres_repository):
        progress = client.patch(
            f"/api/library/items/{owner_a['id']}/progress", json=own_progress
        )
        listening = client.patch(
            f"/api/library/items/{owner_a['id']}/listening-progress",
            json=own_listening,
        )
    assert progress.status_code == 200
    assert progress.json()["progress"]["stage_index"] == 1
    assert listening.status_code == 200
    assert listening.json()["listening_progress"]["mode"] == "blind"


def test_database_rejects_cross_owner_media_foreign_key(postgres_repository):
    item = postgres_repository.create_for_owner(OWNER_A, lesson())
    dsn = _dsn()

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(dsn) as connection:
            connection.execute(
                """
                INSERT INTO cloud_library_media_assets (
                    owner_user_id, media_id, library_item_id, asset_kind,
                    object_key, filename, content_type, byte_size, sha256,
                    status, created_at, updated_at
                ) VALUES (
                    %s, gen_random_uuid(), %s, 'audio', %s, 'audio.mp3',
                    'audio/mpeg', 1, %s, 'ready', %s, %s
                )
                """,
                (
                    OWNER_B,
                    item.id,
                    "owners/" + "a" * 64 + "/library/" + item.id
                    + "/audio-" + "b" * 32 + ".mp3",
                    "c" * 64,
                    NOW,
                    NOW,
                ),
            )


def test_audio_metadata_and_bytes_require_same_owner(postgres_repository):
    item_a = postgres_repository.create_for_owner(OWNER_A, lesson())
    item_b = postgres_repository.create_for_owner(
        OWNER_B,
        lesson(source_url="https://youtu.be/tenant_456"),
    )
    postgres_repository.put_audio_for_owner(
        OWNER_A,
        item_a.id,
        filename="reference.mp3",
        content_type="audio/mpeg",
        content=b"owner-a-audio",
    )
    postgres_repository.put_audio_for_owner(
        OWNER_B,
        item_b.id,
        filename="reference.mp3",
        content_type="audio/mpeg",
        content=b"owner-b-audio",
    )

    with _as_owner(OWNER_A, postgres_repository):
        own = client.get(f"/api/library/items/{item_a.id}/audio")
        cross = client.get(f"/api/library/items/{item_b.id}/audio")
        direct = client.get("/media/reference.mp3")

    assert own.status_code == 200
    assert own.content == b"owner-a-audio"
    assert cross.status_code == 404
    assert direct.status_code == 503
    assert direct.json()["detail"]["code"] == "cloud_tenant_storage_not_configured"


def test_audio_read_rejects_metadata_with_wrong_owner_namespace(
    postgres_repository,
):
    item = postgres_repository.create_for_owner(OWNER_A, lesson())
    mismatched_key = postgres_repository.media_objects.put_audio(
        OWNER_B, item.id, "reference.mp3", b"must-not-leak"
    )
    with psycopg.connect(_dsn()) as connection:
        connection.execute(
            """
            INSERT INTO cloud_library_media_assets (
                owner_user_id, media_id, library_item_id, asset_kind,
                object_key, filename, content_type, byte_size, sha256,
                status, created_at, updated_at
            ) VALUES (
                %s, gen_random_uuid(), %s, 'audio', %s, 'audio.mp3',
                'audio/mpeg', %s, %s, 'ready', %s, %s
            )
            """,
            (
                OWNER_A,
                item.id,
                mismatched_key,
                len(b"must-not-leak"),
                hashlib.sha256(b"must-not-leak").hexdigest(),
                NOW,
                NOW,
            ),
        )

    with _as_owner(OWNER_A, postgres_repository):
        response = client.get(f"/api/library/items/{item.id}/audio")

    assert response.status_code == 500
    assert response.content != b"must-not-leak"


def test_cloud_delete_removes_only_authorized_metadata_and_object(postgres_repository):
    item = postgres_repository.create_for_owner(OWNER_A, lesson())
    postgres_repository.put_audio_for_owner(
        OWNER_A,
        item.id,
        filename="reference.wav",
        content_type="audio/wav",
        content=b"synthetic-wave",
    )
    audio_path = postgres_repository.audio_for_owner(OWNER_A, item.id).path

    with _as_owner(OWNER_B, postgres_repository):
        assert client.delete(f"/api/library/items/{item.id}").status_code == 404
    assert audio_path.exists()

    with _as_owner(OWNER_A, postgres_repository):
        assert client.delete(f"/api/library/items/{item.id}").status_code == 204
    assert not audio_path.exists()
    with pytest.raises(KeyError):
        postgres_repository.get_for_owner(OWNER_A, item.id)


def test_delete_failure_keeps_durable_cleanup_outbox(
    monkeypatch, postgres_repository
):
    item = postgres_repository.create_for_owner(OWNER_A, lesson())
    postgres_repository.put_audio_for_owner(
        OWNER_A,
        item.id,
        filename="reference.mp3",
        content_type="audio/mpeg",
        content=b"cleanup-evidence",
    )
    audio_path = postgres_repository.audio_for_owner(OWNER_A, item.id).path

    def fail_delete(*_args):
        from services.library.store import LibraryStoreError

        raise LibraryStoreError("synthetic object-store failure")

    monkeypatch.setattr(postgres_repository.media_objects, "delete", fail_delete)
    with _as_owner(OWNER_A, postgres_repository):
        response = client.delete(f"/api/library/items/{item.id}")

    assert response.status_code == 204
    assert audio_path.exists()
    with psycopg.connect(_dsn()) as connection:
        pending = connection.execute(
            """
            SELECT owner_user_id, library_item_id, status, attempt_count
            FROM cloud_media_cleanup_outbox
            """
        ).fetchone()
    assert pending == (OWNER_A, uuid.UUID(item.id), "pending", 1)


def test_database_unavailable_is_a_redacted_controlled_503(tmp_path):
    repository = PostgresOwnerScopedLibraryRepository(
        "postgresql://secret-user:secret-password@127.0.0.1:1/secret-db"
        "?connect_timeout=1",
        FilesystemMediaObjectStore(tmp_path / "unavailable-media"),
    )
    with _as_owner(OWNER_A, repository):
        response = client.get("/api/library/items")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "cloud_tenant_storage_unavailable",
        "message": "Cloud tenant Library storage is unavailable.",
    }
    assert "secret" not in response.text


def test_unprovisioned_principal_fails_closed(postgres_repository):
    with _as_owner(MISSING_OWNER, postgres_repository):
        listed = client.get("/api/library/items")
        response = client.post(
            "/api/library/items",
            json={"lesson": lesson().model_dump(mode="json")},
        )

    assert listed.status_code == 503
    assert listed.json()["detail"]["code"] == "cloud_owner_not_provisioned"
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_owner_not_provisioned"


def test_missing_cloud_repository_never_falls_back_to_personal_store(monkeypatch):
    class BombStore:
        def list_summaries(self):
            raise AssertionError("Personal Library must not be read in cloud mode")

    monkeypatch.setattr(library_api, "library_store", BombStore())
    app_module.app.dependency_overrides[get_request_identity] = lambda: _identity(OWNER_A)
    try:
        response = client.get("/api/library/items")
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_tenant_storage_not_configured"


def test_cloud_requests_do_not_mutate_personal_json_or_claim_unowned_media(
    tmp_path, monkeypatch, postgres_repository
):
    personal = LearningLibraryStore(
        tmp_path / "personal-library.json",
        tmp_path / "personal-library-media",
        tmp_path / "personal-source-media",
        clock=lambda: NOW,
    )
    personal.save_item(lesson(title="Personal record"))
    original_json = personal.path.read_bytes()
    monkeypatch.setattr(library_api, "library_store", personal)
    cloud_lesson = lesson(audio_url="/media/unowned.mp3")

    created = _create_via_api(OWNER_A, postgres_repository, cloud_lesson)

    assert personal.path.read_bytes() == original_json
    assert created["persistent_audio"]["status"] == "unavailable"
    assert created["lesson"]["audio_url"] is None
    assert not (tmp_path / "personal-library-media" / created["id"]).exists()


@pytest.mark.parametrize(
    "path",
    [
        "/api/library/items/not-owned/study-candidates?user_id=synthetic-owner-a",
        "/api/library/items/not-owned/study-bank/words/status"
        "?user_id=synthetic-owner-a&sentence_id=1&word=tenant",
    ],
)
def test_unmigrated_library_provenance_routes_remain_fail_closed(
    path, postgres_repository
):
    with _as_owner(OWNER_A, postgres_repository):
        response = client.get(path)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_tenant_storage_not_configured"


def test_no_local_to_cloud_import_endpoint_is_exposed():
    paths = {route.path for route in app_module.app.routes}
    assert not any(path.startswith("/api/account/import") for path in paths)
