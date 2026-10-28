"""Controlled dictionary cutover, rollback, and legacy conversion.

Each mutating operation:

1. validates everything read-only first (and stops there in dry-run);
2. stops the LaunchAgent and confirms it is gone;
3. hashes learning stores without reading their contents into logs;
4. switches ``dictionary_current`` with one atomic symlink replacement;
5. restarts and runs dictionary-aware smoke checks through the live backend;
6. re-hashes learning stores and requires exact equality;
7. on any failure after the stop, restores the previous link and plist,
   restarts the previous configuration, and verifies it.

Nothing here deletes a release, a database, or a learning file.
"""

import fcntl
import hashlib
import json
import os
import plistlib
import signal
import sqlite3
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from services.dictionary.launch_agent import (
    BackendClient,
    LaunchAgentError,
    ServiceController,
    expected_dictionary_environment,
    migrated_plist,
    plist_dictionary_environment,
    read_plist,
    wait_until,
    write_plist_atomic,
)
from services.dictionary.legacy_release import (
    LegacyConversionError,
    create_legacy_rollback_release,
    legacy_fingerprint,
    read_legacy_provenance,
)
from services.dictionary.release_manifest import (
    ManifestValidationError,
    validate_published_release,
    validate_rollback_release,
)
from services.dictionary.release_store import (
    DICTIONARY_NAME,
    GIB,
    ReleaseLayout,
    ReleaseStoreError,
    current_build_id,
    existing_release,
    fsync_directory,
    require_free_space,
    switch_current,
)

LEARNING_STORE_FILES = (
    "study_bank.json",
    "chunk_bank.json",
    "learning_library.json",
    "speaking_sessions.json",
)
LEARNING_MEDIA_DIRECTORY = "library_media"
MIGRATIONS_DIRNAME = "dictionary_migrations"
LOCK_NAME = ".dictionary-migration.lock"
EMPTY_LEARNING_STORES = {
    "study_bank.json": {"version": 3, "items": []},
    "chunk_bank.json": {"version": 2, "chunks": []},
    "learning_library.json": {"version": 3, "items": []},
    "speaking_sessions.json": {"version": 1, "sessions": []},
}


class LearningDriftError(RuntimeError):
    """Learning stores changed while the dictionary was being switched."""


class CutoverError(RuntimeError):
    """A cutover or rollback did not complete.

    ``recovered`` is True when the previous configuration was restored and
    verified, False when manual recovery is required, and None when nothing
    had been changed yet.
    """

    def __init__(self, message: str, *, recovered: bool | None = None):
        super().__init__(message)
        self.recovered = recovered


# --------------------------------------------------------------------- hashing

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _entry(path: Path, *, empty_store: object | None = None) -> dict:
    if path.is_symlink():
        raise LearningDriftError(f"learning-data path is a symlink: {path.name}")
    if not path.exists():
        return {"type": "missing"}
    if path.is_dir():
        return {"type": "directory"}
    stat_result = path.stat()
    result = {
        "type": "file",
        "size_bytes": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "sha256": _sha256(path),
    }
    if empty_store is not None:
        try:
            result["known_empty_default"] = json.loads(
                path.read_text(encoding="utf-8")
            ) == empty_store
        except (OSError, UnicodeError, json.JSONDecodeError):
            result["known_empty_default"] = False
    return result


def learning_snapshot(storage: Path) -> dict:
    """Existence, size, mtime and SHA-256 of every learning store; never contents."""
    files = {
        name: _entry(storage / name, empty_store=EMPTY_LEARNING_STORES[name])
        for name in LEARNING_STORE_FILES
    }
    media_root = storage / LEARNING_MEDIA_DIRECTORY
    media = {}
    if media_root.is_dir() and not media_root.is_symlink():
        for directory, directories, filenames in os.walk(media_root, followlinks=False):
            directories.sort()
            for name in sorted([*directories, *filenames]):
                path = Path(directory) / name
                media[path.relative_to(storage).as_posix()] = _entry(path)
    return {
        "files": files,
        "media_root": _entry(media_root),
        "media": media,
    }


