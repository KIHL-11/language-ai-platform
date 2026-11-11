"""B-1 (Issue #58): media read integrity, cleanup outbox worker, replacement.

Synthetic audio bytes and a disposable PostgreSQL only.
"""
from __future__ import annotations

import hashlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
import pytest

from services.library import cleanup_worker, postgres as library_postgres
from services.library.postgres import (
    MAX_CLEANUP_ATTEMPTS,
    UPLOAD_CLEANUP_GRACE,
    MediaUploadAbortedError,
)
from services.library.store import LibraryStoreError
from test_owner_scoped_library import (
    NOW,
    OWNER_A,
    OWNER_B,
    _as_owner,
    _dsn,
    _execute_migration,
    _provision_owner,
    client,
    lesson,
    postgres_repository,  # noqa: F401  (fixture)
)


AUDIO = b"RIFF-synthetic-audio-" + bytes(range(256)) * 64


def _item_with_audio(repository, owner=OWNER_A, content=AUDIO, source="tenant_123"):
    item = repository.create_for_owner(
        owner, lesson(source_url=f"https://youtu.be/{source}")
    )
    repository.put_audio_for_owner(
        owner,
        item.id,
        filename="reference.mp3",
        content_type="audio/mpeg",
        content=content,
    )
    return item


def _object_path(repository, owner, item_id):
    with repository.audio_for_owner(owner, item_id) as audio:
        return audio.path


def _outbox():
    with psycopg.connect(_dsn()) as connection:
        return connection.execute(
            """
            SELECT object_key, status, attempt_count, next_attempt_at,
                   last_error_code
            FROM cloud_media_cleanup_outbox ORDER BY created_at, cleanup_id
            """
        ).fetchall()


def _at(repository, delta):
    repository.clock = lambda: NOW + delta


class SimulatedCrash(BaseException):
    """Process death: no except-Exception handler may absorb it."""


def _failing_delete(*_args):
    raise LibraryStoreError("synthetic object-store failure")


# --- read integrity -------------------------------------------------------


def _get_audio(repository, item_id, owner=OWNER_A, headers=None):
    with _as_owner(owner, repository):
        return client.get(f"/api/library/items/{item_id}/audio", headers=headers)


def _assert_integrity_failure(response, secret=AUDIO[:64]):
    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "cloud_media_integrity_failed",
        "message": "Library audio failed an integrity check.",
    }
    assert secret not in response.content
    assert "owners/" not in response.text


def test_verified_audio_is_served_with_content_etag(postgres_repository):
    item = _item_with_audio(postgres_repository)

    response = _get_audio(postgres_repository, item.id)

    assert response.status_code == 200
    assert response.content == AUDIO
    assert response.headers["etag"] == f'"{hashlib.sha256(AUDIO).hexdigest()}"'
    assert response.headers["accept-ranges"] == "bytes"


