"""Bounded worker for the cloud Library media cleanup outbox (B-1, Issue #58).

Run on a schedule, as often as every minute:

    python -m services.library.cleanup_worker [--batch 50] [--max-batches 20]

Needs CLOUD_LIBRARY_DATABASE_URL and CLOUD_LIBRARY_MEDIA_ROOT; the root must
already carry the marker the app writes, so a wrong or unmounted path is
refused instead of "cleaning" an empty directory. Several runs
may overlap safely: rows are claimed with SKIP LOCKED, and each object is
deleted while its outbox row is locked. A crash simply leaves the row to come
back after its backoff. Output carries counts only, never keys, owners or
paths. Exit status: 0 done, 1 database unavailable (rerun later), 3 the
backlog holds dead rows that need an operator (see docs/P8-B1-LIBRARY-RELIABILITY.md).
"""
import argparse
import os
import sys

from services.library.postgres import (
    CLEANUP_OUTCOMES,
    MAX_CLEANUP_BATCH,
    CloudLibraryUnavailableError,
    FilesystemMediaObjectStore,
    MediaRootUnavailableError,
    PostgresOwnerScopedLibraryRepository,
)


def run_cleanup(repository, *, batch=50, max_batches=20):
    """Drain at most ``batch * max_batches`` due rows; return summed counts."""
    if not 1 <= batch <= MAX_CLEANUP_BATCH or not 1 <= max_batches <= 1000:
        raise ValueError("Cleanup bounds out of range")
    totals = {outcome: 0 for outcome in CLEANUP_OUTCOMES}
    for _ in range(max_batches):
        counts = repository.run_cleanup_batch(limit=batch)
        for outcome, count in counts.items():
            totals[outcome] += count
        if counts["claimed"] < batch:
            break
    return totals


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Clean up orphaned cloud Library media."
    )
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--max-batches", type=int, default=20)
    args = parser.parse_args(argv)
    dsn = os.getenv("CLOUD_LIBRARY_DATABASE_URL", "").strip()
    media_root = os.getenv("CLOUD_LIBRARY_MEDIA_ROOT", "").strip()
    if not dsn:
        parser.error("CLOUD_LIBRARY_DATABASE_URL is not set")
    if not media_root:
        parser.error("CLOUD_LIBRARY_MEDIA_ROOT is not set")
    try:
        media_objects = FilesystemMediaObjectStore(media_root, create=False)
    except MediaRootUnavailableError:
        print(
            "cleanup refused: CLOUD_LIBRARY_MEDIA_ROOT is not an initialised media root",
            file=sys.stderr,
        )
        return 1
    repository = PostgresOwnerScopedLibraryRepository(dsn, media_objects)
    try:
        counts = run_cleanup(
            repository, batch=args.batch, max_batches=args.max_batches
        )
        backlog = repository.cleanup_backlog()
    except ValueError as exc:
        parser.error(str(exc))
    except CloudLibraryUnavailableError:
        print(
            "cleanup failed: database unavailable; claimed rows retry after backoff",
            file=sys.stderr,
        )
        return 1
    counts.update({f"backlog_{name}": count for name, count in backlog.items()})
    print(" ".join(f"{name}={count}" for name, count in counts.items()))
    return 3 if backlog["dead"] else 0


if __name__ == "__main__":
    sys.exit(main())
