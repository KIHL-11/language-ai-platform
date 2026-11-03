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
    """Guards the FileResponse override: Starlette must not reopen the path."""
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
    assert len(calls) == 12
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
        raise RuntimeError("synthetic worker crash")

    monkeypatch.setattr(postgres_repository.media_objects, "delete", crash)
    with pytest.raises(RuntimeError):
        cleanup_worker.run_cleanup(postgres_repository)
    [(_, status, attempts, next_at, _)] = _outbox()
    assert (status, attempts) == ("pending", 2)
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
        raise RuntimeError("synthetic crash before acknowledgement")

    monkeypatch.setattr(postgres_repository.media_objects, "delete", delete_then_crash)
    with pytest.raises(RuntimeError):
        postgres_repository.delete_for_owner(OWNER_A, item.id)
    assert not path.exists()
    assert [row[1:3] for row in _outbox()] == [("pending", 1)]

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
    assert _outbox()[0][2] == 3


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