def test_same_size_tamper_is_not_served(postgres_repository):
    item = _item_with_audio(postgres_repository)
    path = _object_path(postgres_repository, OWNER_A, item.id)
    tampered = bytearray(AUDIO)
    tampered[len(tampered) // 2] ^= 0xFF
    path.write_bytes(bytes(tampered))

    _assert_integrity_failure(_get_audio(postgres_repository, item.id), bytes(tampered[:64]))


@pytest.mark.parametrize("content", [AUDIO[:-1], AUDIO + b"x"])
def test_truncated_or_extended_object_is_not_served(postgres_repository, content):
    item = _item_with_audio(postgres_repository)
    _object_path(postgres_repository, OWNER_A, item.id).write_bytes(content)

    _assert_integrity_failure(_get_audio(postgres_repository, item.id))


def test_object_swapped_with_another_items_bytes_is_not_served(postgres_repository):
    item_a = _item_with_audio(postgres_repository)
    item_b = _item_with_audio(
        postgres_repository, OWNER_B, content=b"owner-b-private-audio", source="b_1"
    )
    path_a = _object_path(postgres_repository, OWNER_A, item_a.id)
    path_b = _object_path(postgres_repository, OWNER_B, item_b.id)
    os.replace(path_b, path_a)

    _assert_integrity_failure(
        _get_audio(postgres_repository, item_a.id), b"owner-b-private-audio"
    )


def test_symlink_at_object_key_is_not_followed(postgres_repository):
    item_a = _item_with_audio(postgres_repository)
    item_b = _item_with_audio(
        postgres_repository, OWNER_B, content=AUDIO, source="b_2"
    )
    path_a = _object_path(postgres_repository, OWNER_A, item_a.id)
    path_b = _object_path(postgres_repository, OWNER_B, item_b.id)
    path_a.unlink()
    # Identical bytes, so only the link check can refuse this.
    path_a.symlink_to(path_b)

    _assert_integrity_failure(_get_audio(postgres_repository, item_a.id))


def test_symlinked_item_directory_is_not_followed(postgres_repository, tmp_path):
    item = _item_with_audio(postgres_repository)
    path = _object_path(postgres_repository, OWNER_A, item.id)
    elsewhere = tmp_path / "elsewhere"
    os.replace(path.parent, elsewhere)
    path.parent.symlink_to(elsewhere, target_is_directory=True)

    _assert_integrity_failure(_get_audio(postgres_repository, item.id))


def test_missing_object_is_a_non_enumerating_404(postgres_repository):
    item = _item_with_audio(postgres_repository)
    _object_path(postgres_repository, OWNER_A, item.id).unlink()

    response = _get_audio(postgres_repository, item.id)

    assert response.status_code == 404
    assert response.json()["detail"] == "Library item not found"


def test_verification_reads_in_bounded_chunks(postgres_repository, monkeypatch):
    content = os.urandom(3 * 1024 + 17)
    item = _item_with_audio(postgres_repository, content=content)
    monkeypatch.setattr(library_postgres, "VERIFY_CHUNK_BYTES", 1024)
    reads = []
    real_open = open

    class Spy:
        def __init__(self, handle):
            self._handle = handle

        def read(self, size=-1):
            reads.append(size)
            return self._handle.read(size)

        def __getattr__(self, name):
            return getattr(self._handle, name)

    def spying_open(*args, **kwargs):
        return Spy(real_open(*args, **kwargs))

    monkeypatch.setattr(library_postgres, "open", spying_open, raising=False)
    with postgres_repository.audio_for_owner(OWNER_A, item.id):
        pass

    assert reads and all(0 < size <= 1024 for size in reads)
    assert sum(min(size, len(content)) for size in reads) <= len(content) + 1024


def test_oversized_metadata_is_refused_before_reading(postgres_repository, monkeypatch):
    item = _item_with_audio(postgres_repository)
    monkeypatch.setattr(library_postgres, "MAX_AUDIO_BYTES", len(AUDIO) - 1)

    _assert_integrity_failure(_get_audio(postgres_repository, item.id))


def test_range_requests_are_served_from_verified_bytes(postgres_repository):
    item = _item_with_audio(postgres_repository)

    partial = _get_audio(postgres_repository, item.id, headers={"Range": "bytes=10-99"})
    tail = _get_audio(postgres_repository, item.id, headers={"Range": "bytes=-5"})
    unsatisfiable = _get_audio(
        postgres_repository, item.id, headers={"Range": f"bytes={len(AUDIO) + 5}-"}
    )

    assert partial.status_code == 206
    assert partial.content == AUDIO[10:100]
    assert partial.headers["content-range"] == f"bytes 10-99/{len(AUDIO)}"
    assert tail.content == AUDIO[-5:]
    assert unsatisfiable.status_code == 416


def test_bytes_swapped_after_verification_are_never_streamed(
    postgres_repository, monkeypatch
):
    """End to end through FastAPI: the swapped path is never reopened."""
    item = _item_with_audio(postgres_repository)
    original = postgres_repository.audio_for_owner

    def verify_then_swap(owner, item_id):
        audio = original(owner, item_id)
        replacement = audio.path.with_name("swap.tmp")
        replacement.write_bytes(b"Z" * len(AUDIO))
        os.replace(replacement, audio.path)
        return audio

    path = _object_path(postgres_repository, OWNER_A, item.id)
    monkeypatch.setattr(postgres_repository, "audio_for_owner", verify_then_swap)
    full = _get_audio(postgres_repository, item.id)
    path.write_bytes(AUDIO)
    ranged = _get_audio(postgres_repository, item.id, headers={"Range": "bytes=0-31"})

    assert full.content == AUDIO
    assert ranged.content == AUDIO[:32]


def _asgi_call(response, method="GET", headers=(), extensions=None):
    """Drive the response at the ASGI layer and collect every message."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "path": "/audio",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "extensions": extensions if extensions is not None else {},
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    import anyio

    anyio.run(response, scope, receive, send)
    start = messages[0]
    body = b"".join(m.get("body", b"") for m in messages[1:])
    return start, dict((k.decode(), v.decode()) for k, v in start["headers"]), body, messages


def _verified_then_swapped(repository, item_id):
    """Verification passes, then the object at the path is replaced."""
    from services.api.library_api import VerifiedAudioResponse

    audio = repository.audio_for_owner(OWNER_A, item_id)
    replacement = audio.path.with_name("swap.tmp")
    replacement.write_bytes(b"Z" * len(AUDIO))
    os.replace(replacement, audio.path)
    return audio, VerifiedAudioResponse(audio)


@pytest.mark.parametrize(
    "method,headers,status,expected",
    [
        ("GET", (), 200, AUDIO),
        ("GET", (("Range", "bytes=100-299"),), 206, AUDIO[100:300]),
        ("GET", (("Range", "bytes=-7"),), 206, AUDIO[-7:]),
        ("GET", (("Range", f"bytes={len(AUDIO) - 3}-"),), 206, AUDIO[-3:]),
        # Several ranges are answered with the whole verified body (RFC 9110
        # lets a server ignore Range); never a multipart read of the path.
        ("GET", (("Range", "bytes=0-9,20-29"),), 200, AUDIO),
        ("HEAD", (), 200, b""),
        ("HEAD", (("Range", "bytes=0-9"),), 206, b""),
    ],
    ids=["get", "single-range", "suffix-range", "open-range", "multi-range", "head", "head-range"],
)
def test_asgi_layer_streams_only_verified_bytes_after_swap(
    postgres_repository, method, headers, status, expected
):
    item = _item_with_audio(postgres_repository)
    audio, response = _verified_then_swapped(postgres_repository, item.id)

    start, response_headers, body, _ = _asgi_call(response, method, headers)

    assert start["status"] == status
    assert body == expected
    assert b"Z" * 8 not in body
    length = len(AUDIO) if method == "HEAD" and status == 200 else len(expected)
    if method == "HEAD" and status == 206:
        length = 10
    assert int(response_headers["content-length"]) == length
    assert audio.file.closed


@pytest.mark.parametrize(
    "headers,status,expected",
    [
        ((("Range", "bytes=0-9"), ("If-Range", '"stale-etag"')), 200, AUDIO),
        ((("Range", "bytes=9-0"),), 200, AUDIO),
        ((("Range", "items=0-9"),), 200, AUDIO),
        ((("Range", "bytes=-"),), 200, AUDIO),
        ((("Range", "bytes=-0"),), 416, b""),
        ((("Range", f"bytes={len(AUDIO)}-"),), 416, b""),
        ((("Range", f"bytes=0-{len(AUDIO) * 2}"),), 206, AUDIO),
        ((("Range", "bytes=" + "9" * 5000 + "-"),), 200, AUDIO),
        ((("Range", "bytes=0-" + "9" * 5000),), 200, AUDIO),
        ((("Range", "bytes=-" + "9" * 5000),), 200, AUDIO),
    ],
    ids=["stale-if-range", "reversed", "other-unit", "empty", "zero-suffix",
         "past-end", "clamped-end", "huge-start", "huge-end", "huge-suffix"],
)
def test_range_edge_cases(postgres_repository, headers, status, expected):
    from services.api.library_api import VerifiedAudioResponse

    item = _item_with_audio(postgres_repository)
    audio = postgres_repository.audio_for_owner(OWNER_A, item.id)

    start, response_headers, body, _ = _asgi_call(VerifiedAudioResponse(audio), "GET", headers)

    assert (start["status"], body) == (status, expected)
    if status == 416:
        assert response_headers["content-range"] == f"bytes */{len(AUDIO)}"
    assert audio.file.closed


def test_matching_if_range_serves_the_partial_body(postgres_repository):
    from services.api.library_api import VerifiedAudioResponse

    item = _item_with_audio(postgres_repository)
    audio = postgres_repository.audio_for_owner(OWNER_A, item.id)
    etag = f'"{hashlib.sha256(AUDIO).hexdigest()}"'

    start, _, body, _ = _asgi_call(
        VerifiedAudioResponse(audio), headers=(("Range", "bytes=5-9"), ("If-Range", etag))
    )

    assert (start["status"], body) == (206, AUDIO[5:10])


def test_handle_is_closed_when_the_client_send_fails(postgres_repository):
    import anyio

    from services.api.library_api import VerifiedAudioResponse

    item = _item_with_audio(postgres_repository)
    audio = postgres_repository.audio_for_owner(OWNER_A, item.id)

    async def receive():
        return {"type": "http.disconnect"}

    async def broken_send(message):
        if message["type"] == "http.response.body":
            raise OSError("client went away")

    # ASGI 2.4 servers raise from send() after a disconnect.
    scope = {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "GET",
             "headers": [], "extensions": {}}
    with pytest.raises(OSError):
        anyio.run(VerifiedAudioResponse(audio), scope, receive, broken_send)
    assert audio.file.closed


def test_in_place_truncation_after_verification_aborts(postgres_repository):
    """Same inode shortened: never send less than the declared length."""
    import anyio

    from services.api.library_api import VerifiedAudioResponse

    item = _item_with_audio(postgres_repository)
    audio = postgres_repository.audio_for_owner(OWNER_A, item.id)
    os.truncate(audio.path, 100)
    sent = []

    async def receive():
        await anyio.sleep_forever()

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "headers": [], "extensions": {}}
    with pytest.raises(RuntimeError):
        anyio.run(VerifiedAudioResponse(audio), scope, receive, send)
    assert not any(m.get("more_body") is False for m in sent[1:])
    assert audio.file.closed


def test_pathsend_extension_never_bypasses_the_verified_handle(postgres_repository):
    item = _item_with_audio(postgres_repository)
    audio, response = _verified_then_swapped(postgres_repository, item.id)

    _, _, body, messages = _asgi_call(
        response, extensions={"http.response.pathsend": {}}
    )

    assert all(m["type"] != "http.response.pathsend" for m in messages)
    assert body == AUDIO
    assert audio.file.closed


# --- cleanup outbox worker -------------------------------------------------


def test_failed_delete_is_retried_after_backoff(postgres_repository, monkeypatch):
    item = _item_with_audio(postgres_repository)
    path = _object_path(postgres_repository, OWNER_A, item.id)
    real_delete = postgres_repository.media_objects.delete
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)
    postgres_repository.delete_for_owner(OWNER_A, item.id)

    [(_, status, attempts, next_at, error)] = _outbox()
    assert (status, attempts, error) == ("pending", 1, "object_delete_failed")
    assert next_at == NOW + library_postgres.CLEANUP_BACKOFF_BASE
    monkeypatch.setattr(postgres_repository.media_objects, "delete", real_delete)

    assert cleanup_worker.run_cleanup(postgres_repository)["claimed"] == 0
    assert path.exists()

    _at(postgres_repository, library_postgres.CLEANUP_BACKOFF_BASE)
    counts = cleanup_worker.run_cleanup(postgres_repository)

    assert counts["deleted"] == 1
    assert not path.exists()
    assert _outbox() == []


def test_backoff_doubles_then_dead_letters(postgres_repository, monkeypatch):
    item = _item_with_audio(postgres_repository)
    path = _object_path(postgres_repository, OWNER_A, item.id)
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)
    postgres_repository.delete_for_owner(OWNER_A, item.id)

    elapsed = timedelta()
    delays = []
    for _ in range(MAX_CLEANUP_ATTEMPTS - 1):
        [(_, _, _, next_at, _)] = _outbox()
        delays.append(next_at - (NOW + elapsed))
        elapsed = next_at - NOW
        _at(postgres_repository, elapsed)
        cleanup_worker.run_cleanup(postgres_repository)

    base = library_postgres.CLEANUP_BACKOFF_BASE
    assert delays[:4] == [base, base * 2, base * 4, base * 8]
    assert max(delays) <= library_postgres.CLEANUP_BACKOFF_CAP
    [(_, status, attempts, _, error)] = _outbox()
    assert (status, attempts, error) == ("dead", MAX_CLEANUP_ATTEMPTS, "object_delete_failed")
    assert path.exists()
    assert postgres_repository.cleanup_backlog() == {"pending": 0, "due": 0, "dead": 1}

    _at(postgres_repository, timedelta(days=30))
    assert cleanup_worker.run_cleanup(postgres_repository)["claimed"] == 0


def test_two_concurrent_workers_delete_each_object_once(
    postgres_repository, monkeypatch
):
    items = [
        _item_with_audio(postgres_repository, source=f"bulk_{index}")
        for index in range(12)
    ]
    paths = [_object_path(postgres_repository, OWNER_A, item.id) for item in items]
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)
    for item in items:
        postgres_repository.delete_for_owner(OWNER_A, item.id)
    store = library_postgres.FilesystemMediaObjectStore(
        postgres_repository.media_objects.root
    )
    calls = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def counting_delete(object_key, owner, item_id):
        with lock:
            calls.append(object_key)
        store.delete(object_key, owner, item_id)

    monkeypatch.setattr(postgres_repository.media_objects, "delete", counting_delete)
    _at(postgres_repository, timedelta(hours=1))

    def worker(_):
        barrier.wait()
        return cleanup_worker.run_cleanup(postgres_repository, batch=2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, range(2)))

    assert sorted(calls) == sorted(set(calls))
    # Contention must never strand a claimed row as "skipped". A row lock
    # held past the timeout is "busy" and is only handed back to its lease.
    assert sum(result["skipped"] for result in results) == 0
    # Eventual deletion is the guarantee: anything left after this pass comes
    # back once its claim lease expires, with no failure budget spent.
    remaining = _outbox()
    assert all(attempts == 1 for (_, _, attempts, _, _) in remaining)
    _at(postgres_repository, timedelta(hours=1) + library_postgres.CLEANUP_CLAIM_LEASE)
    cleanup_worker.run_cleanup(postgres_repository, batch=50)
    assert sorted(calls) == sorted(set(calls))
    assert len(calls) == 12
    assert _outbox() == []
    assert not any(path.exists() for path in paths)
    assert sum(result["deleted"] for result in results) == 12
    assert not any(path.exists() for path in paths)
    assert _outbox() == []


def test_crash_after_claim_is_retried_by_next_worker(postgres_repository, monkeypatch):
    item = _item_with_audio(postgres_repository)
    path = _object_path(postgres_repository, OWNER_A, item.id)
    real_delete = postgres_repository.media_objects.delete
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)
    postgres_repository.delete_for_owner(OWNER_A, item.id)
    _at(postgres_repository, timedelta(hours=1))

    def crash(*_args):
        raise SimulatedCrash()

    monkeypatch.setattr(postgres_repository.media_objects, "delete", crash)
    with pytest.raises(SimulatedCrash):
        cleanup_worker.run_cleanup(postgres_repository)
    [(_, status, attempts, next_at, _)] = _outbox()
    # Only the genuine delete failure counts; the crashed claim is a lease.
    assert (status, attempts) == ("pending", 1)
    assert next_at == NOW + timedelta(hours=1) + library_postgres.CLEANUP_CLAIM_LEASE
    assert path.exists()

    monkeypatch.setattr(postgres_repository.media_objects, "delete", real_delete)
    assert cleanup_worker.run_cleanup(postgres_repository)["claimed"] == 0
    postgres_repository.clock = lambda: next_at
    assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1
    assert not path.exists()
    assert _outbox() == []


def test_crash_after_object_delete_is_idempotent(postgres_repository, monkeypatch):
    item = _item_with_audio(postgres_repository)
    path = _object_path(postgres_repository, OWNER_A, item.id)
    real_delete = postgres_repository.media_objects.delete

    def delete_then_crash(*args):
        real_delete(*args)
        raise SimulatedCrash()

    monkeypatch.setattr(postgres_repository.media_objects, "delete", delete_then_crash)
    with pytest.raises(SimulatedCrash):
        postgres_repository.delete_for_owner(OWNER_A, item.id)
    assert not path.exists()
    # The object was removed before the crash: nothing failed, so no failure
    # is counted and the row returns when the claim lease expires.
    [(_, status, attempts, next_at, _)] = _outbox()
    assert (status, attempts) == ("pending", 0)
    assert next_at == NOW + library_postgres.CLEANUP_CLAIM_LEASE

    monkeypatch.setattr(postgres_repository.media_objects, "delete", real_delete)
    _at(postgres_repository, timedelta(hours=1))
    assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1
    assert _outbox() == []


def test_object_delete_runs_under_the_outbox_row_lock(postgres_repository, monkeypatch):
    """An upload adopting this key must block until the delete finishes."""
    item = _item_with_audio(postgres_repository)
    real_delete = postgres_repository.media_objects.delete
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)
    postgres_repository.delete_for_owner(OWNER_A, item.id)
    _at(postgres_repository, timedelta(hours=1))
    adoption_errors = []

    def delete_while_adoption_is_attempted(*args):
        with psycopg.connect(_dsn()) as other:
            other.execute("SET lock_timeout = '200ms'")
            try:
                other.execute("DELETE FROM cloud_media_cleanup_outbox")
            except psycopg.errors.LockNotAvailable as exc:
                adoption_errors.append(exc)
        real_delete(*args)

    monkeypatch.setattr(
        postgres_repository.media_objects, "delete", delete_while_adoption_is_attempted
    )
    assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1
    assert len(adoption_errors) == 1


def test_stale_claim_is_skipped_after_another_worker_reclaims(
    postgres_repository, monkeypatch
):
    item = _item_with_audio(postgres_repository)
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)
    postgres_repository.delete_for_owner(OWNER_A, item.id)
    _at(postgres_repository, timedelta(hours=1))
    [stale] = postgres_repository._claim_cleanups(limit=1)
    _at(postgres_repository, timedelta(hours=2))
    assert cleanup_worker.run_cleanup(postgres_repository)["retry_scheduled"] == 1

    assert postgres_repository._process_cleanup(stale) == "skipped"
    # Two genuine failures (immediate cleanup and the re-claim); the stale
    # claim itself spent nothing and its late processing wrote nothing.
    assert _outbox()[0][2] == 2


def _insert_outbox(owner, item_id, object_key):
    with psycopg.connect(_dsn()) as connection:
        connection.execute(
            """
            INSERT INTO cloud_media_cleanup_outbox (
                cleanup_id, owner_user_id, library_item_id, object_key, status,
                attempt_count, created_at, updated_at, next_attempt_at
            ) VALUES (%s, %s, %s, %s, 'pending', 0, %s, %s, %s)
            """,
            (str(uuid.uuid4()), owner, item_id, object_key, NOW, NOW, NOW),
        )


def test_row_whose_key_belongs_to_another_owner_is_dead_lettered(postgres_repository):
    item_b = _item_with_audio(postgres_repository, OWNER_B, source="b_3")
    path_b = _object_path(postgres_repository, OWNER_B, item_b.id)
    other_key = postgres_repository.media_objects.put_audio(
        OWNER_B, item_b.id, "orphan.mp3", b"owner-b-orphan"
    )
    # The database binds the item into the key; only the owner hash differs.
    _insert_outbox(OWNER_A, item_b.id, other_key)

    counts = cleanup_worker.run_cleanup(postgres_repository)

    assert counts["dead"] == 1
    [(_, status, _, _, error)] = _outbox()
    assert (status, error) == ("dead", "namespace_mismatch")
    assert path_b.exists()
    assert (postgres_repository.media_objects.root / other_key).exists()


def test_row_for_a_live_object_is_never_deleted(postgres_repository):
    item = _item_with_audio(postgres_repository)
    with psycopg.connect(_dsn()) as connection:
        [live_key] = connection.execute(
            "SELECT object_key FROM cloud_library_media_assets"
        ).fetchone()
    _insert_outbox(OWNER_A, item.id, live_key)

    counts = cleanup_worker.run_cleanup(postgres_repository)

    assert counts["dead"] == 1
    assert _outbox()[0][4] == "object_still_referenced"
    assert _get_audio(postgres_repository, item.id).content == AUDIO


def test_duplicate_enqueue_keeps_one_row(postgres_repository):
    item = postgres_repository.create_for_owner(OWNER_A, lesson())
    key = postgres_repository.media_objects.put_audio(
        OWNER_A, item.id, "a.mp3", b"orphan"
    )
    with postgres_repository._connect() as connection:
        first = postgres_repository._enqueue_cleanup(
            connection, OWNER_A, item.id, key, not_before=NOW
        )
        second = postgres_repository._enqueue_cleanup(
            connection, OWNER_A, item.id, key, not_before=NOW
        )

    assert first == second
    assert len(_outbox()) == 1
    assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1


def test_worker_cli_reports_counts_only(postgres_repository, monkeypatch, capsys):
    item = _item_with_audio(postgres_repository)
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)
    postgres_repository.delete_for_owner(OWNER_A, item.id)
    monkeypatch.setenv("CLOUD_LIBRARY_DATABASE_URL", _dsn())
    monkeypatch.setenv(
        "CLOUD_LIBRARY_MEDIA_ROOT", str(postgres_repository.media_objects.root)
    )

    # Real clock: far past the synthetic NOW, so the row is due.
    assert cleanup_worker.main([]) == 0
    output = capsys.readouterr().out
    assert "deleted=1" in output and "backlog_pending=0" in output
    assert "owners/" not in output and OWNER_A not in output


def test_worker_cli_unavailable_database_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(
        "CLOUD_LIBRARY_DATABASE_URL",
        "postgresql://secret-user:secret-password@127.0.0.1:1/db?connect_timeout=1",
    )
    monkeypatch.setenv("CLOUD_LIBRARY_MEDIA_ROOT", str(tmp_path))

    assert cleanup_worker.main([]) == 1
    assert "secret" not in capsys.readouterr().err


def test_worker_bounds_are_validated(postgres_repository):
    with pytest.raises(ValueError):
        cleanup_worker.run_cleanup(postgres_repository, batch=0)
    with pytest.raises(ValueError):
        postgres_repository.run_cleanup_batch(limit=library_postgres.MAX_CLEANUP_BATCH + 1)


# --- replacement path ------------------------------------------------------


def _media_keys():
    with psycopg.connect(_dsn()) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT object_key FROM cloud_library_media_assets"
            ).fetchall()
        ]


def _object_files(repository):
    return sorted(
        path.relative_to(repository.media_objects.root).as_posix()
        for path in repository.media_objects.root.rglob("audio-*")
    )


def test_replacement_removes_old_object_and_leaves_no_rows(postgres_repository):
    item = _item_with_audio(postgres_repository)
    old_path = _object_path(postgres_repository, OWNER_A, item.id)

    postgres_repository.put_audio_for_owner(
        OWNER_A, item.id, filename="new.mp3", content_type=None, content=b"new-audio"
    )

    assert not old_path.exists()
    assert _object_files(postgres_repository) == _media_keys()
    assert _outbox() == []
    assert _get_audio(postgres_repository, item.id).content == b"new-audio"


def test_replacement_delete_failure_keeps_old_key_in_outbox(
    postgres_repository, monkeypatch
):
    item = _item_with_audio(postgres_repository)
    old_path = _object_path(postgres_repository, OWNER_A, item.id)
    real_delete = postgres_repository.media_objects.delete
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)

    postgres_repository.put_audio_for_owner(
        OWNER_A, item.id, filename="new.mp3", content_type=None, content=b"new-audio"
    )

    assert old_path.exists()
    [(old_key, status, attempts, _, _)] = _outbox()
    assert old_path.as_posix().endswith(old_key)
    assert (status, attempts) == ("pending", 1)
    assert _get_audio(postgres_repository, item.id).content == b"new-audio"

    monkeypatch.setattr(postgres_repository.media_objects, "delete", real_delete)
    _at(postgres_repository, timedelta(hours=1))
    assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1
    assert not old_path.exists()


def test_crash_between_write_and_commit_is_cleaned_after_grace(
    postgres_repository, monkeypatch
):
    item = _item_with_audio(postgres_repository)
    real_write = postgres_repository.media_objects.write_audio

    def write_then_crash(*args):
        real_write(*args)
        raise RuntimeError("synthetic crash after object write")

    monkeypatch.setattr(postgres_repository.media_objects, "write_audio", write_then_crash)
    # A real crash runs no handler: skip the in-process immediate cleanup.
    monkeypatch.setattr(postgres_repository, "_cleanup_now", lambda _ids: None)
    with pytest.raises(RuntimeError):
        postgres_repository.put_audio_for_owner(
            OWNER_A, item.id, filename="new.mp3", content_type=None, content=b"lost"
        )
    orphans = [
        key for key in _object_files(postgres_repository) if key not in _media_keys()
    ]
    assert len(orphans) == 1
    assert [row[0] for row in _outbox()] == orphans

    assert cleanup_worker.run_cleanup(postgres_repository)["claimed"] == 0
    _at(postgres_repository, UPLOAD_CLEANUP_GRACE)
    assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1
    assert _object_files(postgres_repository) == _media_keys()
    assert _get_audio(postgres_repository, item.id).content == AUDIO


def test_upload_claimed_by_worker_before_commit_aborts_cleanly(
    postgres_repository, monkeypatch
):
    item = _item_with_audio(postgres_repository)
    real_write = postgres_repository.media_objects.write_audio

    def slow_write(*args):
        real_write(*args)
        # The upload outlived its grace and a worker removed the object.
        _at(postgres_repository, UPLOAD_CLEANUP_GRACE)
        assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1

    monkeypatch.setattr(postgres_repository.media_objects, "write_audio", slow_write)
    with pytest.raises(MediaUploadAbortedError):
        postgres_repository.put_audio_for_owner(
            OWNER_A, item.id, filename="new.mp3", content_type=None, content=b"late"
        )

    assert _outbox() == []
    assert _object_files(postgres_repository) == _media_keys()
    assert _get_audio(postgres_repository, item.id).content == AUDIO


def test_upload_racing_item_delete_leaves_no_object(postgres_repository, monkeypatch):
    item = postgres_repository.create_for_owner(OWNER_A, lesson())
    real_write = postgres_repository.media_objects.write_audio

    def write_then_delete_item(*args):
        real_write(*args)
        postgres_repository.delete_for_owner(OWNER_A, item.id)

    monkeypatch.setattr(
        postgres_repository.media_objects, "write_audio", write_then_delete_item
    )
    with pytest.raises(KeyError):
        postgres_repository.put_audio_for_owner(
            OWNER_A, item.id, filename="new.mp3", content_type=None, content=b"gone"
        )

    assert _object_files(postgres_repository) == []
    assert _outbox() == []


def test_delete_waits_for_an_uncommitted_first_upload(postgres_repository, monkeypatch):
    """Without the item lock, delete reads no keys, then cascades the upload's
    asset row once it commits, leaving its object with no outbox row."""
    item = postgres_repository.create_for_owner(OWNER_A, lesson())
    inserted = threading.Event()
    release = threading.Event()
    real_connect = postgres_repository._connect
    upload_thread = None

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
            if (
                threading.current_thread() is upload_thread
                and "INSERT INTO cloud_library_media_assets" in sql
            ):
                inserted.set()
                assert release.wait(10)
            return result

    monkeypatch.setattr(
        postgres_repository, "_connect", lambda: PausingConnection(real_connect())
    )

    def upload():
        # Its commit lands; only the read-back sees the deleted item.
        with pytest.raises(KeyError):
            postgres_repository.put_audio_for_owner(
                OWNER_A, item.id, filename="first.mp3", content_type=None, content=b"first"
            )

    upload_thread = threading.Thread(target=upload)
    upload_thread.start()
    assert inserted.wait(10)
    with ThreadPoolExecutor(max_workers=1) as executor:
        deleting = executor.submit(postgres_repository.delete_for_owner, OWNER_A, item.id)
        with pytest.raises(TimeoutError):
            deleting.result(timeout=0.5)
        release.set()
        deleting.result(timeout=10)
    upload_thread.join(10)

    assert _media_keys() == []
    assert _object_files(postgres_repository) == []
    assert _outbox() == []


def test_concurrent_replacements_converge_with_no_orphans(postgres_repository):
    item = _item_with_audio(postgres_repository)
    barrier = threading.Barrier(4)

    def replace(index):
        barrier.wait()
        postgres_repository.put_audio_for_owner(
            OWNER_A,
            item.id,
            filename=f"take-{index}.mp3",
            content_type=None,
            content=f"take-{index}".encode(),
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(replace, range(4)))
    _at(postgres_repository, timedelta(hours=1))
    cleanup_worker.run_cleanup(postgres_repository)

    assert len(_media_keys()) == 1
    assert _object_files(postgres_repository) == _media_keys()
    assert _outbox() == []
    assert _get_audio(postgres_repository, item.id).content.startswith(b"take-")


def test_replacement_does_not_touch_other_owner_objects(postgres_repository):
    item_a = _item_with_audio(postgres_repository)
    item_b = _item_with_audio(postgres_repository, OWNER_B, source="b_4")
    path_b = _object_path(postgres_repository, OWNER_B, item_b.id)

    postgres_repository.put_audio_for_owner(
        OWNER_A, item_a.id, filename="new.mp3", content_type=None, content=b"a2"
    )
    postgres_repository.delete_for_owner(OWNER_A, item_a.id)
    cleanup_worker.run_cleanup(postgres_repository)

    assert path_b.exists()
    assert _get_audio(postgres_repository, item_b.id, OWNER_B).content == AUDIO


# --- migration -------------------------------------------------------------


def test_0010_backfills_existing_rows_and_rolls_back():
    dsn = _dsn()
    _execute_migration(dsn, "0001_owner_scoped_library.down.sql")
    _execute_migration(dsn, "0001_owner_scoped_library.up.sql")
    try:
        _provision_owner(dsn, OWNER_A)
        item_id = str(uuid.uuid4())
        key = (
            "owners/" + "a" * 64 + f"/library/{item_id}/audio-" + "b" * 32 + ".mp3"
        )
        with psycopg.connect(dsn) as connection:
            connection.execute(
                """
                INSERT INTO cloud_media_cleanup_outbox (
                    cleanup_id, owner_user_id, library_item_id, object_key,
                    status, attempt_count, created_at, updated_at
                ) VALUES (gen_random_uuid(), %s, %s, %s, 'pending', 2, %s, %s)
                """,
                (OWNER_A, item_id, key, NOW, NOW + timedelta(minutes=1)),
            )
        _execute_migration(dsn, "0010_library_media_cleanup_worker.up.sql")
        [(_, status, attempts, next_at, error)] = _outbox()
        assert (status, attempts, next_at, error) == (
            "pending", 2, NOW + timedelta(minutes=1), None,
        )

        with psycopg.connect(dsn) as connection:
            connection.execute(
                "UPDATE cloud_media_cleanup_outbox "
                "SET status = 'dead', last_error_code = 'object_delete_failed'"
            )
        with pytest.raises(psycopg.errors.RaiseException):
            _execute_migration(dsn, "0010_library_media_cleanup_worker.down.sql")
        assert _outbox()[0][1] == "dead"

        with psycopg.connect(dsn) as connection:
            connection.execute(
                "UPDATE cloud_media_cleanup_outbox "
                "SET status = 'pending', last_error_code = NULL"
            )
        _execute_migration(dsn, "0010_library_media_cleanup_worker.down.sql")
        with psycopg.connect(dsn) as connection:
            columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'cloud_media_cleanup_outbox'
                    """
                )
            }
            assert "next_attempt_at" not in columns
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    "UPDATE cloud_media_cleanup_outbox SET status = 'dead'"
                )
    finally:
        _execute_migration(dsn, "0001_owner_scoped_library.down.sql")


