"""PR-D remediation: audio attach under COMMIT uncertainty and concurrency.

Codex review of PR-D (#97) found two defects in ``put_audio_for_owner``:

* High: every transaction exception removed the freshly written object, even
  when COMMIT had been sent and might have succeeded (acknowledgement lost),
  which could delete audio that READY metadata references.
* Medium: a first attach had no row to lock, so two concurrent attaches were
  not serialized and the object replaced by the later one was neither
  referenced nor queued in the cleanup outbox.

Every test uses a throwaway PostgreSQL 16 database and synthetic bytes. The
object store's ``delete`` is wrapped so each physical removal is recorded.
Interleavings are forced deterministically: a statement hook pauses the first
transaction at a chosen statement, and the test releases it only after the
competing transaction is observably blocked on a row lock in PostgreSQL
(``pg_stat_activity``) or has already passed the same statement. No sleep,
fixed wait or retry decides an outcome.
"""
from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager

import psycopg
import pytest

from services.library import postgres as library_postgres
from test_owner_scoped_library import (  # noqa: F401  (fixture re-export)
    OWNER_A,
    _as_owner,
    _dsn,
    client,
    lesson,
    postgres_repository,
)

MEDIA_INSERT = "INSERT INTO cloud_library_media_assets"
ITEM_DELETE = "DELETE FROM cloud_library_items"
TIMEOUT = 30


class _Connection:
    """Delegating connection that reports each statement to a hook first."""

    def __init__(self, connection, hook):
        self._connection = connection
        self._hook = hook
        self.statements: list[str] = []

    def execute(self, query, params=None, **kwargs):
        statement = " ".join(str(query).split())
        self.statements.append(statement)
        self._hook(statement)
        return self._connection.execute(query, params, **kwargs)

    def __getattr__(self, name):
        return getattr(self._connection, name)


def instrument(monkeypatch, repository, hook):
    original = repository._owner_transaction

    @contextmanager
    def instrumented(owner_user_id):
        with original(owner_user_id) as connection:
            yield _Connection(connection, hook)

    monkeypatch.setattr(repository, "_owner_transaction", instrumented)


class PauseFirst:
    """Pause the first thread whose transaction reaches ``marker``.

    Later arrivals record themselves and continue; with ``fail_first`` the
    paused statement raises after release, i.e. before COMMIT is ever sent.
    """

    def __init__(self, marker: str, *, fail_first: bool = False):
        self.marker = marker
        self.fail_first = fail_first
        self._lock = threading.Lock()
        self.first: str | None = None
        self.later: list[str] = []
        self.paused = threading.Event()
        self.release = threading.Event()

    def __call__(self, statement: str) -> None:
        if not statement.startswith(self.marker):
            return
        me = threading.current_thread().name
        with self._lock:
            is_first = self.first is None
            if is_first:
                self.first = me
            else:
                self.later.append(me)
        if not is_first:
            return
        self.paused.set()
        assert self.release.wait(TIMEOUT), "paused transaction was never released"
        if self.fail_first:
            raise psycopg.OperationalError("synthetic failure before COMMIT")


class Worker(threading.Thread):
    def __init__(self, name, target, barrier=None):
        super().__init__(name=name, daemon=True)
        self._target_fn = target
        self._barrier = barrier
        self.result = None
        self.error: BaseException | None = None

    def run(self):
        try:
            if self._barrier is not None:
                self._barrier.wait(TIMEOUT)
            self.result = self._target_fn()
        except BaseException as exc:  # recorded and asserted by the test
            self.error = exc


def lock_waiters(admin_dsn: str) -> int:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        return connection.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        ).fetchone()[0]


def wait_until(predicate, what: str) -> None:
    """Poll an observable condition; the deadline only turns a hang into a failure."""
    deadline = time.monotonic() + TIMEOUT
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for: {what}")
        time.sleep(0.01)


def competitor_blocked_or_through(admin_dsn, pause, *workers, seen=None):
    """True once the competitor is lock-blocked, past the marker, or finished.

    ``seen`` (a list) records which of these was observed, so a test can prove
    the competitor really waited on the row lock instead of passing vacuously.
    """
    if lock_waiters(admin_dsn) >= 1:
        observed = "lock-wait"
    elif pause.later or any(not worker.is_alive() for worker in workers):
        observed = "not-blocked"
    else:
        return False
    if seen is not None:
        seen.append(observed)
    return True


