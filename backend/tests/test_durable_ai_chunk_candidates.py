from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.ai_chunks.adapter import DeterministicMockChunkAdapter
from services.ai_chunks.postgres import (
    CloudAiChunkOwnerNotProvisionedError,
    CloudAiChunkUnavailableError,
    PostgresOwnerScopedAiChunkRepository,
)
from services.ai_chunks.service import AiChunkValidationError, CloudAiChunkOrganizerService
from services.ai_chunks.store import CandidateConflictError, CandidateNotFoundError
from services.api import ai_chunk_api
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
from services.review.service import ReviewService
from services.schemas.ai_chunks import CandidateDecisionRequest, ChunkSuggestionRequest
from services.schemas.lesson import Lesson
from services.schemas.study_bank import AiTextStudyProvenance, StudyItemInput, StudyItemPatch
from services.study_bank.postgres import PostgresOwnerScopedStudyBankRepository


client = TestClient(app_module.app)
MIGRATIONS = Path(__file__).parents[1] / "migrations" / "postgresql"
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
OWNER_A = "candidate-cloud-a"
OWNER_B = "candidate-cloud-b"


class MutableClock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class CountingMockAdapter(DeterministicMockChunkAdapter):
    def __init__(self):
        self.calls = 0

    def propose(self, **kwargs):
        self.calls += 1
        return super().propose(**kwargs)


def _dsn() -> str:
    value = os.getenv("TEST_POSTGRES_DSN")
    if not value:
        pytest.skip("TEST_POSTGRES_DSN is required for PostgreSQL candidate tests")
    return value