def test_database_rejects_dead_rows_without_reason(postgres_repository):
    item = postgres_repository.create_for_owner(OWNER_A, lesson())
    key = postgres_repository.media_objects.new_audio_key(OWNER_A, item.id, "a.mp3")
    _insert_outbox(OWNER_A, item.id, key)
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(_dsn()) as connection:
            connection.execute("UPDATE cloud_media_cleanup_outbox SET status = 'dead'")
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(_dsn()) as connection:
            connection.execute(
                "UPDATE cloud_media_cleanup_outbox SET last_error_code = '/tmp/leak'"
            )


# --- independent review findings (bfed0b8) ---------------------------------


def test_concurrent_first_uploads_leave_no_untracked_object(
    postgres_repository, monkeypatch
):
    """Review finding 1: with no media row to lock, both uploads used to see
    previous=None and the second upsert dropped the first key untracked."""
    item = postgres_repository.create_for_owner(OWNER_A, lesson())
    inserted = threading.Event()
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
            if (
                threading.current_thread() is first_thread
                and "INSERT INTO cloud_library_media_assets" in sql
            ):
                inserted.set()
                assert release.wait(10)
            return result

    monkeypatch.setattr(
        postgres_repository, "_connect", lambda: PausingConnection(real_connect())
    )

    def upload(name):
        postgres_repository.put_audio_for_owner(
            OWNER_A, item.id, filename=f"{name}.mp3", content_type=None, content=name.encode()
        )

    first_thread = threading.Thread(target=upload, args=("first",))
    first_thread.start()
    assert inserted.wait(10)
    with ThreadPoolExecutor(max_workers=1) as executor:
        second = executor.submit(upload, "second")
        with pytest.raises(TimeoutError):
            second.result(timeout=0.5)
        release.set()
        second.result(timeout=10)
    first_thread.join(10)
    monkeypatch.setattr(postgres_repository, "_connect", real_connect)
    cleanup_worker.run_cleanup(postgres_repository)

    assert len(_media_keys()) == 1
    assert _object_files(postgres_repository) == _media_keys()
    assert _outbox() == []
    assert _get_audio(postgres_repository, item.id).content == b"second"


