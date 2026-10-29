from __future__ import annotations

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
from services.api import study_bank_api
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
from services.library.store import LibraryStoreError
from services.schemas.lesson import Lesson
from services.schemas.study_bank import (
    AiTextStudyProvenance,
    MediaStudyProvenance,
    StudyItemInput,
    StudyItemPatch,
)
from services.study_bank.postgres import (
    CloudStudyBankUnavailableError,
    PostgresOwnerScopedStudyBankRepository,
)
from services.study_bank.store import StudyBankConflictError


client = TestClient(app_module.app)
NOW = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
OWNER_A = "study-owner-a"
OWNER_B = "study-owner-b"
MIGRATIONS = Path(__file__).parents[1] / "migrations" / "postgresql"


def _dsn() -> str:
    value = os.getenv("TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("TEST_POSTGRES_DSN is required for PostgreSQL Study Bank tests")
    return value


def _migration(dsn: str, filename: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            (MIGRATIONS / filename).read_text(encoding="utf-8"), prepare=False
        )


def _lesson() -> Lesson:
    return Lesson.model_validate({
        "id": "owned-lesson",
        "title": "Owned lesson",
        "source_url": "https://www.youtube.com/watch?v=owned_123",
        "source_language": "en",
        "target_language": "zh-CN",
        "level": "B2",
        "source": "subtitles",
        "sentences": [{
            "id": 1,
            "text": "We can figure it out.",
            "translation": "我们能解决。",
            "start": 1.5,
            "end": 4.0,
            "chunks": [{"text": "figure it out", "meaning": "解决"}],
            "ai_status": "ok",
        }],
        "ai": {"provider": "mock", "status": "complete"},
    })


def _manual(owner: str, *, text: str = "figure it out") -> StudyItemInput:
    return StudyItemInput(
        user_id=owner,
        kind="chunk",
        source_language="en",
        target_language="zh-CN",
        text=text,
        meaning="解决",
        usage="solve a problem",
        provenance=[AiTextStudyProvenance(
            source_text=f"We can {text}.", evidence_start=7, evidence_end=7 + len(text)
        )],
    )


def _media(owner: str, library_item_id: str, **updates) -> StudyItemInput:
    values = {
        "source_type": "media",
        "library_item_id": library_item_id,
        "lesson_id": "owned-lesson",
        "source_url": "https://www.youtube.com/watch?v=owned_123",
        "media_title": "Owned lesson",
        "sentence_id": 1,
        "sentence_text": "We can figure it out.",
        "sentence_translation": "我们能解决。",
        "start": 1.5,
        "end": 4.0,
    }
    values.update(updates)
    return StudyItemInput(
        user_id=owner,
        kind="chunk",
        source_language="en",
        target_language="zh-CN",
        text="figure it out",
        meaning="解决",
        provenance=[MediaStudyProvenance.model_validate(values)],
    )


@pytest.fixture
def cloud_repositories(tmp_path):
    dsn = _dsn()
    _migration(dsn, "0002_owner_scoped_study_bank.down.sql")
    _migration(dsn, "0001_owner_scoped_library.down.sql")
    _migration(dsn, "0001_owner_scoped_library.up.sql")
    _migration(dsn, "0002_owner_scoped_study_bank.up.sql")
    with psycopg.connect(dsn) as connection:
        connection.cursor().executemany(
            "INSERT INTO cloud_users (user_id, created_at) VALUES (%s, %s)",
            [(OWNER_A, NOW), (OWNER_B, NOW)],
        )
    study = PostgresOwnerScopedStudyBankRepository(dsn, clock=lambda: NOW)
    library = PostgresOwnerScopedLibraryRepository(
        dsn, FilesystemMediaObjectStore(tmp_path / "media"), clock=lambda: NOW
    )
    try:
        yield dsn, study, library
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)
        for attribute in ("cloud_study_bank_repository", "cloud_library_repository"):
            if hasattr(app_module.app.state, attribute):
                delattr(app_module.app.state, attribute)
        _migration(dsn, "0002_owner_scoped_study_bank.down.sql")
        _migration(dsn, "0001_owner_scoped_library.down.sql")


