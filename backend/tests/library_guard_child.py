"""Child-process helpers for the R-H3 cross-process tests (spawned, so they
must be importable module-level functions)."""
from __future__ import annotations

from pathlib import Path


def _store(library_path: str):
    from services.library.store import LearningLibraryStore

    root = Path(library_path).parent
    return LearningLibraryStore(library_path, media_root=root / "media", source_media_root=root / "source")


def hold_guard_then_delete(library_path: str, item_id: str, acquired, proceed, done) -> None:
    """Take the Library guard, report it, then (on `proceed`) delete the item
    while still holding it."""
    store = _store(library_path)
    with store._lock:
        acquired.set()
        proceed.wait()
        store.delete_item(item_id)
    done.set()


def probe_then_delete(library_path: str, item_id: str, probed, results, proceed, done) -> None:
    """Report whether the guard is free right now (non-blocking), then, on
    `proceed`, delete with a normal blocking acquire."""
    store = _store(library_path)
    got = store._lock.acquire(blocking=False)
    if got:
        store._lock.release()
    results.put(("probe_acquired", got))
    probed.set()
    proceed.wait()
    store.delete_item(item_id)
    results.put(("deleted", True))
    done.set()