def record_physical_delete(monkeypatch, repository):
    calls: list[str] = []
    original = repository.media_objects.delete

    def recording(object_key, owner_user_id, item_id):
        calls.append(object_key)
        return original(object_key, owner_user_id, item_id)

    monkeypatch.setattr(repository.media_objects, "delete", recording)
    return calls


def files_for(repository, owner_user_id, item_id) -> set[str]:
    root = repository.media_objects.root.resolve()
    owner_key = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
    directory = root / "owners" / owner_key / "library" / item_id
    if not directory.exists():
        return set()
    return {
        path.relative_to(root).as_posix()
        for path in directory.iterdir()
        if path.name.startswith("audio-")
    }


def ready_keys(admin_dsn, item_id) -> list[str]:
    with psycopg.connect(admin_dsn) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT object_key FROM cloud_library_media_assets "
                "WHERE library_item_id = %s AND asset_kind = 'audio' AND status = 'ready'",
                (item_id,),
            ).fetchall()
        ]


def outbox_rows(admin_dsn, item_id) -> set[tuple]:
    with psycopg.connect(admin_dsn) as connection:
        return {
            tuple(row)
            for row in connection.execute(
                "SELECT owner_user_id, library_item_id::text, object_key, status, attempt_count "
                "FROM cloud_media_cleanup_outbox WHERE library_item_id = %s",
                (item_id,),
            ).fetchall()
        }


def pending(owner, item_id, keys) -> set[tuple]:
    return {(owner, item_id, key, "pending", 0) for key in keys}


def attach(repository, owner, item_id, content: bytes, name="take.mp3"):
    return repository.put_audio_for_owner(
        owner, item_id, filename=name, content_type="audio/mpeg", content=content
    )


def assert_outcome_unknown(error, object_key=None):
    unknown = getattr(library_postgres, "CloudLibraryCommitOutcomeUnknownError", None)
    assert unknown is not None and isinstance(error, unknown), repr(error)
    assert isinstance(error, library_postgres.CloudLibraryUnavailableError)
    if object_key is not None:
        assert error.object_key == object_key


# ---------------------------------------------------------------- Task A (High)


# A-1: a failure before COMMIT is sent is a confirmed rollback; only then is the
# fresh, never-referenced object compensated.
def test_failure_before_commit_rolls_back_and_compensates_fresh_object(
    monkeypatch, postgres_repository
):
    repo = postgres_repository
    item = repo.create_for_owner(OWNER_A, lesson())
    deletes = record_physical_delete(monkeypatch, repo)

    def fail_on_insert(statement):
        if statement.startswith(MEDIA_INSERT):
            raise psycopg.OperationalError("synthetic failure before COMMIT")

    instrument(monkeypatch, repo, fail_on_insert)
    with pytest.raises(psycopg.OperationalError):
        attach(repo, OWNER_A, item.id, b"rolled-back-take")

    assert len(deletes) == 1
    assert files_for(repo, OWNER_A, item.id) == set()
    assert ready_keys(_dsn(), item.id) == []
    assert outbox_rows(_dsn(), item.id) == set()


# A-2: COMMIT succeeded on the server but its acknowledgement was lost. READY
# metadata references the object, so it must be kept and the outcome reported.
def test_commit_acknowledgement_lost_keeps_referenced_object(
    monkeypatch, postgres_repository
):
    repo = postgres_repository
    item = repo.create_for_owner(OWNER_A, lesson())
    deletes = record_physical_delete(monkeypatch, repo)
    original = repo._owner_transaction

    @contextmanager
    def ack_lost(owner_user_id):
        with original(owner_user_id) as connection:
            proxy = _Connection(connection, lambda _statement: None)
            yield proxy
        # The real COMMIT above has succeeded; the client never learns that.
        if any(s.startswith(MEDIA_INSERT) for s in proxy.statements):
            raise psycopg.OperationalError(
                "synthetic: connection lost before the COMMIT acknowledgement"
            )

    monkeypatch.setattr(repo, "_owner_transaction", ack_lost)
    with pytest.raises(Exception) as info:
        attach(repo, OWNER_A, item.id, b"committed-take")
    monkeypatch.setattr(repo, "_owner_transaction", original)

    referenced = ready_keys(_dsn(), item.id)
    assert len(referenced) == 1
    # Core assertion: the object READY metadata references is never deleted.
    assert deletes == []
    assert files_for(repo, OWNER_A, item.id) == set(referenced)
    assert_outcome_unknown(info.value, referenced[0])
    with _as_owner(OWNER_A, repo):
        playback = client.get(f"/api/library/items/{item.id}/audio")
    assert playback.status_code == 200 and playback.content == b"committed-take"