def _identity(owner: str) -> RequestIdentity:
    return RequestIdentity(
        mode=DeploymentMode.CLOUD,
        principal=AuthenticatedPrincipal(
            user_id=owner, session_id=f"session-{owner}", authentication_method="test"
        ),
    )


@contextmanager
def _as_owner(owner: str, study, library=None):
    previous_study = getattr(app_module.app.state, "cloud_study_bank_repository", None)
    previous_library = getattr(app_module.app.state, "cloud_library_repository", None)
    app_module.app.state.cloud_study_bank_repository = study
    if library is not None:
        app_module.app.state.cloud_library_repository = library
    app_module.app.dependency_overrides[get_request_identity] = lambda: _identity(owner)
    try:
        yield
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)
        if previous_study is None:
            delattr(app_module.app.state, "cloud_study_bank_repository")
        else:
            app_module.app.state.cloud_study_bank_repository = previous_study
        if library is not None:
            if previous_library is None:
                delattr(app_module.app.state, "cloud_library_repository")
            else:
                app_module.app.state.cloud_library_repository = previous_library


def test_migration_is_reversible_and_constraints_are_owner_scoped():
    dsn = _dsn()
    _migration(dsn, "0002_owner_scoped_study_bank.down.sql")
    _migration(dsn, "0001_owner_scoped_library.down.sql")
    _migration(dsn, "0001_owner_scoped_library.up.sql")
    _migration(dsn, "0002_owner_scoped_study_bank.up.sql")
    with psycopg.connect(dsn) as connection:
        constraints = {
            row[0] for row in connection.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid='cloud_study_items'::regclass"
            )
        }
    assert "cloud_study_owner_fk" in constraints
    assert "cloud_study_owner_identity_unique" in constraints
    with psycopg.connect(dsn) as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
        }
        policies = {
            row[0] for row in connection.execute(
                "SELECT policyname FROM pg_policies WHERE schemaname='public'"
            )
        }
    assert "cloud_study_media_provenance" in tables
    assert {"cloud_study_owner_policy", "cloud_study_media_owner_policy"} <= policies
    _migration(dsn, "0002_owner_scoped_study_bank.down.sql")
    _migration(dsn, "0001_owner_scoped_library.down.sql")


def test_two_tenants_have_independent_identity_and_non_enumerating_crud(cloud_repositories):
    _, repository, _ = cloud_repositories
    item_a = repository.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])[0][0]
    item_b = repository.save_items_for_owner(OWNER_B, [_manual(OWNER_B)])[0][0]
    assert item_a.id != item_b.id
    assert [item.id for item in repository.list_items(OWNER_A)] == [item_a.id]
    with pytest.raises(KeyError):
        repository.get_item(OWNER_B, item_a.id)
    with pytest.raises(KeyError):
        repository.update_item(OWNER_B, item_a.id, StudyItemPatch(note="forged"))
    with pytest.raises(KeyError):
        repository.delete_item(OWNER_B, item_a.id)
    assert repository.get_item(OWNER_A, item_a.id).note is None


def test_database_rls_hides_other_owner_rows_from_application_role(cloud_repositories):
    dsn, repository, _ = cloud_repositories
    repository.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])
    repository.save_items_for_owner(OWNER_B, [_manual(OWNER_B)])
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='cloud_study_rls_test') THEN
                    CREATE ROLE cloud_study_rls_test NOLOGIN NOSUPERUSER NOBYPASSRLS;
                END IF;
            END $$
            """
        )
        connection.execute("GRANT USAGE ON SCHEMA public TO cloud_study_rls_test")
        connection.execute("GRANT SELECT ON cloud_study_items TO cloud_study_rls_test")
        connection.execute("SET ROLE cloud_study_rls_test")
        connection.execute("SELECT set_config('app.current_owner', %s, true)", (OWNER_A,))
        owners = [row[0] for row in connection.execute(
            "SELECT owner_user_id FROM cloud_study_items ORDER BY owner_user_id"
        )]
        connection.execute("RESET ROLE")
    assert owners == [OWNER_A]


def test_database_rejects_cross_owner_library_provenance(cloud_repositories):
    dsn, study, library = cloud_repositories
    study_item = study.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])[0][0]
    library_item = library.create_for_owner(OWNER_B, _lesson())
    with psycopg.connect(dsn) as connection:
        connection.execute("SELECT set_config('app.current_owner', %s, true)", (OWNER_A,))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                INSERT INTO cloud_study_media_provenance (
                    owner_user_id, study_item_id, provenance_index,
                    library_item_id, created_at
                ) VALUES (%s, %s, 0, %s, %s)
                """,
                (OWNER_A, study_item.id, library_item.id, NOW),
            )