def snapshot_digest(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


# -------------------------------------------------------------- smoke checks

_SAMPLE_SCAN_LIMIT = 5_000


def release_samples(database: Path) -> dict[str, dict]:
    """Pick one entry per language with forms, preferring one with a Chinese value.

    Scans at most ``_SAMPLE_SCAN_LIMIT`` entries per language in id order and
    uses primary-key/index lookups only, so it stays fast on the full dictionary.
    """
    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        samples = {}
        for language in ("en", "de"):
            fallback = None
            chosen = None
            seen: set[int] = set()
            # forms has no entry_id index: walk it in rowid order (entries were
            # imported in order) with primary-key joins, and stop early.
            rows = connection.execute(
                """SELECT forms.entry_id, entries.lemma FROM forms
                   JOIN entries ON entries.id = forms.entry_id
                   WHERE entries.language = ?
                   ORDER BY forms.id LIMIT ?""",
                (language, _SAMPLE_SCAN_LIMIT * 10),
            )
            for entry_id, lemma in rows:
                if entry_id in seen:
                    continue
                seen.add(entry_id)
                if len(seen) > _SAMPLE_SCAN_LIMIT:
                    break
                senses = connection.execute(
                    "SELECT translations_json FROM senses WHERE entry_id = ? ORDER BY sense_index LIMIT 100",
                    (entry_id,),
                ).fetchall()
                if not senses:
                    continue
                if fallback is None:
                    fallback = str(lemma)
                if any('"zh-CN"' in value for (value,) in senses):
                    chosen = str(lemma)
                    break
            if chosen is None and fallback is None:
                raise CutoverError(f"release has no {language} entry suitable for smoke checks")
            samples[language] = {"word": chosen or fallback, "expects_chinese": chosen is not None}
        return samples
    finally:
        connection.close()


def observe_backend(client: BackendClient, samples: dict[str, dict]) -> dict:
    """What the running backend reports, without judging it (used as a baseline)."""
    observation: dict = {"healthy": client.healthy()}
    if not observation["healthy"]:
        return observation
    try:
        status = client.get_json("/api/dictionary/status")
        observation.update({
            "configured": status.get("configured"),
            "release_id": status.get("release_id"),
            "forms_index_status": status.get("forms_index_status"),
        })
        lookups = {}
        for language, sample in samples.items():
            response = client.post_json("/api/dictionary/lookup", {
                "language": language,
                "word": sample["word"],
                "sentence": "",
                "include_inflections": True,
            })
            chinese = (response.get("explanations") or {}).get("chinese") or {}
            lookups[language] = {
                "status": response.get("status"),
                "inflections_status": response.get("inflections_status"),
                "chinese_status": chinese.get("status"),
            }
        observation["lookups"] = lookups
    except Exception as exc:  # noqa: BLE001 - an observation never raises
        observation["error"] = type(exc).__name__
    return observation


def smoke_check(client: BackendClient, *, expected_release_id: str, samples: dict[str, dict]) -> dict:
    if not client.healthy():
        raise CutoverError("backend /api/health is not healthy")
    status = client.get_json("/api/dictionary/status")
    if status.get("configured") is not True:
        raise CutoverError("backend reports the dictionary is not configured")
    if status.get("release_id") != expected_release_id:
        raise CutoverError("backend is not serving the expected dictionary release")
    if status.get("forms_index_status") != "available":
        raise CutoverError("backend forms Sidecar is not available")
    results = {}
    for language, sample in samples.items():
        response = client.post_json("/api/dictionary/lookup", {
            "language": language,
            "word": sample["word"],
            "sentence": "",
            "include_inflections": True,
        })
        if response.get("status") not in {"found", "ambiguous"}:
            raise CutoverError(f"{language} dictionary smoke lookup failed")
        if response.get("inflections_status") != "available":
            raise CutoverError(f"{language} inflections are not available")
        chinese = (response.get("explanations") or {}).get("chinese") or {}
        if sample["expects_chinese"] and chinese.get("status") != "available":
            raise CutoverError(f"{language} Chinese explanation level is unavailable")
        results[language] = {
            "status": response.get("status"),
            "inflections_status": response.get("inflections_status"),
            "chinese_status": chinese.get("status"),
        }
    return {"release_id": status["release_id"], "lookups": results}


# -------------------------------------------------------------- bookkeeping

def _utc(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _migration_lock(storage: Path):
    lock_path = storage / LOCK_NAME
    if lock_path.is_symlink():
        raise CutoverError("migration lock path is an unexpected symlink")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CutoverError("another dictionary migration is already running") from exc
        yield
    finally:
        os.close(descriptor)


class MigrationRecord:
    """Per-run evidence directory (mode 0700) containing hashes, never contents."""

    def __init__(self, storage: Path, operation: str, build_id: str, now: datetime):
        root = storage / MIGRATIONS_DIRNAME
        if root.is_symlink():
            raise CutoverError("dictionary_migrations must not be a symlink")
        root.mkdir(mode=0o700, exist_ok=True)
        stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"{stamp}-{operation}-{build_id}"
        path = root / base
        suffix = 1
        while path.exists() or path.is_symlink():
            suffix += 1
            path = root / f"{base}-{suffix}"
        path.mkdir(mode=0o700)
        self.path = path
        self.events: list[dict] = []

    def write(self, name: str, payload: bytes, *, mode: int = 0o600) -> None:
        target = self.path / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

    def write_json(self, name: str, value: object) -> None:
        self.write(name, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())

    def event(self, step: str, **details) -> None:
        self.events.append({"step": step, **details})


# ------------------------------------------------------------- the operation

class _Transition:
    """Shared stop → switch → start → verify → (recover) state machine."""

    def __init__(
        self,
        *,
        layout: ReleaseLayout,
        controller: ServiceController,
        client: BackendClient,
        plist_path: Path,
        record: MigrationRecord,
        sleep: Callable[[float], None],
        health_attempts: int,
        fault: Callable[[str], None],
    ):
        self.layout = layout
        self.controller = controller
        self.client = client
        self.plist_path = plist_path
        self.record = record
        self.sleep = sleep
        self.health_attempts = health_attempts
        self.fault = fault
        self.was_loaded = False
        self.stopped = False
        self.previous_link: str | None = None
        self.switch_attempted = False
        self.original_plist: bytes | None = None
        self.plist_changed = False
        self.before: dict | None = None

    def stop(self) -> None:
        self.was_loaded = self.controller.is_loaded()
        self.stopped = True
        self.controller.stop()
        if self.controller.is_loaded():
            raise CutoverError("LaunchAgent is still loaded after stop")
        if self.client.healthy():
            raise CutoverError("a backend still answers on 127.0.0.1:8000 after the LaunchAgent stopped")
        self.record.event("service_stopped", was_loaded=self.was_loaded)

    def snapshot_learning(self) -> None:
        self.before = learning_snapshot(self.layout.storage)
        self.record.write_json("learning-before.json", self.before)

    def learning_unchanged(self, name: str) -> bool:
        after = learning_snapshot(self.layout.storage)
        self.record.write_json(name, after)
        return learning_preserved(self.before, after)

    def verify_learning(self, name: str) -> None:
        if not self.learning_unchanged(name):
            raise LearningDriftError("learning-store hashes changed during the dictionary migration")

    def switch(self, build_id: str) -> None:
        self.previous_link = current_build_id(self.layout)
        self.fault("before_switch")
        # Mark the intent first: switch_current can fail after os.replace
        # (directory fsync, re-check), and recovery must then still restore.
        self.switch_attempted = True
        switch_current(self.layout, build_id, _before_replace=lambda: self.fault("during_switch"))
        self.record.event("switched", to=build_id, previous=self.previous_link)
        self.fault("after_switch")

    def install_plist(self, payload: bytes) -> None:
        self.original_plist = read_plist(self.plist_path)
        if payload == self.original_plist:
            return
        self.record.write("launch-agent.before.plist", self.original_plist)
        self.plist_changed = True
        write_plist_atomic(self.plist_path, payload)
        self.record.event("plist_updated")

    def start_and_verify(self, *, release_id: str, samples: dict, environment: dict | None) -> dict:
        self.fault("before_start")
        self.controller.start(self.plist_path)
        if not wait_until(self.client.healthy, attempts=self.health_attempts, sleep=self.sleep):
            raise CutoverError("backend did not become healthy after restart")
        if environment is not None:
            loaded = self.controller.loaded_environment()
            mismatched = [key for key, value in environment.items() if loaded.get(key) != value]
            if mismatched:
                raise CutoverError(
                    "running LaunchAgent environment does not use the versioned dictionary paths: "
                    + ", ".join(mismatched)
                )
        self.fault("before_smoke")
        result = smoke_check(self.client, expected_release_id=release_id, samples=samples)
        self.fault("after_smoke")
        return result

    def restore_link(self) -> None:
        """Return ``dictionary_current`` to exactly what it was before the switch."""
        if not self.switch_attempted:
            return
        link = self.layout.current_link
        if self.previous_link is None:
            # First cutover: there was no link. Remove the one this run created
            # (a symlink owns no data) so nothing later mistakes it for the
            # served release.
            if link.is_symlink():
                link.unlink()
                fsync_directory(self.layout.storage)
            self.record.event("link_removed")
            return
        try:
            actual = current_build_id(self.layout)
        except ReleaseStoreError:
            actual = None
        if actual != self.previous_link:
            switch_current(self.layout, self.previous_link)
        self.record.event("link_restored", to=self.previous_link)

    def recover(self, *, baseline: dict | None, samples: dict, environment: dict | None) -> dict:
        """Restore the previous link/plist and prove the old configuration serves again."""
        try:
            self.controller.stop()
        except LaunchAgentError:
            pass
        self.restore_link()
        if self.plist_changed and self.original_plist is not None:
            write_plist_atomic(self.plist_path, self.original_plist)
            self.record.event("plist_restored")
        if not self.was_loaded:
            self.record.event("service_left_stopped")
            return {"restarted": False, "learning_unchanged": self._recovery_learning()}
        self.controller.start(self.plist_path)
        if not wait_until(self.client.healthy, attempts=self.health_attempts, sleep=self.sleep):
            raise CutoverError("previous configuration did not become healthy")
        if environment is not None:
            loaded = self.controller.loaded_environment()
            if any(loaded.get(key) != value for key, value in environment.items()):
                raise CutoverError("previous LaunchAgent environment was not restored")
        observed = observe_backend(self.client, samples)
        if baseline is not None and observed != baseline:
            raise CutoverError("restored configuration does not answer like it did before the migration")
        return {"restarted": True, "observed": observed, "learning_unchanged": self._recovery_learning()}

    def _recovery_learning(self) -> bool:
        if self.before is None:
            return True
        unchanged = self.learning_unchanged("learning-after-recovery.json")
        if not unchanged:
            self.record.event("learning_drift_preserved_as_evidence")
        return unchanged


def learning_preserved(before: dict | None, after: dict) -> bool:
    """Exact equality, except that a store missing before may now exist.

    Backend start-up creates empty default stores (and ``library_media``) when
    they are absent; that is not a change to existing learning data.
    """
    if before is None:
        return True
    for name, entry in before["files"].items():
        now = after["files"].get(name)
        if entry != now and not (
            entry["type"] == "missing"
            and now
            and now["type"] == "file"
            and now.get("known_empty_default") is True
        ):
            return False
    if before["media_root"] != after["media_root"] and not (
        before["media_root"]["type"] == "missing" and after["media_root"]["type"] == "directory"
    ):
        return False
    if before["media_root"]["type"] == "missing":
        return not after["media"] or after["media"] == before["media"]
    return before["media"] == after["media"]


@contextmanager
def _deferred_signals():
    """Hold SIGINT/SIGTERM/SIGHUP while the service is stopped.

    An interrupted cutover would otherwise leave the service down and half
    switched. Signals are recorded and reported once the run has finished or
    recovered.
    """
    received: list[int] = []
    if threading.current_thread() is not threading.main_thread():
        yield received
        return
    previous = {}

    def handler(signum, frame):  # noqa: ARG001
        received.append(signum)

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is not None:
            previous[number] = signal.signal(number, handler)
    try:
        yield received
    finally:
        for number, original in previous.items():
            signal.signal(number, original)


def _lock_unless_dry_run(storage: Path, dry_run: bool):
    return nullcontext() if dry_run else _migration_lock(storage)


def _require_confirmation(dry_run: bool, confirmation: str | None, expected: str) -> None:
    if not dry_run and confirmation != expected:
        raise CutoverError(f'Owner confirmation is required: pass --confirm "{expected}"')


def _candidate_release(layout: ReleaseLayout, build_id: str, source_root: Path) -> dict:
    release = existing_release(layout, build_id)
    if (release / "build-failure.json").exists():
        raise CutoverError("candidate recorded a build failure and cannot be selected")
    try:
        return validate_published_release(release, layout.releases, source_root)
    except (ManifestValidationError, OSError) as exc:
        raise CutoverError(f"candidate is not a valid published release: {exc}") from exc


def _legacy_environment(plist_bytes: bytes) -> dict | None:
    """The dictionary variables the original plist used (None = backend defaults)."""
    environment = plistlib.loads(plist_bytes).get("EnvironmentVariables", {}) or {}
    values = {key: environment[key] for key in ("DICTIONARY_DB_PATH", "DICTIONARY_FORMS_INDEX_PATH") if key in environment}
    return values or None


def _file_identities(release: Path) -> dict:
    """Cheap stat identity of every file, for post-stop revalidation."""
    return {
        child.name: (child.stat().st_size, child.stat().st_mtime_ns, child.stat().st_ino)
        for child in sorted(release.iterdir())
    }


def _legacy_identity(path: Path) -> tuple:
    wal = Path(f"{path}-wal")
    stat_result = path.stat()
    wal_stat = wal.stat() if wal.exists() else None
    return (
        stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_ino,
        None if wal_stat is None else (wal_stat.st_size, wal_stat.st_mtime_ns, wal_stat.st_ino),
    )


def _samples(database: Path) -> dict:
    try:
        return release_samples(database)
    except sqlite3.Error as exc:
        raise CutoverError(f"dictionary cannot be read for smoke checks: {type(exc).__name__}") from exc


def previously_served(layout: ReleaseLayout, build_id: str) -> bool:
    """True if a completed migration record shows ``build_id`` was served."""
    root = layout.storage / MIGRATIONS_DIRNAME
    if not root.is_dir() or root.is_symlink():
        return False
    for record in root.iterdir():
        path = record / "result.json"
        if record.is_symlink() or path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("result") == "complete" and value.get("build_id") == build_id:
            return True
    return False


def _finish_failure(
    *,
    exc: BaseException,
    transition: "_Transition",
    record: MigrationRecord,
    operation: str,
    baseline: dict | None,
    samples: dict,
    environment: dict | None,
) -> CutoverError:
    """Recover after any failure (including KeyboardInterrupt) and build the error."""
    record.event("failed", error_type=type(exc).__name__, message=str(exc)[:500])
    if not transition.stopped:
        record.write_json("result.json", {"result": "failed_before_change", "operation": operation,
                                          "events": record.events})
        return CutoverError(str(exc) or type(exc).__name__, recovered=None)
    try:
        recovery = transition.recover(baseline=baseline, samples=samples, environment=environment)
    except BaseException as recovery_exc:  # noqa: BLE001
        record.event("recovery_failed", error_type=type(recovery_exc).__name__, message=str(recovery_exc)[:500])
        safe_stop_error = None
        try:
            transition.controller.stop()
            if transition.controller.is_loaded() or transition.client.healthy():
                raise CutoverError("service still responds after final safe stop")
            record.event("final_safe_stop_verified")
        except BaseException as stop_exc:  # noqa: BLE001
            safe_stop_error = f"{type(stop_exc).__name__}: {stop_exc}"[:500]
            record.event("final_safe_stop_failed", message=safe_stop_error)
        record.write_json("result.json", {
            "result": "recovery_failed",
            "operation": operation,
            "final_safe_stop_verified": safe_stop_error is None,
            "final_safe_stop_error": safe_stop_error,
            "events": record.events,
        })
        return CutoverError(
            f"{operation} failed ({exc or type(exc).__name__}) and automatic recovery failed "
            f"({recovery_exc}); see {record.path.name} and the runbook manual-recovery section",
            recovered=False,
        )
    clean = recovery["learning_unchanged"]
    record.write_json("result.json", {
        "result": "rolled_back" if clean else "rolled_back_learning_drift",
        "operation": operation,
        "recovery": recovery,
        "events": record.events,
    })
    if not clean:
        return CutoverError(
            f"{operation} failed and the previous dictionary configuration was restored, but "
            f"learning-store hashes differ from the pre-migration snapshot; investigate using "
            f"{record.path.name}",
            recovered=False,
        )
    return CutoverError(f"{operation} failed and was rolled back: {exc or type(exc).__name__}", recovered=True)


def run_cutover(
    *,
    layout: ReleaseLayout,
    source_root: Path,
    build_id: str,
    confirmation: str | None,
    controller: ServiceController,
    client: BackendClient,
    plist_path: Path,
    legacy_database: Path,
    dry_run: bool = False,
    now: datetime | None = None,
    minimum_free_bytes: int = 2 * GIB,
    health_attempts: int = 30,
    sleep: Callable[[float], None] = time.sleep,
    _fault: Callable[[str], None] | None = None,
) -> dict:
    fault = _fault or (lambda stage: None)
    now = now or datetime.now(timezone.utc)
    _require_confirmation(dry_run, confirmation, f"CUTOVER {build_id}")
    with _lock_unless_dry_run(layout.storage, dry_run):
        # ---- read-only revalidation (all heavy work happens before stop) ----
        try:
            manifest = _candidate_release(layout, build_id, source_root)
            rollback_id = manifest["rollback"]["previous_build_id"]
            rollback_release = validate_rollback_release(layout.releases, rollback_id)
            current = current_build_id(layout)
        except (ManifestValidationError, ReleaseStoreError, OSError) as exc:
            raise CutoverError(str(exc)) from exc
        if current == build_id:
            raise CutoverError("candidate is already the current release")
        legacy_mode = current is None
        if not legacy_mode and current != rollback_id:
            raise CutoverError("candidate's rollback release is not the current release")
        try:
            require_free_space(layout.storage, minimum_free_bytes)
            original_plist = read_plist(plist_path)
            new_environment = expected_dictionary_environment(layout.current_link)
            new_plist = migrated_plist(original_plist, new_environment)
            plist_environment = plist_dictionary_environment(original_plist)
        except (ReleaseStoreError, LaunchAgentError) as exc:
            raise CutoverError(str(exc)) from exc
        if not legacy_mode and plist_environment != new_environment:
            raise CutoverError(
                "dictionary_current exists but the LaunchAgent does not use it; "
                "inspect with 'status' before migrating"
            )
        if legacy_mode and plist_environment == new_environment:
            raise CutoverError("LaunchAgent already points at dictionary_current, but no link exists")
        provenance = None
        legacy_identity = None
        if legacy_mode:
            provenance = read_legacy_provenance(rollback_release)
            if not rollback_id.startswith("legacy-") or provenance is None:
                raise CutoverError("first cutover requires a verified legacy rollback release")
            try:
                live = legacy_fingerprint(legacy_database)
                legacy_identity = _legacy_identity(legacy_database)
            except (LegacyConversionError, OSError) as exc:
                raise CutoverError(str(exc)) from exc
            if live["state_sha256"] != provenance["legacy_database"]["state_sha256"]:
                raise CutoverError("installed legacy dictionary no longer matches its rollback release")
        rollback_identity = _file_identities(rollback_release)
        candidate_samples = _samples(existing_release(layout, build_id) / DICTIONARY_NAME)
        previous_samples = _samples(legacy_database if legacy_mode else rollback_release / DICTIONARY_NAME)
        previous_environment = _legacy_environment(original_plist) if legacy_mode else new_environment
        plan = {
            "operation": "cutover",
            "build_id": build_id,
            "rollback_build_id": rollback_id,
            "current_build_id": current,
            "legacy_mode": legacy_mode,
            "plist_change_required": new_plist != original_plist,
            "service_loaded": controller.is_loaded(),
            "dry_run": dry_run,
        }
        if dry_run:
            return {**plan, "action": "would_cutover"}

        record = MigrationRecord(layout.storage, "cutover", build_id, now)
        record.write_json("plan.json", plan)
        transition = _Transition(
            layout=layout, controller=controller, client=client, plist_path=plist_path,
            record=record, sleep=sleep, health_attempts=health_attempts, fault=fault,
        )
        baseline = observe_backend(client, previous_samples) if controller.is_loaded() else None
        record.write_json("baseline-observation.json", baseline)
        with _deferred_signals() as signals:
            try:
                transition.stop()
                transition.snapshot_learning()
                # Cheap final revalidation with writers stopped.
                if _file_identities(rollback_release) != rollback_identity:
                    raise CutoverError("rollback release changed before cutover")
                if legacy_mode and _legacy_identity(legacy_database) != legacy_identity:
                    raise CutoverError("installed legacy dictionary changed before cutover")
                transition.switch(build_id)
                transition.install_plist(new_plist)
                smoke = transition.start_and_verify(
                    release_id=build_id, samples=candidate_samples, environment=new_environment,
                )
                transition.verify_learning("learning-after.json")
            except BaseException as exc:  # noqa: BLE001 - every failure after stop must recover
                error = _finish_failure(
                    exc=exc, transition=transition, record=record, operation="cutover",
                    baseline=baseline, samples=previous_samples, environment=previous_environment,
                )
                raise error from exc
        record.write_json("result.json", {
            "result": "complete", "operation": "cutover", "build_id": build_id,
            "smoke": smoke, "deferred_signals": signals, "events": record.events,
        })
        return {**plan, "action": "cutover_complete", "smoke": smoke, "record": record.path.name,
                "deferred_signals": signals}


def run_rollback(
    *,
    layout: ReleaseLayout,
    build_id: str,
    confirmation: str | None,
    controller: ServiceController,
    client: BackendClient,
    plist_path: Path,
    dry_run: bool = False,
    now: datetime | None = None,
    health_attempts: int = 30,
    sleep: Callable[[float], None] = time.sleep,
    _fault: Callable[[str], None] | None = None,
) -> dict:
    """Switch ``dictionary_current`` back to an earlier, previously served release."""
    fault = _fault or (lambda stage: None)
    now = now or datetime.now(timezone.utc)
    _require_confirmation(dry_run, confirmation, f"ROLLBACK {build_id}")
    with _lock_unless_dry_run(layout.storage, dry_run):
        try:
            current = current_build_id(layout)
            if current is None:
                raise CutoverError("rollback requires dictionary_current; legacy mode has nothing to roll back")
            if current == build_id:
                raise CutoverError("requested rollback target is already current")
            target = validate_rollback_release(layout.releases, build_id)
            current_release = validate_rollback_release(layout.releases, current)
            plist = read_plist(plist_path)
        except (ManifestValidationError, ReleaseStoreError, LaunchAgentError, OSError) as exc:
            raise CutoverError(str(exc)) from exc
        if not build_id.startswith("legacy-") and not previously_served(layout, build_id):
            raise CutoverError(
                "rollback target has never been served by a completed migration; use cutover for new releases"
            )
        environment = expected_dictionary_environment(layout.current_link)
        try:
            if plist_dictionary_environment(plist) != environment:
                raise CutoverError("LaunchAgent does not use dictionary_current; reinstall before rollback")
        except (LaunchAgentError, ValueError) as exc:
            raise CutoverError(str(exc)) from exc
        target_samples = _samples(target / DICTIONARY_NAME)
        current_samples = _samples(current_release / DICTIONARY_NAME)
        plan = {
            "operation": "rollback",
            "build_id": build_id,
            "current_build_id": current,
            "service_loaded": controller.is_loaded(),
            "dry_run": dry_run,
        }
        if dry_run:
            return {**plan, "action": "would_rollback"}
        record = MigrationRecord(layout.storage, "rollback", build_id, now)
        record.write_json("plan.json", plan)
        transition = _Transition(
            layout=layout, controller=controller, client=client, plist_path=plist_path,
            record=record, sleep=sleep, health_attempts=health_attempts, fault=fault,
        )
        baseline = observe_backend(client, current_samples) if controller.is_loaded() else None
        record.write_json("baseline-observation.json", baseline)
        with _deferred_signals() as signals:
            try:
                transition.stop()
                transition.snapshot_learning()
                transition.switch(build_id)
                smoke = transition.start_and_verify(
                    release_id=build_id, samples=target_samples, environment=environment,
                )
                transition.verify_learning("learning-after.json")
            except BaseException as exc:  # noqa: BLE001
                error = _finish_failure(
                    exc=exc, transition=transition, record=record, operation="rollback",
                    baseline=baseline, samples=current_samples, environment=environment,
                )
                raise error from exc
        record.write_json("result.json", {
            "result": "complete", "operation": "rollback", "build_id": build_id,
            "smoke": smoke, "deferred_signals": signals, "events": record.events,
        })
        return {**plan, "action": "rollback_complete", "smoke": smoke, "record": record.path.name,
                "deferred_signals": signals}


def run_legacy_conversion(
    *,
    layout: ReleaseLayout,
    legacy_database: Path,
    controller: ServiceController,
    client: BackendClient,
    plist_path: Path,
    dry_run: bool = False,
    now: datetime | None = None,
    health_attempts: int = 30,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Stop the backend, create/verify ``legacy-<sha>``, restart the unchanged config."""
    now = now or datetime.now(timezone.utc)
    try:
        plan = create_legacy_rollback_release(layout, legacy_database, dry_run=True, now=now)
    except (LegacyConversionError, ReleaseStoreError, OSError) as exc:
        raise CutoverError(str(exc)) from exc
    loaded = controller.is_loaded()
    if dry_run:
        return {**plan, "operation": "legacy_rollback", "service_loaded": loaded}
    if not layout.releases.is_dir():
        layout.releases.mkdir(mode=0o755)
    with _migration_lock(layout.storage):
        record = MigrationRecord(layout.storage, "legacy-rollback", plan["build_id"], now)
        result = None
        error: BaseException | None = None
        before = None
        with _deferred_signals() as signals:
            try:
                if loaded:
                    controller.stop()
                    if controller.is_loaded() or client.healthy():
                        raise CutoverError("backend did not stop; legacy conversion was not attempted")
                before = learning_snapshot(layout.storage)
                record.write_json("learning-before.json", before)
                result = create_legacy_rollback_release(layout, legacy_database, now=now)
            except BaseException as exc:  # noqa: BLE001 - restart on every path
                error = exc
                record.event("failed", error_type=type(exc).__name__, message=str(exc)[:500])
            healthy = True
            if loaded:
                try:
                    controller.start(plist_path)
                    healthy = wait_until(client.healthy, attempts=health_attempts, sleep=sleep)
                except BaseException as exc:  # noqa: BLE001
                    record.event("restart_failed", error_type=type(exc).__name__, message=str(exc)[:500])
                    healthy = False
            preserved = True
            if before is not None:
                after = learning_snapshot(layout.storage)
                record.write_json("learning-after.json", after)
                preserved = learning_preserved(before, after)
        outcome = (
            "restart_failed" if not healthy
            else "learning_drift" if not preserved
            else "failed" if error is not None
            else result["action"]
        )
        record.write_json("result.json", {
            "result": outcome, "operation": "legacy_rollback", "build_id": plan["build_id"],
            "deferred_signals": signals, "events": record.events,
        })
        if not healthy:
            raise CutoverError(
                "backend did not become healthy after legacy conversion; see the runbook "
                "manual-recovery section", recovered=False,
            ) from error
        if not preserved:
            raise CutoverError(
                f"learning-store hashes changed during legacy conversion; investigate using {record.path.name}",
                recovered=False,
            ) from error
        if error is not None:
            raise CutoverError(
                f"legacy conversion failed; the backend was restarted unchanged: {error or type(error).__name__}",
                recovered=True if loaded else None,
            ) from error
        return {**result, "operation": "legacy_rollback", "service_loaded": loaded,
                "record": record.path.name, "deferred_signals": signals}