def _migration(dsn: str, filename: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute((MIGRATIONS / filename).read_text(), prepare=False)


def _lesson() -> Lesson:
    return Lesson.model_validate(
        {
            "id": "candidate-lesson",
            "title": "Candidate source",
            "source_url": "https://www.youtube.com/watch?v=candidate_1",
            "source_language": "en",
            "target_language": "zh-CN",
            "level": "B2",
            "source": "subtitles",
            "sentences": [
                {
                    "id": 1,
                    "text": "As far as I know, it depends on the weather.",
                    "translation": "据我所知，这取决于天气。",
                    "start": 1.0,
                    "end": 4.0,
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
    )


def _manual(owner=OWNER_A, *, key="candidate-request-0001", text=None):
    return ChunkSuggestionRequest.model_validate(
        {
            "user_id": owner,
            "source_language": "en",
            "source": {
                "source_type": "manual_text",
                "text": text or "As far as I know, it depends on the weather.",
            },
            "max_candidates": 5,
            "idempotency_key": key,
        }
    )


def _library_request(owner, item_id, *, key="candidate-library-001"):
    return ChunkSuggestionRequest.model_validate(
        {
            "user_id": owner,
            "source_language": "en",
            "source": {
                "source_type": "library_passage",
                "library_item_id": item_id,
                "sentence_ids": [1],
            },
            "idempotency_key": key,
        }
    )


def _decision(candidate, *, action="accept", key="candidate-decision-001", edit=None):
    return CandidateDecisionRequest.model_validate(
        {
            "user_id": OWNER_A,
            "candidate_id": candidate.candidate_id,
            "action": action,
            "expected_version": candidate.version,
            "idempotency_key": key,
            **({"edit": edit} if edit is not None else {}),
        }
    )


@pytest.fixture
def cloud_candidates(tmp_path):
    dsn = _dsn()
    for filename in (
        "0003_owner_scoped_ai_chunk_candidates.down.sql",
        "0002_owner_scoped_study_bank.down.sql",
        "0001_owner_scoped_library.down.sql",
    ):
        _migration(dsn, filename)
    for filename in (
        "0001_owner_scoped_library.up.sql",
        "0002_owner_scoped_study_bank.up.sql",
        "0003_owner_scoped_ai_chunk_candidates.up.sql",
    ):
        _migration(dsn, filename)
    with psycopg.connect(dsn) as connection:
        connection.cursor().executemany(
            "INSERT INTO cloud_users (user_id, created_at) VALUES (%s,%s)",
            [(OWNER_A, NOW), (OWNER_B, NOW)],
        )
    clock = MutableClock()
    study = PostgresOwnerScopedStudyBankRepository(dsn, clock=clock)
    library = PostgresOwnerScopedLibraryRepository(
        dsn, FilesystemMediaObjectStore(tmp_path / "media"), clock=clock
    )
    candidates = PostgresOwnerScopedAiChunkRepository(dsn, study, clock=clock)
    try:
        yield dsn, clock, study, library, candidates
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)
        for name in (
            "cloud_ai_chunk_repository",
            "cloud_study_bank_repository",
            "cloud_library_repository",
        ):
            if hasattr(app_module.app.state, name):
                delattr(app_module.app.state, name)
        for filename in (
            "0003_owner_scoped_ai_chunk_candidates.down.sql",
            "0002_owner_scoped_study_bank.down.sql",
            "0001_owner_scoped_library.down.sql",
        ):
            _migration(dsn, filename)


def _identity(owner):
    return RequestIdentity(
        mode=DeploymentMode.CLOUD,
        principal=AuthenticatedPrincipal(
            user_id=owner,
            session_id=f"session-{owner}",
            authentication_method="test",
        ),
    )


@contextmanager
def _as_owner(owner, candidates, study, library):
    names = (
        "cloud_ai_chunk_repository",
        "cloud_study_bank_repository",
        "cloud_library_repository",
    )
    previous = {
        name: getattr(app_module.app.state, name, None)
        for name in names
    }
    app_module.app.state.cloud_ai_chunk_repository = candidates
    app_module.app.state.cloud_study_bank_repository = study
    app_module.app.state.cloud_library_repository = library
    app_module.app.dependency_overrides[get_request_identity] = lambda: _identity(owner)
    try:
        yield
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)
        for name in names:
            if previous[name] is None:
                delattr(app_module.app.state, name)
            else:
                setattr(app_module.app.state, name, previous[name])


def _service(clock, study, library, candidates, adapter=None):
    return CloudAiChunkOrganizerService(
        library, study, candidates, adapter=adapter, clock=clock
    )


def test_migration_has_owner_constraints_force_rls_and_is_reversible(cloud_candidates):
    dsn, *_ = cloud_candidates
    with psycopg.connect(dsn) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'cloud_ai_chunk%'"
            )
        }
        forced = {
            row[0]
            for row in connection.execute(
                "SELECT relname FROM pg_class WHERE relname LIKE 'cloud_ai_chunk%' AND relforcerowsecurity"
            )
        }
    assert {
        "cloud_ai_chunk_batches",
        "cloud_ai_chunk_candidates",
        "cloud_ai_chunk_evidence",
        "cloud_ai_chunk_generation_requests",
        "cloud_ai_chunk_decisions",
        "cloud_ai_chunk_decision_keys",
    } <= tables
    assert tables <= forced


def test_manual_generation_is_durable_across_repository_restart(cloud_candidates):
    dsn, clock, study, library, candidates = cloud_candidates
    batch = _service(clock, study, library, candidates).suggest(OWNER_A, _manual())
    restarted = PostgresOwnerScopedAiChunkRepository(dsn, study, clock=clock)
    restored = restarted.get_batch(OWNER_A, batch.batch_id)
    assert restored == batch
    assert restored.candidates[0].evidence.source_type == "manual_text"


def test_request_idempotency_is_durable_and_fingerprint_conflicts(cloud_candidates):
    dsn, clock, study, library, candidates = cloud_candidates
    adapter = CountingMockAdapter()
    service = _service(clock, study, library, candidates, adapter)
    first = service.suggest(OWNER_A, _manual())
    restarted = _service(
        clock,
        study,
        library,
        PostgresOwnerScopedAiChunkRepository(dsn, study, clock=clock),
        adapter,
    )
    assert restarted.suggest(OWNER_A, _manual()) == first
    assert adapter.calls == 1
    with pytest.raises(CandidateConflictError):
        restarted.suggest(OWNER_A, _manual(text="Different text"))