def test_duplicate_save_preserves_review_archive_and_learner_fields(cloud_repositories):
    _, repository, _ = cloud_repositories
    item = repository.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])[0][0]
    rated, _ = repository.rate_item(OWNER_A, item.id, "good", 0)
    archived = repository.update_item(
        OWNER_A, item.id,
        StudyItemPatch(
            active=False,
            meaning="my meaning",
            note="mine",
            expected_revision=rated.revision,
        ),
    )
    duplicate, created = repository.save_items_for_owner(
        OWNER_A, [_manual(OWNER_A)]
    )
    assert created == 0
    assert duplicate[0].active is False
    assert duplicate[0].review == archived.review
    assert duplicate[0].meaning == "my meaning" and duplicate[0].note == "mine"


def test_revision_and_review_count_conflicts_are_atomic(cloud_repositories):
    _, repository, _ = cloud_repositories
    item = repository.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])[0][0]
    with pytest.raises(StudyBankConflictError):
        repository.update_item(OWNER_A, item.id, StudyItemPatch(note="missing"))
    updated = repository.update_item(
        OWNER_A, item.id, StudyItemPatch(note="first", expected_revision=item.revision)
    )
    with pytest.raises(StudyBankConflictError):
        repository.update_item(
            OWNER_A, item.id, StudyItemPatch(note="stale", expected_revision=item.revision)
        )
    assert repository.get_item(OWNER_A, item.id).note == "first"
    rated, _ = repository.rate_item(OWNER_A, item.id, "good", 0)
    with pytest.raises(StudyBankConflictError):
        repository.rate_item(OWNER_A, item.id, "easy", 0)
    assert repository.get_item(OWNER_A, item.id).review == rated.review
    assert updated.revision < rated.revision


def test_concurrent_duplicate_and_rating_operations_converge(cloud_repositories):
    _, repository, _ = cloud_repositories
    with ThreadPoolExecutor(max_workers=6) as executor:
        saved = list(executor.map(
            lambda _: repository.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])[0][0],
            range(12),
        ))
    assert len({item.id for item in saved}) == 1
    item = repository.list_items(OWNER_A)[0]

    def rate():
        try:
            repository.rate_item(OWNER_A, item.id, "good", 0)
            return "ok"
        except StudyBankConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: rate(), range(2)))
    assert results.count("ok") == 1 and results.count("conflict") == 1
    assert repository.get_item(OWNER_A, item.id).review.review_count == 1


def test_batch_failure_rolls_back_every_insert(cloud_repositories):
    dsn, _, _ = cloud_repositories
    identifiers = iter([str(uuid.uuid4()), "not-a-uuid"])
    repository = PostgresOwnerScopedStudyBankRepository(
        dsn, clock=lambda: NOW, id_factory=lambda: next(identifiers)
    )
    with pytest.raises(CloudStudyBankUnavailableError):
        repository.save_items_for_owner(
            OWNER_A, [_manual(OWNER_A), _manual(OWNER_A, text="take into account")]
        )
    assert repository.list_items(OWNER_A, active=None) == []


