"""One guard per Personal Library file, shared by threads, store instances
and processes (R-H3).

Every `LearningLibraryStore` for the same resolved path gets the same guard
object, so a second instance cannot bypass it. The guard is a re-entrant
thread lock plus an exclusive OS file lock on `<library>.lock`, taken by the
outermost holder only, so another process using the same Library file is
excluded as well. A plain RLock alone is not cross-process safe.
"""
from __future__ import annotations

import os
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


_all_guards = weakref.WeakSet()


class LibraryPathGuard:
    """Re-entrant within a thread; exclusive across threads and processes.

    After fork, idle guards (including those held by vanished threads) get a
    fresh file description. A critical section owned by the forking thread
    cannot safely continue: its old guard rejects all operations in the child.
    Discard that inherited context and construct a new store there instead.
    """

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._pid = os.getpid()
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._owner = None
        self._invalid = None
        self._handle = open(lock_path, "a+b")  # noqa: SIM115 - held for the guard's lifetime
        _all_guards.add(self)

    def _after_fork(self) -> None:
        if self._pid == os.getpid():
            return
        # Never acquire an inherited mutex, and never LOCK_UN an inherited
        # open file description: it may still be locked by the parent.
        inherited_context = self._owner == threading.get_ident()
        self._pid = os.getpid()
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._owner = None
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                self._invalid = "Library guard could not close its inherited fork handle"
        if inherited_context:
            self._invalid = "Library guard inherited an active fork context; rebuild the store"
            return
        if self._invalid is None:
            try:
                self._handle = open(self.lock_path, "a+b")
            except OSError:
                self._invalid = "Library guard could not reopen its lock after fork; rebuild the store"

    def _check_process(self) -> None:
        # Also protects direct guard references, before touching their RLock.
        self._after_fork()
        if self._invalid is not None:
            raise RuntimeError(self._invalid)

    def acquire(self, blocking: bool = True) -> bool:
        self._check_process()
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
        self._owner = threading.get_ident()
        self._depth += 1
        return True

    def release(self) -> None:
        self._check_process()
        if self._owner != threading.get_ident() or self._depth == 0:
            raise RuntimeError("Library guard can only be released by its owning thread")
        self._depth -= 1
        try:
            if self._depth == 0:
                try:
                    _unlock_file(self._handle)
                except BaseException:
                    self._invalid = "Library guard unlock failed; rebuild the store"
                    raise
                finally:
                    self._owner = None
        finally:
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
_registry_pid = os.getpid()


def _after_fork_child() -> None:
    global _guards_lock, _registry_pid
    if _registry_pid == os.getpid():
        return
    _guards_lock = threading.Lock()
    _registry_pid = os.getpid()
    for guard in list(_all_guards):
        guard._after_fork()
    # An active inherited context must reject on the old object. A fresh
    # store may obtain a new guard which blocks normally on the parent's fd.
    for key, guard in list(_guards.items()):
        if guard._invalid is not None:
            del _guards[key]


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_child)


def guard_for(library_path: Path) -> LibraryPathGuard:
    """The single guard for this Library file in this process."""
    _after_fork_child()  # before the potentially inherited registry mutex
    resolved = Path(library_path).resolve()
    with _guards_lock:
        guard = _guards.get(str(resolved))
        if guard is None:
            guard = LibraryPathGuard(resolved.with_name(resolved.name + ".lock"))
            _guards[str(resolved)] = guard
        return guard