def test_upload_whose_row_is_cleaned_before_the_object_lands_is_retracked(
    postgres_repository, monkeypatch
):
    """Review finding 4: the worker deletes nothing (no file yet) and drops the
    row; the object then lands, so the abort must re-enqueue it."""
    item = _item_with_audio(postgres_repository)
    real_write = postgres_repository.media_objects.write_audio

    def worker_runs_before_replace(*args):
        _at(postgres_repository, UPLOAD_CLEANUP_GRACE)
        assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1
        real_write(*args)

    monkeypatch.setattr(
        postgres_repository.media_objects, "write_audio", worker_runs_before_replace
    )
    with pytest.raises(MediaUploadAbortedError):
        postgres_repository.put_audio_for_owner(
            OWNER_A, item.id, filename="new.mp3", content_type=None, content=b"late"
        )

    assert _outbox() == []
    assert _object_files(postgres_repository) == _media_keys()
    assert _get_audio(postgres_repository, item.id).content == AUDIO


@pytest.mark.parametrize("prepare", ["missing", "unmarked"])
def test_worker_refuses_an_uninitialised_media_root(
    postgres_repository, monkeypatch, tmp_path, capsys, prepare
):
    """Review finding 3: a wrong root used to "delete" every row as a no-op."""
    item = _item_with_audio(postgres_repository)
    path = _object_path(postgres_repository, OWNER_A, item.id)
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)
    postgres_repository.delete_for_owner(OWNER_A, item.id)
    wrong_root = tmp_path / "typo-root"
    if prepare == "unmarked":
        wrong_root.mkdir()
    monkeypatch.setenv("CLOUD_LIBRARY_DATABASE_URL", _dsn())
    monkeypatch.setenv("CLOUD_LIBRARY_MEDIA_ROOT", str(wrong_root))

    assert cleanup_worker.main([]) == 1

    assert "not an initialised media root" in capsys.readouterr().err
    assert wrong_root.exists() == (prepare == "unmarked")
    assert [row[1] for row in _outbox()] == ["pending"]
    assert path.exists()