def test_cloud_api_authorizes_library_provenance_and_review_flow(cloud_repositories):
    _, study, library = cloud_repositories
    library_item = library.create_for_owner(OWNER_A, _lesson())
    payload = {"items": [_media(OWNER_A, library_item.id).model_dump(mode="json")]}
    with _as_owner(OWNER_A, study, library):
        saved = client.post("/api/study-bank/items/bulk", json=payload)
        summary = client.get("/api/review/summary", params={"user_id": OWNER_A})
        queue = client.get("/api/review/queue", params={"user_id": OWNER_A})
        item = saved.json()["items"][0]
        rated = client.post(
            f"/api/review/items/{item['id']}/rate",
            json={"user_id": OWNER_A, "rating": "good", "expected_review_count": 0},
        )
    assert saved.status_code == 200 and saved.json()["created"] == 1
    assert summary.json()["new_chunks"] == 1
    assert [value["id"] for value in queue.json()["items"]] == [item["id"]]
    assert rated.status_code == 200
    assert rated.json()["interval_seconds"] == 86_400


def test_cross_tenant_api_returns_same_404_for_foreign_and_missing(cloud_repositories):
    _, study, _ = cloud_repositories
    item = study.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])[0][0]
    with _as_owner(OWNER_B, study):
        foreign = client.patch(
            f"/api/study-bank/items/{item.id}",
            params={"user_id": OWNER_B},
            json={"note": "forged", "expected_revision": 1},
        )
        missing = client.patch(
            f"/api/study-bank/items/{uuid.uuid4()}",
            params={"user_id": OWNER_B},
            json={"note": "missing", "expected_revision": 1},
        )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_forged_library_provenance_and_owner_claim_are_rejected(cloud_repositories):
    _, study, library = cloud_repositories
    item_b = library.create_for_owner(OWNER_B, _lesson())
    with _as_owner(OWNER_A, study, library):
        wrong_owner = client.post(
            "/api/study-bank/items/bulk",
            json={"items": [_media(OWNER_A, item_b.id).model_dump(mode="json")]},
        )
        forged_claim = client.post(
            "/api/study-bank/items/bulk",
            json={"items": [_manual(OWNER_B).model_dump(mode="json")]},
        )
    assert wrong_owner.status_code == 404
    assert forged_claim.status_code == 403
    assert study.list_items(OWNER_A, active=None) == []


def test_forged_sentence_snapshot_is_rejected_without_writing(cloud_repositories):
    _, study, library = cloud_repositories
    item = library.create_for_owner(OWNER_A, _lesson())
    with _as_owner(OWNER_A, study, library):
        response = client.post(
            "/api/study-bank/items/bulk",
            json={"items": [_media(OWNER_A, item.id, start=2.0).model_dump(mode="json")]},
        )
    assert response.status_code == 422
    assert study.list_items(OWNER_A, active=None) == []


def test_library_provenance_verifier_failure_is_controlled(
    cloud_repositories, monkeypatch
):
    _, study, library = cloud_repositories
    item = library.create_for_owner(OWNER_A, _lesson())
    monkeypatch.setattr(
        library,
        "get_for_owner",
        lambda *_args: (_ for _ in ()).throw(LibraryStoreError("sensitive driver detail")),
    )
    with _as_owner(OWNER_A, study, library):
        response = client.post(
            "/api/study-bank/items/bulk",
            json={"items": [_media(OWNER_A, item.id).model_dump(mode="json")]},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_library_verifier_unavailable"
    assert "sensitive" not in response.text
    assert study.list_items(OWNER_A, active=None) == []


def test_archived_item_cannot_be_rated_via_cloud_api(cloud_repositories):
    _, study, _ = cloud_repositories
    item = study.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])[0][0]
    study.update_item(
        OWNER_A,
        item.id,
        StudyItemPatch(active=False, expected_revision=item.revision),
    )
    with _as_owner(OWNER_A, study):
        response = client.post(
            f"/api/review/items/{item.id}/rate",
            json={"user_id": OWNER_A, "rating": "good", "expected_review_count": 0},
        )
    assert response.status_code == 409
    assert study.get_item(OWNER_A, item.id).review.review_count == 0