# A-3: the COMMIT outcome is unknown and the database is unavailable. Nothing may
# be inferred from the database, so the object is kept without any further query;
# here the commit did not happen, and the unreferenced object is only reported.
def test_commit_outcome_unknown_with_database_down_keeps_object_and_asks_nothing(
    monkeypatch, postgres_repository
):
    repo = postgres_repository
    item = repo.create_for_owner(OWNER_A, lesson())
    deletes = record_physical_delete(monkeypatch, repo)
    original = repo._owner_transaction
    state = {"down": False, "attempts_after_failure": 0}

    @contextmanager
    def lost_then_down(owner_user_id):
        if state["down"]:
            state["attempts_after_failure"] += 1
            raise psycopg.OperationalError("synthetic: database unavailable")
        with original(owner_user_id) as connection:
            proxy = _Connection(connection, lambda _statement: None)
            yield proxy
            if any(s.startswith(MEDIA_INSERT) for s in proxy.statements):
                # The connection drops while COMMIT is outstanding: the server
                # aborts, but the client cannot tell, and the database is gone.
                connection.close()
                state["down"] = True
                raise psycopg.OperationalError("synthetic: connection lost during COMMIT")

    monkeypatch.setattr(repo, "_owner_transaction", lost_then_down)
    with pytest.raises(Exception) as info:
        attach(repo, OWNER_A, item.id, b"uncertain-take")
    monkeypatch.setattr(repo, "_owner_transaction", original)

    kept = files_for(repo, OWNER_A, item.id)
    assert deletes == [] and len(kept) == 1
    assert_outcome_unknown(info.value, next(iter(kept)))
    assert state["attempts_after_failure"] == 0
    # Reported, not cleaned: the commit did not happen, so the kept object is an
    # unreferenced orphan with no outbox row. This round performs no cleanup.
    assert ready_keys(_dsn(), item.id) == []
    assert outbox_rows(_dsn(), item.id) == set()


# A-4: a normal successful commit references the object and removes nothing.
def test_normal_commit_attaches_without_any_physical_delete(
    monkeypatch, postgres_repository
):
    repo = postgres_repository
    item = repo.create_for_owner(OWNER_A, lesson())
    deletes = record_physical_delete(monkeypatch, repo)

    attached = attach(repo, OWNER_A, item.id, b"normal-take")

    assert attached.persistent_audio.status == "available"
    assert deletes == []
    assert files_for(repo, OWNER_A, item.id) == set(ready_keys(_dsn(), item.id))
    assert outbox_rows(_dsn(), item.id) == set()
    with _as_owner(OWNER_A, repo):
        playback = client.get(f"/api/library/items/{item.id}/audio")
    assert playback.status_code == 200 and playback.content == b"normal-take"