def test_app_store_marks_its_root(tmp_path):
    root = tmp_path / "media"
    library_postgres.FilesystemMediaObjectStore(root)

    assert (root / library_postgres.ROOT_MARKER).is_file()
    library_postgres.FilesystemMediaObjectStore(root, create=False)


def test_symlink_loop_is_an_integrity_failure_and_a_dead_letter(
    postgres_repository, monkeypatch
):
    """Review finding 5: resolve() raised RuntimeError/OSError unmapped."""
    item = _item_with_audio(postgres_repository)
    path = _object_path(postgres_repository, OWNER_A, item.id)
    path.unlink()
    path.symlink_to(path)

    _assert_integrity_failure(_get_audio(postgres_repository, item.id))

    postgres_repository.delete_for_owner(OWNER_A, item.id)
    [(_, status, _, _, error)] = _outbox()
    assert (status, error) == ("dead", "namespace_mismatch")


def test_unexpected_delete_error_is_a_recorded_retry(postgres_repository, monkeypatch):
    item = _item_with_audio(postgres_repository)

    def unexpected(*_args):
        raise OSError("/secret/absolute/path is busy")

    monkeypatch.setattr(postgres_repository.media_objects, "delete", unexpected)
    postgres_repository.delete_for_owner(OWNER_A, item.id)

    [(_, status, attempts, _, error)] = _outbox()
    assert (status, attempts, error) == ("pending", 1, "object_delete_failed")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs only")