def test_cloud_crud_api_uses_revision_and_owner_scope(cloud_repositories):
    _, study, _ = cloud_repositories
    with _as_owner(OWNER_A, study):
        saved = client.post(
            "/api/study-bank/items/bulk",
            json={"items": [_manual(OWNER_A).model_dump(mode="json")]},
        ).json()["items"][0]
        listed = client.get(
            "/api/study-bank/items", params={"user_id": OWNER_A, "active": True}
        )
        updated = client.patch(
            f"/api/study-bank/items/{saved['id']}",
            params={"user_id": OWNER_A},
            json={"note": "owner note", "expected_revision": saved["revision"]},
        )
        stale = client.patch(
            f"/api/study-bank/items/{saved['id']}",
            params={"user_id": OWNER_A},
            json={"note": "stale", "expected_revision": saved["revision"]},
        )
        deleted = client.delete(
            f"/api/study-bank/items/{saved['id']}",
            params={"user_id": OWNER_A, "expected_revision": updated.json()["revision"]},
        )
    assert [value["id"] for value in listed.json()["items"]] == [saved["id"]]
    assert updated.status_code == 200 and updated.json()["note"] == "owner note"
    assert updated.json()["revision"] == saved["revision"] + 1
    assert stale.status_code == 409
    assert deleted.status_code == 204
    assert study.list_items(OWNER_A, active=None) == []


def test_cloud_update_and_delete_require_current_revision(cloud_repositories):
    _, study, _ = cloud_repositories
    item = study.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])[0][0]
    with _as_owner(OWNER_A, study):
        missing_update = client.patch(
            f"/api/study-bank/items/{item.id}",
            params={"user_id": OWNER_A},
            json={"note": "missing revision"},
        )
        missing_delete = client.delete(
            f"/api/study-bank/items/{item.id}", params={"user_id": OWNER_A}
        )
        updated = client.patch(
            f"/api/study-bank/items/{item.id}",
            params={"user_id": OWNER_A},
            json={"note": "current", "expected_revision": item.revision},
        )
        stale_delete = client.delete(
            f"/api/study-bank/items/{item.id}",
            params={"user_id": OWNER_A, "expected_revision": item.revision},
        )
    assert missing_update.status_code == missing_delete.status_code == 409
    assert updated.status_code == 200 and stale_delete.status_code == 409
    assert study.get_item(OWNER_A, item.id).note == "current"


def test_rating_wins_delete_race_and_stale_delete_cannot_remove_progress(cloud_repositories):
    _, study, _ = cloud_repositories
    item = study.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])[0][0]
    rated, _ = study.rate_item(OWNER_A, item.id, "good", 0)
    with pytest.raises(StudyBankConflictError):
        study.delete_item(OWNER_A, item.id, item.revision)
    assert study.get_item(OWNER_A, item.id).review == rated.review


def test_wrong_owner_and_missing_rating_are_non_enumerating(cloud_repositories):
    _, study, _ = cloud_repositories
    item = study.save_items_for_owner(OWNER_A, [_manual(OWNER_A)])[0][0]
    with _as_owner(OWNER_B, study):
        foreign = client.post(
            f"/api/review/items/{item.id}/rate",
            json={"user_id": OWNER_B, "rating": "good", "expected_review_count": 0},
        )
        missing = client.post(
            f"/api/review/items/{uuid.uuid4()}/rate",
            json={"user_id": OWNER_B, "rating": "good", "expected_review_count": 0},
        )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


@pytest.mark.parametrize(
    "value",
    [
        {"target_language": "de"},
        {"text": "invented expression"},
    ],
)
def test_media_save_rejects_forged_language_or_text(cloud_repositories, value):
    _, study, library = cloud_repositories
    item = library.create_for_owner(OWNER_A, _lesson())
    request = _media(OWNER_A, item.id).model_copy(update=value)
    with _as_owner(OWNER_A, study, library):
        response = client.post(
            "/api/study-bank/items/bulk",
            json={"items": [request.model_dump(mode="json")]},
        )
    assert response.status_code == 422
    assert study.list_items(OWNER_A, active=None) == []


