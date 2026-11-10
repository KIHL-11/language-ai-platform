"""POSIX fork guard counterexamples; synthetic files and deterministic pipes."""
from __future__ import annotations

from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import threading

import pytest

from services.library import source_guard

pytestmark = pytest.mark.skipif(
    not hasattr(os, "fork") or source_guard.fcntl is None,
    reason="requires POSIX fork/flock",
)
TIMEOUT = 10


def _reply(connection):
    assert connection.poll(TIMEOUT), "child did not respond (possible inherited-lock deadlock)"
    message = connection.recv()
    assert message[0] != "fatal", message
    return message


def _child(connection, inherited, library_path):
    held = None
    try:
        while True:
            command = connection.recv()
            if command == "quit":
                return
            try:
                if command == "probe":
                    acquired = inherited.acquire(blocking=False)
                    if acquired:
                        inherited.release()
                    connection.send(("probe", acquired, getattr(inherited, "_pid", None)))
                elif command == "fresh_probe":
                    guard = source_guard.guard_for(Path(library_path))
                    acquired = guard.acquire(blocking=False)
                    if acquired:
                        guard.release()
                    connection.send(("fresh_probe", acquired))
                elif command == "release_inherited":
                    inherited.release()
                    connection.send(("released",))
                elif command == "hold_fresh":
                    held = source_guard.guard_for(Path(library_path))
                    held.acquire()
                    connection.send(("held",))
                elif command == "release_held":
                    held.release()
                    held = None
                    connection.send(("released_held",))
                elif command == "same_guard":
                    a = source_guard.guard_for(Path(library_path))
                    b = source_guard.guard_for(Path(library_path).parent / "." / Path(library_path).name)
                    connection.send(("same_guard", a is b))
                elif command == "write":
                    from services.library.store import LearningLibraryStore
                    from services.schemas.lesson import Lesson
                    root = Path(library_path).parent
                    store = LearningLibraryStore(library_path, media_root=root / "media",
                                                 source_media_root=root / "source")
                    connection.send(("writing",))
                    store.save_item(Lesson.model_validate({
                        "id": "fork-child-lesson", "title": "Synthetic child",
                        "source_url": "https://example.invalid/fork-child",
                        "source_language": "en", "target_language": "zh-CN",
                        "level": "B2", "source": "subtitles",
                        "sentences": [{"id": 1, "text": "A synthetic child line.",
                                       "start": 0, "end": 2}],
                        "ai": {"provider": "mock", "status": "complete"},
                    }))
                    connection.send(("written",))
                else:
                    raise AssertionError(command)
            except RuntimeError as exc:
                connection.send(("rejected", str(exc)))
    except BaseException as exc:
        connection.send(("fatal", type(exc).__name__, str(exc)))
    finally:
        if held is not None:
            held.release()
        connection.close()


@contextmanager
def _fork(guard, path):
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe()
    process = context.Process(target=_child, args=(child, guard, str(path)), daemon=True)
    process.start()
    child.close()
    try:
        yield parent
    finally:
        if process.is_alive():
            parent.send("quit")
        process.join(TIMEOUT)
        if process.is_alive():
            process.terminate()
            process.join(TIMEOUT)
        parent.close()
        assert process.exitcode == 0, f"child failed or deadlocked: {process.exitcode}"


def test_guard_created_before_fork_cannot_bypass_parent_lock(tmp_path):
    path = tmp_path / "library.json"
    guard = source_guard.guard_for(path)
    with _fork(guard, path) as connection:
        with guard:
            connection.send("probe")
            response = _reply(connection)
            assert response[:2] == ("probe", False)
            assert response[2] != os.getpid()
        connection.send("hold_fresh")
        assert _reply(connection) == ("held",)
        assert guard.acquire(blocking=False) is False
        connection.send("release_held")
        assert _reply(connection) == ("released_held",)
        with guard:
            pass


def test_fork_inside_parent_critical_section_is_rejected_not_reentered(tmp_path):
    path = tmp_path / "library.json"
    guard = source_guard.guard_for(path)
    with guard:
        with _fork(guard, path) as connection:
            connection.send("probe")
            response = _reply(connection)
            assert response[0] == "rejected" and "fork" in response[1].lower()
            connection.send("release_inherited")
            assert _reply(connection)[0] == "rejected"
            # A newly constructed guard is independent and still cannot enter.
            connection.send("fresh_probe")
            assert _reply(connection) == ("fresh_probe", False)
            # Child close/release must not unlock the parent's open description.
            with open(guard.lock_path, "a+b") as independent:
                assert source_guard._lock_file(independent, False) is False
    with guard:
        pass