def test_fifo_at_object_key_fails_fast(postgres_repository):
    """Review finding 6: open() on a FIFO blocked the request thread."""
    item = _item_with_audio(postgres_repository)
    path = _object_path(postgres_repository, OWNER_A, item.id)
    path.unlink()
    os.mkfifo(path)

    executor = ThreadPoolExecutor(max_workers=1)
    pending = executor.submit(_get_audio, postgres_repository, item.id)
    try:
        response = pending.result(timeout=5)
    finally:
        if not pending.done():
            # Release a reader stuck in open() so a regression fails, not hangs.
            os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
        executor.shutdown(wait=True)

    _assert_integrity_failure(response)


def test_disconnect_on_a_pre_2_4_server_stops_reading_the_file(postgres_repository):
    """Re-review Low 2: uvicorn (ASGI 2.3) swallows sends after a disconnect,
    so the response must notice via receive() instead of reading on."""
    import anyio

    from services.api.library_api import VerifiedAudioResponse
    from services.library import verified_response

    content = os.urandom(verified_response.CHUNK_BYTES * 16)
    item = _item_with_audio(postgres_repository, content=content)
    audio = postgres_repository.audio_for_owner(OWNER_A, item.id)
    chunks = []

    async def run():
        gone = anyio.Event()

        async def receive():
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] != "http.response.body":
                return
            chunks.append(message)
            if len(chunks) == 1:
                gone.set()
            # Like uvicorn after a disconnect: swallow, never raise.
            await anyio.sleep(0)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "method": "GET",
            "headers": [],
            "extensions": {},
        }
        await VerifiedAudioResponse(audio)(scope, receive, send)

    anyio.run(run)
    assert 1 <= len(chunks) < 16
    assert audio.file.closed