def test_expired_generation_lease_can_be_reclaimed_after_restart(cloud_candidates):
    dsn, clock, study, library, candidates = cloud_candidates
    request = _manual()
    fingerprint = candidates.fingerprint(
        request.model_dump(mode="json", exclude={"idempotency_key", "user_id"})
    )
    first = candidates.reserve_generation(OWNER_A, request.idempotency_key, fingerprint)
    with pytest.raises(CandidateConflictError):
        candidates.reserve_generation(OWNER_A, request.idempotency_key, fingerprint)
    clock.advance(seconds=31)
    restarted = PostgresOwnerScopedAiChunkRepository(dsn, study, clock=clock)
    second = restarted.reserve_generation(OWNER_A, request.idempotency_key, fingerprint)
    assert first.lease_token != second.lease_token


def test_parallel_duplicate_generation_invokes_mock_once(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    adapter = CountingMockAdapter()
    service = _service(clock, study, library, candidates, adapter)

    def run():
        try:
            return service.suggest(OWNER_A, _manual())
        except CandidateConflictError:
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: run(), range(4)))
    assert adapter.calls == 1
    assert len({value.batch_id for value in results if value is not None}) == 1


def test_library_source_is_owner_scoped_and_exact_snapshot_is_persisted(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    item = library.create_for_owner(OWNER_A, _lesson())
    batch = _service(clock, study, library, candidates).suggest(
        OWNER_A, _library_request(OWNER_A, item.id)
    )
    evidence = batch.candidates[0].evidence
    assert evidence.library_item_id == item.id
    assert (evidence.lesson_id, evidence.sentence_id, evidence.start, evidence.end) == (
        "candidate-lesson",
        1,
        1.0,
        4.0,
    )
    with pytest.raises(KeyError):
        _service(clock, study, library, candidates).suggest(
            OWNER_B,
            _library_request(OWNER_B, item.id, key="candidate-library-002"),
        )


def test_library_deletion_race_prevents_candidate_persistence(cloud_candidates, monkeypatch):
    _, clock, study, library, candidates = cloud_candidates
    item = library.create_for_owner(OWNER_A, _lesson())
    original = library.get_for_owner

    def delete_after_read(owner, item_id):
        result = original(owner, item_id)
        library.delete_for_owner(owner, item_id)
        return result

    monkeypatch.setattr(library, "get_for_owner", delete_after_read)
    with pytest.raises(CandidateConflictError):
        _service(clock, study, library, candidates).suggest(
            OWNER_A, _library_request(OWNER_A, item.id)
        )
    with psycopg.connect(_dsn()) as connection:
        count = connection.execute("SELECT count(*) FROM cloud_ai_chunk_batches").fetchone()[0]
    assert count == 0


def test_library_deleted_after_generation_blocks_acceptance_atomically(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    item = library.create_for_owner(OWNER_A, _lesson())
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _library_request(OWNER_A, item.id))
    library.delete_for_owner(OWNER_A, item.id)
    with pytest.raises(CandidateConflictError):
        service.decide(OWNER_A, batch.batch_id, _decision(batch.candidates[0]))
    assert study.list_items(OWNER_A, active=None) == []
    assert service.get_batch(OWNER_A, batch.batch_id).candidates[0].state == "proposed"


def test_library_snapshot_change_after_generation_blocks_acceptance(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    item = library.create_for_owner(OWNER_A, _lesson())
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _library_request(OWNER_A, item.id))
    changed = _lesson().model_copy(deep=True)
    changed.sentences[0].text = "This authorized sentence changed."
    updated_item = library.create_for_owner(OWNER_A, changed)
    assert updated_item.id == item.id
    with pytest.raises(CandidateConflictError, match="no longer matches"):
        service.decide(OWNER_A, batch.batch_id, _decision(batch.candidates[0]))
    assert study.list_items(OWNER_A, active=None) == []


def test_accept_is_atomic_and_enters_daily_review(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _manual())
    response = service.decide(OWNER_A, batch.batch_id, _decision(batch.candidates[0]))
    assert response.created is True
    assert response.candidate.state == "accepted"
    assert response.study_item.review.review_count == 0
    assert ReviewService(study, clock=clock).summary(OWNER_A).new_chunks == 1


def test_duplicate_accept_preserves_srs_fields_archive_meaning_and_note(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    original = study.save_items_for_owner(
        OWNER_A,
        [
            StudyItemInput(
                user_id=OWNER_A,
                kind="chunk",
                source_language="en",
                target_language="zh-CN",
                text="as far as i know",
                meaning="learner meaning",
                note="learner note",
                provenance=[
                    AiTextStudyProvenance(
                        source_text="As far as I know.", evidence_start=0, evidence_end=16
                    )
                ],
            )
        ],
    )[0][0]
    rated, _ = study.rate_item(OWNER_A, original.id, "good", 0)
    archived = study.update_item(
        OWNER_A,
        rated.id,
        StudyItemPatch(active=False, expected_revision=rated.revision),
    )
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _manual())
    result = service.decide(OWNER_A, batch.batch_id, _decision(batch.candidates[0]))
    assert result.created is False and result.study_item.active is False
    assert result.study_item.review == archived.review
    assert (result.study_item.meaning, result.study_item.note) == (
        "learner meaning",
        "learner note",
    )


def test_reject_has_no_study_or_review_side_effect(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _manual())
    result = service.decide(
        OWNER_A,
        batch.batch_id,
        _decision(batch.candidates[0], action="reject"),
    )
    assert result.candidate.state == "rejected" and result.study_item is None
    assert study.list_items(OWNER_A, active=None) == []


def test_edit_accept_must_remain_grounded(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _manual())
    with pytest.raises(ValueError):
        service.decide(
            OWNER_A,
            batch.batch_id,
            _decision(
                batch.candidates[0],
                action="edit_and_accept",
                edit={
                    "text": "fabricated phrase",
                    "suggested_meaning_zh": "伪造",
                    "usage_note": "",
                    "example": "",
                },
            ),
        )
    assert study.list_items(OWNER_A, active=None) == []
    assert service.get_batch(OWNER_A, batch.batch_id).candidates[0].state == "proposed"


def test_decision_replay_survives_restart_and_wrong_fingerprint_conflicts(cloud_candidates):
    dsn, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _manual())
    request = _decision(batch.candidates[0])
    first = service.decide(OWNER_A, batch.batch_id, request)
    restarted = _service(
        clock,
        study,
        library,
        PostgresOwnerScopedAiChunkRepository(dsn, study, clock=clock),
    )
    assert restarted.decide(OWNER_A, batch.batch_id, request) == first
    with pytest.raises(CandidateConflictError):
        restarted.decide(
            OWNER_A,
            batch.batch_id,
            request.model_copy(update={"candidate_id": batch.candidates[1].candidate_id}),
        )
    assert len(study.list_items(OWNER_A, active=None)) == 1


