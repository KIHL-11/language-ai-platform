"""B-2 (Issue #58 item 4): optimistic concurrency for cloud Guided/Listening
progress. Synthetic owners and a disposable PostgreSQL only."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from services.api import library_api
from services.library.postgres import LibraryRevisionConflictError
from services.library.store import LearningLibraryStore
from test_owner_scoped_library import (
    NOW,
    OWNER_A,
    OWNER_B,
    _as_owner,
    _create_via_api,
    _dsn,
    client,
    lesson,
    postgres_repository,  # noqa: F401  (fixture)
)


def _two_sentence_lesson(**updates):
    base = lesson().model_dump(mode="json")
    second = {**base["sentences"][0], "id": 2, "text": "Progress never moves back.",
              "start": 3.5, "end": 6.0}
    return lesson(sentences=[base["sentences"][0], second], **updates)


def _guided(item, **changes):
    return {**item["progress"], **changes}


def _patch(owner, repository, item_id, body, kind="progress"):
    with _as_owner(owner, repository):
        return client.patch(f"/api/library/items/{item_id}/{kind}", json=body)


def _stored(item_id):
    with psycopg.connect(_dsn()) as connection:
        return connection.execute(
            """
            SELECT revision, progress_json, listening_progress_json
            FROM cloud_library_items WHERE item_id = %s
            """,
            (item_id,),
        ).fetchone()


def test_single_learner_progress_flow_advances_revision(postgres_repository):
    item = _create_via_api(OWNER_A, postgres_repository, _two_sentence_lesson())
    assert item["revision"] == 1

    guided = _patch(
        OWNER_A,
        postgres_repository,
        item["id"],
        {**_guided(item, is_training=True, stage_index=1,
                   completed_stages=["active_recall"]), "expected_revision": 1},
    )
    assert guided.status_code == 200, guided.text
    assert guided.json()["revision"] == 2
    listening = _patch(
        OWNER_A,
        postgres_repository,
        item["id"],
        {**item["listening_progress"], "last_position_seconds": 4.0,
         "current_sentence_id": 2, "expected_revision": 2},
        kind="listening-progress",
    )
    assert listening.status_code == 200, listening.text
    with _as_owner(OWNER_A, postgres_repository):
        reloaded = client.get(f"/api/library/items/{item['id']}").json()

    assert reloaded["revision"] == 3
    assert reloaded["progress"]["completed_stages"] == ["active_recall"]
    assert reloaded["listening_progress"]["current_sentence_id"] == 2
    assert "expected_revision" not in reloaded["progress"]
    assert "expected_revision" not in reloaded["listening_progress"]


@pytest.mark.parametrize("kind", ["progress", "listening-progress"])
def test_cloud_write_without_revision_is_refused_without_enumeration(
    postgres_repository, kind
):
    item = _create_via_api(OWNER_A, postgres_repository)
    body = item["progress"] if kind == "progress" else item["listening_progress"]

    own = _patch(OWNER_A, postgres_repository, item["id"], body, kind)
    foreign = _patch(OWNER_B, postgres_repository, item["id"], body, kind)
    missing = _patch(
        OWNER_A, postgres_repository, "00000000-0000-4000-8000-000000000000", body, kind
    )

    for response in (own, foreign, missing):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "library_revision_required"
    assert _stored(item["id"])[0] == 1


@pytest.mark.parametrize("kind", ["progress", "listening-progress"])
def test_stale_revision_is_a_409_and_changes_nothing(postgres_repository, kind):
    item = _create_via_api(OWNER_A, postgres_repository, _two_sentence_lesson())
    advanced = _patch(
        OWNER_A,
        postgres_repository,
        item["id"],
        {**_guided(item, stage_index=1, completed_stages=["active_recall"]),
         "expected_revision": 1},
    ).json()
    before = _stored(item["id"])
    body = (
        _guided(item)  # the stale tab still shows stage 0
        if kind == "progress"
        else {**item["listening_progress"], "last_position_seconds": 1.0}
    )

    response = _patch(
        OWNER_A, postgres_repository, item["id"], {**body, "expected_revision": 1}, kind
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "library_revision_conflict",
        "message": "This Library item changed in another session. Reload it and try again.",
    }
    assert _stored(item["id"]) == before
    assert advanced["revision"] == 2


def test_guided_and_listening_share_one_revision(postgres_repository):
    item = _create_via_api(OWNER_A, postgres_repository)
    first = _patch(
        OWNER_A, postgres_repository, item["id"],
        {**_guided(item, is_training=True), "expected_revision": 1},
    )
    second = _patch(
        OWNER_A, postgres_repository, item["id"],
        {**item["listening_progress"], "expected_revision": 1},
        kind="listening-progress",
    )

    assert first.status_code == 200
    assert second.status_code == 409


def test_concurrent_writers_with_one_revision_have_exactly_one_winner(
    postgres_repository,
):
    item = _create_via_api(OWNER_A, postgres_repository, _two_sentence_lesson())
    barrier = threading.Barrier(8)

    def write(index):
        barrier.wait()
        try:
            return postgres_repository.update_listening_progress_for_owner(
                OWNER_A,
                item["id"],
                # Every position is inside the 6 s lesson, so any writer may win.
                {**item["listening_progress"], "last_position_seconds": index * 0.5},
                expected_revision=1,
            )
        except LibraryRevisionConflictError:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(write, range(8)))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    revision, _, listening = _stored(item["id"])
    assert revision == 2
    assert listening["last_position_seconds"] == winners[0].listening_progress.last_position_seconds


def test_second_writer_waits_for_the_lock_then_conflicts(
    postgres_repository, monkeypatch
):
    """Deterministic: writer A holds the row lock after its revision check;
    writer B with the same revision must block, then fail, never overwrite."""
    item = _create_via_api(OWNER_A, postgres_repository, _two_sentence_lesson())
    locked = threading.Event()
    release = threading.Event()
    real_connect = postgres_repository._connect
    first_thread = None

    class PausingConnection:
        def __init__(self, connection):
            self._connection = connection

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *exc):
            return self._connection.__exit__(*exc)

        def execute(self, sql, *args, **kwargs):
            result = self._connection.execute(sql, *args, **kwargs)
            if threading.current_thread() is first_thread and "FOR UPDATE OF item" in sql:
                locked.set()
                assert release.wait(10)
            return result

    monkeypatch.setattr(
        postgres_repository, "_connect", lambda: PausingConnection(real_connect())
    )
    outcomes = {}

    def writer(name, position):
        try:
            outcomes[name] = postgres_repository.update_listening_progress_for_owner(
                OWNER_A,
                item["id"],
                {**item["listening_progress"], "last_position_seconds": position},
                expected_revision=1,
            ).listening_progress.last_position_seconds
        except LibraryRevisionConflictError:
            outcomes[name] = "conflict"

    first_thread = threading.Thread(target=writer, args=("a", 2.0))
    first_thread.start()
    assert locked.wait(10)
    second = threading.Thread(target=writer, args=("b", 5.0))
    second.start()
    second.join(0.5)
    assert second.is_alive(), "writer B must wait for A's row lock"
    release.set()
    first_thread.join(10)
    second.join(10)

    assert outcomes == {"a": 2.0, "b": "conflict"}
    revision, _, listening = _stored(item["id"])
    assert (revision, listening["last_position_seconds"]) == (2, 2.0)


def test_safe_retry_reloads_then_succeeds_and_replay_never_double_applies(
    postgres_repository,
):
    item = _create_via_api(OWNER_A, postgres_repository, _two_sentence_lesson())
    body = {**_guided(item, stage_index=1, completed_stages=["active_recall"]),
            "expected_revision": 1}
    assert _patch(OWNER_A, postgres_repository, item["id"], body).status_code == 200

    replay = _patch(OWNER_A, postgres_repository, item["id"], body)
    assert replay.status_code == 409
    with _as_owner(OWNER_A, postgres_repository):
        current = client.get(f"/api/library/items/{item['id']}").json()
    retried = _patch(
        OWNER_A,
        postgres_repository,
        item["id"],
        {**_guided(current, stage_index=2,
                   completed_stages=["active_recall", "blind_listening"]),
         "expected_revision": current["revision"]},
    )

    assert retried.status_code == 200
    assert retried.json()["revision"] == 3
    assert _stored(item["id"])[0] == 3


def test_cross_owner_write_is_404_whatever_revision_is_sent(postgres_repository):
    item = _create_via_api(OWNER_A, postgres_repository)
    before = _stored(item["id"])

    for revision in (1, 2, 99):
        response = _patch(
            OWNER_B, postgres_repository, item["id"],
            {**_guided(item, is_training=True), "expected_revision": revision},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Library item not found"
    assert _stored(item["id"]) == before


def test_invalid_progress_with_current_revision_changes_nothing(postgres_repository):
    item = _create_via_api(OWNER_A, postgres_repository)

    invalid = _patch(
        OWNER_A, postgres_repository, item["id"],
        {**_guided(item, stage_index=99), "expected_revision": 1},
    )
    zero = _patch(
        OWNER_A, postgres_repository, item["id"],
        {**_guided(item), "expected_revision": 0},
    )

    assert invalid.status_code == 422
    assert zero.status_code == 422
    assert _stored(item["id"])[0] == 1


def test_resaving_the_lesson_invalidates_open_tabs(postgres_repository):
    item = _create_via_api(OWNER_A, postgres_repository)
    resaved = _create_via_api(OWNER_A, postgres_repository, lesson(title="Regenerated"))

    stale = _patch(
        OWNER_A, postgres_repository, item["id"],
        {**_guided(item, is_training=True), "expected_revision": item["revision"]},
    )

    assert resaved["id"] == item["id"] and resaved["revision"] == 2
    assert stale.status_code == 409


def test_progress_writes_never_touch_other_tables(postgres_repository):
    """Guided/Listening progress is Library-only: Study Bank, SRS and Daily
    Review state live in other tables and keep their own semantics."""
    item = _create_via_api(OWNER_A, postgres_repository)
    statements = []
    real_connect = postgres_repository._connect

    class Recording:
        def __init__(self, connection):
            self._connection = connection

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *exc):
            return self._connection.__exit__(*exc)

        def execute(self, sql, *args, **kwargs):
            statements.append(" ".join(str(sql).split()))
            return self._connection.execute(sql, *args, **kwargs)

    postgres_repository._connect = lambda: Recording(real_connect())
    try:
        postgres_repository.update_progress_for_owner(
            OWNER_A, item["id"], _guided(item, is_training=True), expected_revision=1
        )
    finally:
        postgres_repository._connect = real_connect

    writes = [s for s in statements if s.split()[0] in {"UPDATE", "INSERT", "DELETE"}]
    assert writes and all("cloud_library_items" in s for s in writes)


def test_personal_desktop_contract_is_unchanged(tmp_path, monkeypatch):
    personal = LearningLibraryStore(
        tmp_path / "personal-library.json",
        tmp_path / "personal-library-media",
        tmp_path / "personal-source-media",
        clock=lambda: NOW,
    )
    monkeypatch.setattr(library_api, "library_store", personal)
    saved = client.post(
        "/api/library/items", json={"lesson": lesson().model_dump(mode="json")}
    ).json()

    plain = client.patch(
        f"/api/library/items/{saved['id']}/progress",
        json={**saved["progress"], "is_training": True},
    )
    with_revision = client.patch(
        f"/api/library/items/{saved['id']}/listening-progress",
        json={**saved["listening_progress"], "mode": "blind", "expected_revision": 7},
    )

    assert plain.status_code == 200 and with_revision.status_code == 200
    for response in (plain, with_revision):
        assert "revision" not in response.json()
    stored = json.loads(personal.path.read_text(encoding="utf-8"))
    [record] = stored["items"]
    assert "revision" not in record
    assert "expected_revision" not in record["progress"]
    assert "expected_revision" not in record["listening_progress"]
    assert record["progress"]["is_training"] is True
    assert record["listening_progress"]["mode"] == "blind"