def test_completed_send_on_a_pre_2_4_server_returns(postgres_repository):
    import anyio

    from services.api.library_api import VerifiedAudioResponse

    item = _item_with_audio(postgres_repository)
    audio = postgres_repository.audio_for_owner(OWNER_A, item.id)
    body = []
    ran = []

    async def receive():
        await anyio.sleep_forever()

    async def send(message):
        body.append(message.get("body", b""))

    response = VerifiedAudioResponse(audio)

    async def background():
        ran.append(True)

    response.background = background
    scope = {"type": "http", "asgi": {"spec_version": "2.3"}, "method": "GET",
             "headers": [], "extensions": {}}
    anyio.run(response, scope, receive, send)

    assert b"".join(body) == AUDIO
    assert ran == [True]
    assert audio.file.closed


# --- N-1 / N-1b: lock contention must not strand rows or spend the budget ---


class _RowLockHolder:
    """Hold FOR UPDATE on outbox rows from another connection, as another
    worker's claim scan or processing transaction would."""

    def __init__(self, cleanup_ids, hold_seconds):
        self._ids = [str(value) for value in cleanup_ids]
        self._hold = hold_seconds
        self.locked = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        with psycopg.connect(_dsn()) as connection:
            connection.execute(
                "SELECT 1 FROM cloud_media_cleanup_outbox"
                " WHERE cleanup_id = ANY(%s::uuid[]) FOR UPDATE",
                (self._ids,),
            )
            self.locked.set()
            threading.Event().wait(self._hold)
            connection.rollback()

    def __enter__(self):
        self._thread.start()
        assert self.locked.wait(5)
        return self

    def __exit__(self, *_exc):
        self._thread.join(10)


def _pending_cleanup(repository, monkeypatch, source):
    """One pending outbox row (attempt_count 1) whose object still exists."""
    item = _item_with_audio(repository, source=source)
    path = _object_path(repository, OWNER_A, item.id)
    real_delete = repository.media_objects.delete
    monkeypatch.setattr(repository.media_objects, "delete", _failing_delete)
    repository.delete_for_owner(OWNER_A, item.id)
    monkeypatch.setattr(repository.media_objects, "delete", real_delete)
    return path


def test_transient_foreign_row_lock_is_waited_for_not_skipped(
    postgres_repository, monkeypatch
):
    """Deterministic N-1: another worker's claim scan briefly locks a row this
    worker has already claimed. Before the fix processing skipped it."""
    path = _pending_cleanup(postgres_repository, monkeypatch, "n1_wait")
    _at(postgres_repository, timedelta(hours=1))
    [claim] = postgres_repository._claim_cleanups(limit=1)

    with _RowLockHolder([claim["cleanup_id"]], hold_seconds=0.3):
        outcome = postgres_repository._process_cleanup(claim)

    assert outcome == "deleted"
    assert not path.exists()
    assert _outbox() == []


def test_lock_held_past_timeout_is_busy_and_spends_no_budget(
    postgres_repository, monkeypatch
):
    path = _pending_cleanup(postgres_repository, monkeypatch, "n1_busy")
    monkeypatch.setattr(library_postgres, "CLEANUP_LOCK_TIMEOUT_MS", 100)
    _at(postgres_repository, timedelta(hours=1))
    [claim] = postgres_repository._claim_cleanups(limit=1)

    with _RowLockHolder([claim["cleanup_id"]], hold_seconds=1.0):
        assert postgres_repository._process_cleanup(claim) == "busy"

    [(_, status, attempts, next_at, error)] = _outbox()
    assert (status, attempts, error) == ("pending", 1, "object_delete_failed")
    assert next_at == NOW + timedelta(hours=1) + library_postgres.CLEANUP_CLAIM_LEASE
    assert path.exists()
    # Not due again until the lease expires; then an ordinary run deletes it.
    assert cleanup_worker.run_cleanup(postgres_repository)["claimed"] == 0
    postgres_repository.clock = lambda: next_at
    assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1
    assert not path.exists()


def test_repeated_lock_conflicts_never_dead_letter_the_row(
    postgres_repository, monkeypatch
):
    """N-1b: before the fix every claim spent budget, so repeated contention
    followed by one genuine failure dead-lettered a deleted user's audio."""
    path = _pending_cleanup(postgres_repository, monkeypatch, "n1_repeat")
    monkeypatch.setattr(library_postgres, "CLEANUP_LOCK_TIMEOUT_MS", 50)
    clock = NOW + timedelta(hours=1)
    for _ in range(MAX_CLEANUP_ATTEMPTS + 5):
        postgres_repository.clock = lambda c=clock: c
        [claim] = postgres_repository._claim_cleanups(limit=1)
        with _RowLockHolder([claim["cleanup_id"]], hold_seconds=0.3):
            assert postgres_repository._process_cleanup(claim) == "busy"
        clock = clock + library_postgres.CLEANUP_CLAIM_LEASE
    [(_, status, attempts, _, _)] = _outbox()
    assert (status, attempts) == ("pending", 1)

    # One genuine failure after all that contention is just the second failure.
    postgres_repository.clock = lambda: clock
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)
    assert cleanup_worker.run_cleanup(postgres_repository)["retry_scheduled"] == 1
    [(_, status, attempts, next_at, _)] = _outbox()
    assert (status, attempts) == ("pending", 2)
    assert next_at == clock + library_postgres.CLEANUP_BACKOFF_BASE * 2

    monkeypatch.undo()
    postgres_repository.clock = lambda: next_at
    assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1
    assert not path.exists() and _outbox() == []