def test_decision_idempotency_key_is_bound_to_batch_path(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    first = service.suggest(OWNER_A, _manual(key="candidate-request-path-1"))
    second = service.suggest(OWNER_A, _manual(key="candidate-request-path-2"))
    request = _decision(first.candidates[0], key="candidate-decision-path")
    service.decide(OWNER_A, first.batch_id, request)
    with pytest.raises(CandidateConflictError):
        service.decide(OWNER_A, second.batch_id, request)


def test_simultaneous_decisions_commit_exactly_once(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _manual())
    candidate = batch.candidates[0]
    accept = _decision(candidate, key="candidate-decision-accept")
    reject = _decision(candidate, action="reject", key="candidate-decision-reject")

    def run(request):
        try:
            return service.decide(OWNER_A, batch.batch_id, request).candidate.state
        except CandidateConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        states = list(executor.map(run, [accept, reject]))
    assert states.count("conflict") == 1
    assert len(study.list_items(OWNER_A, active=None)) in {0, 1}
    assert service.get_batch(OWNER_A, batch.batch_id).candidates[0].state in {
        "accepted",
        "rejected",
    }


def test_study_failure_rolls_back_candidate_and_decision(cloud_candidates, monkeypatch):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _manual())
    original = study.save_item_in_transaction

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic transaction failure")

    monkeypatch.setattr(study, "save_item_in_transaction", fail_after_write)
    with pytest.raises(CloudAiChunkUnavailableError):
        service.decide(OWNER_A, batch.batch_id, _decision(batch.candidates[0]))
    assert study.list_items(OWNER_A, active=None) == []
    assert service.get_batch(OWNER_A, batch.batch_id).candidates[0].state == "proposed"