def test_fork_with_library_lock_owned_by_another_thread_recovers(tmp_path):
    path = tmp_path / "library.json"
    guard = source_guard.guard_for(path)
    entered, finish = threading.Event(), threading.Event()

    def holder():
        with guard:
            entered.set()
            assert finish.wait(TIMEOUT)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert entered.wait(TIMEOUT)
    try:
        with _fork(guard, path) as connection:
            connection.send("fresh_probe")
            assert _reply(connection) == ("fresh_probe", False)
            finish.set()
            thread.join(TIMEOUT)
            assert not thread.is_alive()
            connection.send("hold_fresh")
            assert _reply(connection) == ("held",)
            connection.send("release_held")
            assert _reply(connection) == ("released_held",)
    finally:
        finish.set()
        thread.join(TIMEOUT)


def test_fork_does_not_take_an_inherited_registry_mutex(tmp_path):
    path = tmp_path / "library.json"
    guard = source_guard.guard_for(path)
    entered, finish = threading.Event(), threading.Event()

    def holder():
        with source_guard._guards_lock:
            entered.set()
            assert finish.wait(TIMEOUT)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert entered.wait(TIMEOUT)
    try:
        with _fork(guard, path) as connection:
            connection.send("same_guard")
            assert _reply(connection) == ("same_guard", True)
    finally:
        finish.set()
        thread.join(TIMEOUT)
        assert not thread.is_alive()


def test_unowned_child_release_does_not_release_parent_lock(tmp_path):
    path = tmp_path / "library.json"
    guard = source_guard.guard_for(path)
    with _fork(guard, path) as connection:
        with guard:
            connection.send("release_inherited")
            assert _reply(connection)[0] == "rejected"
            with open(guard.lock_path, "a+b") as independent:
                assert source_guard._lock_file(independent, False) is False
        connection.send("hold_fresh")
        assert _reply(connection) == ("held",)
        assert not guard.acquire(blocking=False)
        connection.send("release_held")
        assert _reply(connection) == ("released_held",)
        with guard:
            with open(guard.lock_path, "a+b") as independent:
                assert source_guard._lock_file(independent, False) is False


def test_parent_and_forked_child_serialize_library_writes(tmp_path):
    from services.library.store import LearningLibraryStore
    from services.schemas.lesson import Lesson
    path = tmp_path / "library.json"
    store = LearningLibraryStore(path, media_root=tmp_path / "media",
                                 source_media_root=tmp_path / "source")
    with _fork(store._lock, path) as connection:
        with store._lock:
            connection.send("fresh_probe")
            assert _reply(connection) == ("fresh_probe", False)
            connection.send("write")
            assert _reply(connection) == ("writing",)
            store.save_item(Lesson.model_validate({
                "id": "fork-parent-lesson", "title": "Synthetic parent",
                "source_url": "https://example.invalid/fork-parent",
                "source_language": "en", "target_language": "zh-CN", "level": "B2",
                "source": "subtitles",
                "sentences": [{"id": 1, "text": "A synthetic parent line.", "start": 0, "end": 2}],
                "ai": {"provider": "mock", "status": "complete"},
            }))
        assert _reply(connection) == ("written",)
    assert {item.lesson.id for item in store.list_items()} == {"fork-parent-lesson", "fork-child-lesson"}


def test_exception_releases_guard_and_wrong_thread_cannot_release_it(tmp_path):
    guard = source_guard.guard_for(tmp_path / "library.json")
    with pytest.raises(ValueError, match="synthetic"):
        with guard:
            raise ValueError("synthetic")
    errors = []

    def wrong_owner():
        try:
            guard.release()
        except RuntimeError as exc:
            errors.append(str(exc))

    with guard:
        thread = threading.Thread(target=wrong_owner)
        thread.start()
        thread.join(TIMEOUT)
        assert len(errors) == 1
        with guard:  # recursion is unchanged in the owning process/thread
            pass
    assert guard.acquire(blocking=False)
    guard.release()


def test_os_acquire_exception_releases_thread_mutex(tmp_path, monkeypatch):
    guard = source_guard.guard_for(tmp_path / "library.json")
    real_lock = source_guard._lock_file

    def fail(*_args):
        raise OSError("synthetic acquire failure")

    monkeypatch.setattr(source_guard, "_lock_file", fail)
    with pytest.raises(OSError, match="synthetic"):
        guard.acquire()
    monkeypatch.setattr(source_guard, "_lock_file", real_lock)
    result = []

    def acquire_elsewhere():
        acquired = guard.acquire(blocking=False)
        result.append(acquired)
        if acquired:
            guard.release()

    thread = threading.Thread(target=acquire_elsewhere)
    thread.start()
    thread.join(TIMEOUT)
    assert not thread.is_alive()
    assert result == [True]