def test_dead_letter_boundary_counts_only_genuine_failures(
    postgres_repository, monkeypatch
):
    path = _pending_cleanup(postgres_repository, monkeypatch, "n1_boundary")
    monkeypatch.setattr(library_postgres, "CLEANUP_LOCK_TIMEOUT_MS", 50)
    monkeypatch.setattr(postgres_repository.media_objects, "delete", _failing_delete)
    while True:
        [(_, status, attempts, next_at, _)] = _outbox()
        if attempts == MAX_CLEANUP_ATTEMPTS - 1:
            break
        # A busy pass between every failure must not move the count.
        postgres_repository.clock = lambda n=next_at: n
        [claim] = postgres_repository._claim_cleanups(limit=1)
        with _RowLockHolder([claim["cleanup_id"]], hold_seconds=0.2):
            assert postgres_repository._process_cleanup(claim) == "busy"
        assert _outbox()[0][2] == attempts
        lease_end = next_at + library_postgres.CLEANUP_CLAIM_LEASE
        postgres_repository.clock = lambda n=lease_end: n
        assert cleanup_worker.run_cleanup(postgres_repository)["retry_scheduled"] == 1

    assert (status, attempts) == ("pending", MAX_CLEANUP_ATTEMPTS - 1)
    postgres_repository.clock = lambda: next_at
    assert cleanup_worker.run_cleanup(postgres_repository)["dead"] == 1
    [(_, status, attempts, _, error)] = _outbox()
    assert (status, attempts, error) == ("dead", MAX_CLEANUP_ATTEMPTS, "object_delete_failed")
    assert path.exists()


def test_worker_restart_recovers_claims_it_never_processed(
    postgres_repository, monkeypatch
):
    paths = [
        _pending_cleanup(postgres_repository, monkeypatch, f"n1_restart_{index}")
        for index in range(4)
    ]
    _at(postgres_repository, timedelta(hours=1))
    claims = postgres_repository._claim_cleanups(limit=10)  # the worker dies here
    assert len(claims) == 4

    assert cleanup_worker.run_cleanup(postgres_repository)["claimed"] == 0
    _at(postgres_repository, timedelta(hours=1) + library_postgres.CLEANUP_CLAIM_LEASE)
    assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 4
    assert not any(path.exists() for path in paths) and _outbox() == []
    # The dead worker's late processing is fenced off by its stale token.
    assert {postgres_repository._process_cleanup(claim) for claim in claims} == {"skipped"}


def test_database_error_after_object_delete_retries_idempotently(
    postgres_repository, monkeypatch
):
    path = _pending_cleanup(postgres_repository, monkeypatch, "n1_dberror")
    _at(postgres_repository, timedelta(hours=1))
    with psycopg.connect(_dsn(), autocommit=True) as admin:
        admin.execute(
            """
            CREATE FUNCTION n1_refuse_delete() RETURNS trigger LANGUAGE plpgsql AS
            $$BEGIN RAISE EXCEPTION 'synthetic database failure'; END$$;
            CREATE TRIGGER n1_refuse_delete BEFORE DELETE ON cloud_media_cleanup_outbox
            FOR EACH ROW EXECUTE FUNCTION n1_refuse_delete();
            """
        )
        try:
            with pytest.raises(library_postgres.CloudLibraryUnavailableError):
                cleanup_worker.run_cleanup(postgres_repository)
        finally:
            admin.execute(
                "DROP TRIGGER n1_refuse_delete ON cloud_media_cleanup_outbox;"
                " DROP FUNCTION n1_refuse_delete();"
            )
    assert not path.exists()  # the object went before the row write failed
    [(_, status, attempts, next_at, _)] = _outbox()
    assert (status, attempts) == ("pending", 1)  # a database error is not a delete failure
    postgres_repository.clock = lambda: next_at
    assert cleanup_worker.run_cleanup(postgres_repository)["deleted"] == 1
    assert _outbox() == []


def test_many_workers_under_lock_storm_delete_every_object_exactly_once(
    postgres_repository, monkeypatch
):
    paths = [
        _pending_cleanup(postgres_repository, monkeypatch, f"n1_storm_{index}")
        for index in range(24)
    ]
    store = library_postgres.FilesystemMediaObjectStore(
        postgres_repository.media_objects.root
    )
    calls = []
    calls_lock = threading.Lock()

    def counting_delete(object_key, owner, item_id):
        with calls_lock:
            calls.append(object_key)
        store.delete(object_key, owner, item_id)

    monkeypatch.setattr(postgres_repository.media_objects, "delete", counting_delete)
    stop = threading.Event()

    def lock_storm():
        # Repeatedly grab short row locks on random pending rows, as concurrent
        # claim scans do, without ever claiming anything. It pauses between
        # grabs: a thread that held every remaining row all the time would
        # starve SKIP LOCKED claims by design, which no real worker does.
        with psycopg.connect(_dsn()) as connection:
            while not stop.is_set():
                connection.execute(
                    "SELECT 1 FROM cloud_media_cleanup_outbox WHERE status = 'pending'"
                    " ORDER BY random() LIMIT 3 FOR UPDATE SKIP LOCKED"
                )
                threading.Event().wait(0.01)
                connection.rollback()
                threading.Event().wait(0.02)

    def run_workers(clock):
        postgres_repository.clock = lambda: clock
        barrier = threading.Barrier(6)

        def worker(_):
            barrier.wait()
            return cleanup_worker.run_cleanup(postgres_repository, batch=3)

        with ThreadPoolExecutor(max_workers=6) as executor:
            return list(executor.map(worker, range(6)))

    storm = threading.Thread(target=lock_storm, daemon=True)
    storm.start()
    clock = NOW + timedelta(hours=1)
    try:
        for _ in range(3):
            results = run_workers(clock)
            # Under contention: nothing stranded, nothing dead-lettered and no
            # failure budget spent on any row still waiting.
            assert sum(result["skipped"] for result in results) == 0
            assert sum(result["dead"] for result in results) == 0
            assert all(row[1:3] == ("pending", 1) for row in _outbox())
            clock = clock + library_postgres.CLEANUP_CLAIM_LEASE
    finally:
        stop.set()
        storm.join(10)

    # Eventual deletion: once contention ends, one ordinary pass finishes.
    results = run_workers(clock)
    assert sum(result["skipped"] for result in results) == 0
    assert sorted(calls) == sorted(set(calls)) and len(calls) == 24
    assert _outbox() == []
    assert not any(path.exists() for path in paths)