def test_cross_tenant_batch_and_candidate_ids_are_non_enumerating(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _manual())
    with pytest.raises(CandidateNotFoundError):
        service.get_batch(OWNER_B, batch.batch_id)
    with pytest.raises(CandidateNotFoundError):
        service.get_batch(OWNER_B, str(uuid.uuid4()))
    wrong = _decision(batch.candidates[0]).model_copy(update={"user_id": OWNER_B})
    with pytest.raises(CandidateNotFoundError):
        service.decide(OWNER_B, batch.batch_id, wrong)


def test_cross_tenant_api_batch_ids_match_missing_response(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    batch = _service(clock, study, library, candidates).suggest(OWNER_A, _manual())
    with _as_owner(OWNER_B, candidates, study, library):
        foreign = client.get(
            f"/api/ai/chunk-suggestions/{batch.batch_id}",
            params={"user_id": OWNER_B},
        )
        missing = client.get(
            f"/api/ai/chunk-suggestions/{uuid.uuid4()}",
            params={"user_id": OWNER_B},
        )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_database_rls_hides_other_tenant_candidates(cloud_candidates):
    dsn, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    service.suggest(OWNER_A, _manual())
    service.suggest(OWNER_B, _manual(OWNER_B, key="candidate-request-0002"))
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='cloud_candidate_rls_test') THEN
                CREATE ROLE cloud_candidate_rls_test NOLOGIN NOSUPERUSER NOBYPASSRLS;
              END IF;
            END $$
            """
        )
        connection.execute("GRANT USAGE ON SCHEMA public TO cloud_candidate_rls_test")
        connection.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO cloud_candidate_rls_test")
        connection.execute("SET ROLE cloud_candidate_rls_test")
        connection.execute("SELECT set_config('app.current_owner', %s, true)", (OWNER_A,))
        owners = [
            row[0]
            for row in connection.execute(
                "SELECT owner_user_id FROM cloud_ai_chunk_candidates"
            )
        ]
        connection.execute("RESET ROLE")
    assert owners and set(owners) == {OWNER_A}


def test_repository_operates_with_nonsuperuser_nobypassrls_role(cloud_candidates):
    dsn, clock, _, library, _ = cloud_candidates
    role = "cloud_candidate_app_test"
    password = "synthetic-candidate-test-only"
    writable_tables = (
        "cloud_study_items",
        "cloud_study_media_provenance",
        "cloud_ai_chunk_batches",
        "cloud_ai_chunk_candidates",
        "cloud_ai_chunk_evidence",
        "cloud_ai_chunk_generation_requests",
        "cloud_ai_chunk_decisions",
        "cloud_ai_chunk_decision_keys",
    )
    with psycopg.connect(dsn) as connection:
        connection.execute(
            f"""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS;
              END IF;
            END $$
            """
        )
        connection.execute(f"ALTER ROLE {role} PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS")
        connection.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        connection.execute(
            f"GRANT SELECT ON cloud_users,cloud_library_media_assets TO {role}"
        )
        connection.execute(
            f"GRANT SELECT,UPDATE ON cloud_library_items TO {role}"
        )
        connection.execute(
            f"GRANT SELECT,INSERT,UPDATE,DELETE ON {','.join(writable_tables)} TO {role}"
        )
    app_dsn = psycopg.conninfo.make_conninfo(dsn, user=role, password=password)
    app_study = PostgresOwnerScopedStudyBankRepository(app_dsn, clock=clock)
    app_candidates = PostgresOwnerScopedAiChunkRepository(
        app_dsn, app_study, clock=clock
    )
    app_library = PostgresOwnerScopedLibraryRepository(
        app_dsn, library.media_objects, clock=clock
    )
    library_item = library.create_for_owner(OWNER_A, _lesson())
    service = _service(clock, app_study, app_library, app_candidates)
    batch = service.suggest(
        OWNER_A,
        _library_request(OWNER_A, library_item.id, key="candidate-app-role"),
    )
    accepted = service.decide(
        OWNER_A,
        batch.batch_id,
        _decision(batch.candidates[0], key="candidate-app-role-decision"),
    )
    assert accepted.study_item.user_id == OWNER_A
    with pytest.raises(CandidateNotFoundError):
        app_candidates.get_batch(OWNER_B, batch.batch_id)


def test_cloud_api_uses_principal_and_persists_acceptance(cloud_candidates):
    _, _, study, library, candidates = cloud_candidates
    payload = _manual().model_dump(mode="json")
    with _as_owner(OWNER_A, candidates, study, library):
        created = client.post("/api/ai/chunk-suggestions", json=payload)
        batch = created.json()
        decision = client.post(
            f"/api/ai/chunk-suggestions/{batch['batch_id']}/decisions",
            json={
                "user_id": OWNER_A,
                "candidate_id": batch["candidates"][0]["candidate_id"],
                "action": "accept",
                "expected_version": 1,
                "idempotency_key": "candidate-decision-api",
            },
        )
    assert created.status_code == decision.status_code == 200
    assert decision.json()["study_item"]["review"]["review_count"] == 0


def test_cloud_api_rejects_forged_identity_and_missing_storage_before_adapter(
    cloud_candidates, monkeypatch
):
    _, _, study, library, candidates = cloud_candidates
    with _as_owner(OWNER_A, candidates, study, library):
        forged = client.post(
            "/api/ai/chunk-suggestions", json=_manual(OWNER_B).model_dump(mode="json")
        )
    assert forged.status_code == 403

    app_module.app.dependency_overrides[get_request_identity] = lambda: _identity(OWNER_A)
    called = False

    def forbidden():
        nonlocal called
        called = True
        return ai_chunk_api.organizer()

    monkeypatch.setattr(ai_chunk_api, "organizer", forbidden)
    try:
        missing = client.post(
            "/api/ai/chunk-suggestions", json=_manual().model_dump(mode="json")
        )
    finally:
        app_module.app.dependency_overrides.pop(get_request_identity, None)
    assert missing.status_code == 503 and called is False


def test_unprovisioned_owner_fails_before_mock_adapter(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    adapter = CountingMockAdapter()
    service = _service(clock, study, library, candidates, adapter)
    with pytest.raises(CloudAiChunkOwnerNotProvisionedError, match="not provisioned"):
        service.suggest(
            "candidate-cloud-missing",
            _manual("candidate-cloud-missing", key="candidate-missing-001"),
        )
    assert adapter.calls == 0


def test_cloud_german_generation_uses_mock_only(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    adapter = CountingMockAdapter()
    service = _service(clock, study, library, candidates, adapter)
    request = ChunkSuggestionRequest.model_validate(
        {
            "user_id": OWNER_A,
            "source_language": "de",
            "source": {
                "source_type": "manual_text",
                "text": "Auf jeden Fall kommt es darauf an.",
            },
            "idempotency_key": "candidate-german-001",
        }
    )
    batch = service.suggest(OWNER_A, request)
    assert batch.candidates[0].text == "auf jeden fall"
    assert adapter.calls == 1


def test_cloud_non_chinese_library_target_fails_before_adapter(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    lesson = _lesson().model_copy(update={"target_language": "de"})
    item = library.create_for_owner(OWNER_A, lesson)
    adapter = CountingMockAdapter()
    service = _service(clock, study, library, candidates, adapter)
    with pytest.raises(AiChunkValidationError, match="Chinese-target"):
        service.suggest(
            OWNER_A,
            _library_request(OWNER_A, item.id, key="candidate-non-zh-001"),
        )
    assert adapter.calls == 0


def test_expired_batch_is_terminal_and_cannot_be_accepted(cloud_candidates):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _manual())
    clock.advance(days=31)
    with pytest.raises(CandidateConflictError, match="expired"):
        service.decide(OWNER_A, batch.batch_id, _decision(batch.candidates[0]))
    restored = service.get_batch(OWNER_A, batch.batch_id)
    assert restored.status == "expired"
    assert restored.candidates[0].state == "expired"
    assert study.list_items(OWNER_A, active=None) == []


def test_bounded_retention_purges_candidate_history_not_accepted_study_item(
    cloud_candidates,
):
    _, clock, study, library, candidates = cloud_candidates
    service = _service(clock, study, library, candidates)
    batch = service.suggest(OWNER_A, _manual())
    accepted = service.decide(
        OWNER_A, batch.batch_id, _decision(batch.candidates[0])
    )
    clock.advance(days=31)
    assert candidates.expire_due(OWNER_A, limit=1) == 1
    assert service.get_batch(OWNER_A, batch.batch_id).status == "expired"
    clock.advance(days=31)
    assert candidates.expire_due(OWNER_A, limit=1) == 1
    with pytest.raises(CandidateNotFoundError):
        service.get_batch(OWNER_A, batch.batch_id)
    assert study.get_item(OWNER_A, accepted.study_item.id).review.review_count == 0


def test_personal_desktop_candidate_contract_remains_in_memory(tmp_path, monkeypatch):
    # Existing P8-03A local path remains independent of all cloud tables.
    from services.ai_chunks.service import AiChunkOrganizerService
    from services.ai_chunks.store import InMemoryAiChunkStore
    from services.library.store import LearningLibraryStore
    from services.study_bank.store import StudyBankStore

    local = AiChunkOrganizerService(
        LearningLibraryStore(tmp_path / "library.json", media_root=tmp_path / "media"),
        StudyBankStore(tmp_path / "study.json", clock=lambda: NOW),
        store=InMemoryAiChunkStore(),
        clock=lambda: NOW,
    )
    monkeypatch.setattr(ai_chunk_api, "_service", local)
    response = client.post(
        "/api/ai/chunk-suggestions",
        json=_manual("personal-user").model_dump(mode="json"),
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == "personal-user"


def test_personal_library_non_chinese_target_remains_compatible(tmp_path):
    from services.ai_chunks.service import AiChunkOrganizerService
    from services.library.store import LearningLibraryStore
    from services.study_bank.store import StudyBankStore

    library = LearningLibraryStore(
        tmp_path / "library.json", media_root=tmp_path / "media"
    )
    item = library.save_item(_lesson().model_copy(update={"target_language": "de"}))
    service = AiChunkOrganizerService(
        library,
        StudyBankStore(tmp_path / "study.json", clock=lambda: NOW),
        clock=lambda: NOW,
    )
    request = _library_request(
        "personal-user", item.id, key="candidate-personal-non-zh"
    )
    assert service.suggest("personal-user", request).candidates
