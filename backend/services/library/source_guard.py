"""One guard per Personal Library file, shared by threads, store instances
and processes (R-H3).

Every `LearningLibraryStore` for the same resolved path gets the same guard
object, so a second instance cannot bypass it. The guard is a re-entrant
thread lock plus an exclusive OS file lock on `<library>.lock`, taken by the
outermost holder only, so another process using the same Library file is
excluded as well. A plain RLock alone is not cross-process safe.
"""
from __future__ import annotations

import threading
import weakref
from pathlib import Path

try:  # POSIX (macOS, Linux)
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
    import msvcrt


def _lock_file(handle, blocking: bool) -> bool:
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            return False
        return True
    # Windows: lock the first byte; LK_LOCK gives up after ~10 s, so loop.
    while True:  # pragma: no cover - not exercised on POSIX CI
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            if not blocking:
                return False


def _unlock_file(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    else:  # pragma: no cover
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class LibraryPathGuard:
    """Re-entrant within a thread; exclusive across threads and processes."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._handle = open(lock_path, "a+b")  # noqa: SIM115 - held for the guard's lifetime

    def acquire(self, blocking: bool = True) -> bool:
        if not self._thread_lock.acquire(blocking):
            return False
        if self._depth == 0:
            try:
                locked = _lock_file(self._handle, blocking)
            except BaseException:
                self._thread_lock.release()
                raise
            if not locked:
                self._thread_lock.release()
                return False
        self._depth += 1
        return True

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            _unlock_file(self._handle)
        self._thread_lock.release()

    def __enter__(self) -> "LibraryPathGuard":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()

    def __del__(self):
        handle = getattr(self, "_handle", None)
        if handle is not None:
            handle.close()


_guards: "weakref.WeakValueDictionary[str, LibraryPathGuard]" = weakref.WeakValueDictionary()
_guards_lock = threading.Lock()


def guard_for(library_path: Path) -> LibraryPathGuard:
    """The single guard for this Library file in this process."""
    resolved = Path(library_path).resolve()
    with _guards_lock:
        guard = _guards.get(str(resolved))
        if guard is None:
            guard = LibraryPathGuard(resolved.with_name(resolved.name + ".lock"))
            _guards[str(resolved)] = guard
        return guard