# A-5: replacing existing audio still records the previous object as pending,
# also when the replace's COMMIT acknowledgement is lost.
@pytest.mark.parametrize("ack_lost", [False, True], ids=["normal", "ack-lost"])
def test_replace_records_previous_object_pending(
    monkeypatch, postgres_repository, ack_lost
):
    repo = postgres_repository
    item = repo.create_for_owner(OWNER_A, lesson())
    attach(repo, OWNER_A, item.id, b"first-take")
    (first,) = ready_keys(_dsn(), item.id)
    deletes = record_physical_delete(monkeypatch, repo)
    original = repo._owner_transaction

    @contextmanager
    def maybe_ack_lost(owner_user_id):
        with original(owner_user_id) as connection:
            proxy = _Connection(connection, lambda _statement: None)
            yield proxy
        if ack_lost and any(s.startswith(MEDIA_INSERT) for s in proxy.statements):
            raise psycopg.OperationalError("synthetic: COMMIT acknowledgement lost")

    monkeypatch.setattr(repo, "_owner_transaction", maybe_ack_lost)
    if ack_lost:
        with pytest.raises(Exception) as info:
            attach(repo, OWNER_A, item.id, b"second-take")
    else:
        attach(repo, OWNER_A, item.id, b"second-take")
    monkeypatch.setattr(repo, "_owner_transaction", original)

    (second,) = ready_keys(_dsn(), item.id)
    assert second != first
    assert deletes == []
    assert files_for(repo, OWNER_A, item.id) == {first, second}
    assert outbox_rows(_dsn(), item.id) == pending(OWNER_A, item.id, [first])
    if ack_lost:
        assert_outcome_unknown(info.value, second)


# -------------------------------------------------------------- Task B (Medium)


def run_concurrent_attaches(monkeypatch, repo, admin_dsn, item_id, pause, owner=OWNER_A):
    """Start two attaches together; pause the first at ``pause.marker``."""
    instrument(monkeypatch, repo, pause)
    barrier = threading.Barrier(2)
    workers = [
        Worker(f"attach-{n}", lambda n=n: attach(repo, owner, item_id, f"take-{n}".encode()), barrier)
        for n in (1, 2)
    ]
    for worker in workers:
        worker.start()
    assert pause.paused.wait(TIMEOUT)
    others = [w for w in workers if w.name != pause.first]
    seen: list[str] = []
    wait_until(
        lambda: competitor_blocked_or_through(admin_dsn, pause, *others, seen=seen),
        "the competing attach to block on the item lock or pass the paused statement",
    )
    pause.release.set()
    for worker in workers:
        worker.join(TIMEOUT)
        assert not worker.is_alive()
    return workers, seen[-1]


def assert_one_ready_and_rest_pending(repo, admin_dsn, item_id, owner=OWNER_A):
    (ready,) = ready_keys(admin_dsn, item_id)
    files = files_for(repo, owner, item_id)
    assert ready in files
    # Every object on disk is either the one READY object or has a pending row.
    assert outbox_rows(admin_dsn, item_id) == pending(owner, item_id, files - {ready})
    return ready, files


# B-1: two first attaches start together. Both succeed, exactly one object is
# READY, and the object the later commit replaced is queued as pending.
def test_concurrent_first_attaches_serialize_on_the_item(
    monkeypatch, postgres_repository
):
    repo = postgres_repository
    item = repo.create_for_owner(OWNER_A, lesson())
    deletes = record_physical_delete(monkeypatch, repo)

    workers, observed = run_concurrent_attaches(
        monkeypatch, repo, _dsn(), item.id, PauseFirst(MEDIA_INSERT)
    )

    assert [w.error for w in workers] == [None, None]
    ready, files = assert_one_ready_and_rest_pending(repo, _dsn(), item.id)
    assert len(files) == 2 and deletes == []
    assert observed == "lock-wait"  # serialized on the item row, not by luck


# B-2: concurrent replaces of existing audio: one READY object, both replaced
# objects pending, every file still on disk.
def test_concurrent_replaces_of_existing_audio(monkeypatch, postgres_repository):
    repo = postgres_repository
    item = repo.create_for_owner(OWNER_A, lesson())
    attach(repo, OWNER_A, item.id, b"original-take")
    deletes = record_physical_delete(monkeypatch, repo)

    workers, observed = run_concurrent_attaches(
        monkeypatch, repo, _dsn(), item.id, PauseFirst(MEDIA_INSERT)
    )

    assert [w.error for w in workers] == [None, None]
    ready, files = assert_one_ready_and_rest_pending(repo, _dsn(), item.id)
    assert len(files) == 3 and deletes == []
    assert observed == "lock-wait"