def test_library_delete_during_save_rolls_back_study_item(
    cloud_repositories, monkeypatch
):
    _, study, library = cloud_repositories
    item = library.create_for_owner(OWNER_A, _lesson())
    original = library.get_for_owner

    def delete_after_read(owner, item_id):
        result = original(owner, item_id)
        library.delete_for_owner(owner, item_id)
        return result

    monkeypatch.setattr(library, "get_for_owner", delete_after_read)
    with _as_owner(OWNER_A, study, library):
        response = client.post(
            "/api/study-bank/items/bulk",
            json={"items": [_media(OWNER_A, item.id).model_dump(mode="json")]},
        )
    assert response.status_code == 409
    assert study.list_items(OWNER_A, active=None) == []


def test_later_library_delete_preserves_snapshot_and_allows_new_provenance(
    cloud_repositories
):
    dsn, study, library = cloud_repositories
    first_library = library.create_for_owner(OWNER_A, _lesson())
    first = study.save_items_for_owner(
        OWNER_A, [_media(OWNER_A, first_library.id)]
    )[0][0]
    library.delete_for_owner(OWNER_A, first_library.id)
    with psycopg.connect(dsn) as connection:
        linked = connection.execute(
            """
            SELECT library_item_id FROM cloud_study_media_provenance
            WHERE owner_user_id=%s AND study_item_id=%s
            """,
            (OWNER_A, first.id),
        ).fetchone()
    assert linked[0] is None
    assert first.provenance[0].library_item_id == first_library.id

    second_library = library.create_for_owner(OWNER_A, _lesson())
    merged, created = study.save_items_for_owner(
        OWNER_A, [_media(OWNER_A, second_library.id)]
    )
    assert created == 0
    assert len(merged[0].provenance) == 2


def test_unprovisioned_owner_fails_closed(cloud_repositories):
    _, study, _ = cloud_repositories
    with _as_owner("study-owner-missing", study):
        response = client.get(
            "/api/study-bank/items", params={"user_id": "study-owner-missing"}
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_owner_not_provisioned"


def test_cloud_routes_do_not_touch_personal_json(cloud_repositories, tmp_path, monkeypatch):
    _, study, _ = cloud_repositories
    personal = study_bank_api.StudyBankStore(tmp_path / "personal.json")
    monkeypatch.setattr(study_bank_api, "study_bank_store", personal)
    before = personal.path.read_bytes()
    with _as_owner(OWNER_A, study):
        response = client.post(
            "/api/study-bank/items/bulk",
            json={"items": [_manual(OWNER_A).model_dump(mode="json")]},
        )
    assert response.status_code == 200
    assert personal.path.read_bytes() == before


def test_speaking_provenance_remains_fail_closed(cloud_repositories):
    _, study, _ = cloud_repositories
    payload = _manual(OWNER_A).model_dump(mode="json")
    payload["provenance"] = [{
        "source_type": "speaking",
        "speaking_session_id": "unowned-session",
        "suggestion_index": 0,
        "originating_turn": 1,
        "provider": "gemini-live",
        "mode": "custom",
        "media_title_snapshot": None,
        "evidence_text": "figure it out",
    }]
    with _as_owner(OWNER_A, study):
        response = client.post("/api/study-bank/items/bulk", json={"items": [payload]})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "cloud_speaking_provenance_not_configured"


def test_personal_desktop_store_contract_remains_unchanged(tmp_path, monkeypatch):
    from services.study_bank.store import StudyBankStore

    personal = StudyBankStore(tmp_path / "study.json", clock=lambda: NOW, id_factory=lambda: "local-item")
    monkeypatch.setattr(study_bank_api, "study_bank_store", personal)
    response = client.post(
        "/api/study-bank/items/bulk",
        json={"items": [
            _manual("local-user").model_copy(update={"kind": "word"}).model_dump(mode="json")
        ]},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "local-item"
    assert personal.list_items("local-user")[0].revision == 1