# B-3a: an attach holds the item lock when a Library delete starts. The delete
# waits, then queues the freshly attached object; nothing is unlinked.
def test_delete_waits_for_inflight_attach_and_queues_its_object(
    monkeypatch, postgres_repository
):
    repo = postgres_repository
    item = repo.create_for_owner(OWNER_A, lesson())
    deletes = record_physical_delete(monkeypatch, repo)
    pause = PauseFirst(MEDIA_INSERT)
    instrument(monkeypatch, repo, pause)

    attacher = Worker("attach", lambda: attach(repo, OWNER_A, item.id, b"attach-take"))
    attacher.start()
    assert pause.paused.wait(TIMEOUT)
    deleter = Worker("delete", lambda: repo.delete_for_owner(OWNER_A, item.id))
    deleter.start()
    seen: list[str] = []
    wait_until(
        lambda: competitor_blocked_or_through(_dsn(), pause, deleter, seen=seen),
        "the delete to block on the item lock or finish",
    )
    pause.release.set()
    for worker in (attacher, deleter):
        worker.join(TIMEOUT)

    assert deleter.error is None
    files = files_for(repo, OWNER_A, item.id)
    assert len(files) == 1 and deletes == []
    assert ready_keys(_dsn(), item.id) == []
    assert outbox_rows(_dsn(), item.id) == pending(OWNER_A, item.id, files)
    with pytest.raises(KeyError):
        repo.get_for_owner(OWNER_A, item.id)
    assert seen[-1] == "lock-wait"


# B-3b: a Library delete holds the item lock when an attach starts. The attach
# waits, finds the item gone, rolls back before COMMIT and compensates only its
# own fresh object; no metadata, no outbox row and no orphan remain.
def test_attach_waits_for_inflight_delete_and_compensates_fresh_object(
    monkeypatch, postgres_repository
):
    repo = postgres_repository
    item = repo.create_for_owner(OWNER_A, lesson())
    deletes = record_physical_delete(monkeypatch, repo)
    pause = PauseFirst(ITEM_DELETE)
    instrument(monkeypatch, repo, pause)

    deleter = Worker("delete", lambda: repo.delete_for_owner(OWNER_A, item.id))
    deleter.start()
    assert pause.paused.wait(TIMEOUT)
    attacher = Worker("attach", lambda: attach(repo, OWNER_A, item.id, b"late-take"))
    attacher.start()
    seen: list[str] = []
    wait_until(
        lambda: competitor_blocked_or_through(_dsn(), pause, attacher, seen=seen),
        "the attach to block on the item lock or finish",
    )
    pause.release.set()
    for worker in (deleter, attacher):
        worker.join(TIMEOUT)

    assert deleter.error is None
    assert isinstance(attacher.error, KeyError)
    assert len(deletes) == 1
    assert files_for(repo, OWNER_A, item.id) == set()
    assert ready_keys(_dsn(), item.id) == []
    assert outbox_rows(_dsn(), item.id) == set()
    assert seen[-1] == "lock-wait"


# B-4: of two concurrent first attaches the lock holder fails before COMMIT; the
# waiting attach then proceeds, and a retry of the failed one is a normal replace.
def test_concurrent_attach_failure_then_retry(monkeypatch, postgres_repository):
    repo = postgres_repository
    item = repo.create_for_owner(OWNER_A, lesson())
    deletes = record_physical_delete(monkeypatch, repo)
    pause = PauseFirst(MEDIA_INSERT, fail_first=True)

    workers, observed = run_concurrent_attaches(monkeypatch, repo, _dsn(), item.id, pause)

    failed = next(w for w in workers if w.name == pause.first)
    survivor = next(w for w in workers if w.name != pause.first)
    assert isinstance(failed.error, psycopg.OperationalError)
    assert survivor.error is None
    assert len(deletes) == 1  # only the failed attempt's never-referenced object
    (survivor_key,) = ready_keys(_dsn(), item.id)
    assert outbox_rows(_dsn(), item.id) == set()

    attach(repo, OWNER_A, item.id, b"retry-take")
    ready, files = assert_one_ready_and_rest_pending(repo, _dsn(), item.id)
    assert files - {ready} == {survivor_key} and len(deletes) == 1
    assert observed == "lock-wait"
