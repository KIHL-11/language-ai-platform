"""Personal Desktop backup / verify / isolated restore, on synthetic data only."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.backup import personal
from services.backup.personal import BackupInvalid, BackupRefused
from services.library.store import LearningLibraryStore
from services.retrieval.chunk_store import ChunkStore
from services.schemas.speaking import SpeakingContext, SpeakingEventRequest
from services.speaking.store import SpeakingSessionStore
from services.study_bank.store import StudyBankStore
import test_chunk_quality as chunk_tests
import test_learning_library as library_tests
import test_study_bank as study_tests

STAMP = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "personal_backup.py"


def _tree_state(root: Path) -> dict[str, tuple[str, int]]:
    """sha256 + mtime of every file: proves the live storage is never modified."""
    return {
        p.relative_to(root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture
def storage(tmp_path):
    """A synthetic Personal storage directory with every dataset populated."""
    root = tmp_path / "live" / "storage"
    root.mkdir(parents=True)
    library = LearningLibraryStore(root / "learning_library.json", root / "library_media", tmp_path / "media")
    item = library.save_item(library_tests.lesson())
    library.update_progress(item.id, library_tests.progress(stage=2, sentence=0))
    (root / "library_media" / "synthetic.mp3").write_bytes(b"ID3" + bytes(range(256)) * 4)

    study = StudyBankStore(root / "study_bank.json")
    saved, _ = study.save_items([study_tests.study_input(), study_tests.study_input("chunk")])
    study.rate_item("learner-one", saved[0].id, "good", 0)
    study.rate_item("learner-one", saved[1].id, "again", 0)

    speaking = SpeakingSessionStore(root / "speaking_sessions.json")
    context = SpeakingContext(
        language="en", level="B2", mode="review_weak", session_goal="Practice",
        target_chunks=[{"study_item_id": saved[1].id, "text": saved[1].text, "meaning": saved[1].meaning}],
        support_words=[], media_sentences=[], system_prompt="SYSTEM INSTRUCTIONS",
    )
    session = speaking.create(user_id="learner-one", provider="topic-mock", model="topic-mock", duration=300,
                              context=context, goal=None)
    speaking.append_event("learner-one", session.id, SpeakingEventRequest(
        expected_event_count=0, type="turn", role="learner", text="A synthetic turn."))

    ChunkStore(root / "chunk_bank.json").add_chunks([
        chunk_tests.stored_chunk("chunk-1", "as far as I know", "as far as i know"),
        chunk_tests.stored_chunk("chunk-2", "it depends on", "it depends on"),
    ])

    database = sqlite3.connect(root / "dictionary.sqlite3")
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("CREATE TABLE entries (word TEXT PRIMARY KEY, meaning TEXT)")
    database.executemany("INSERT INTO entries VALUES (?, ?)", [("weather", "天气"), ("depend", "取决于")])
    database.commit()
    database.close()
    return root


def _backup(storage: Path, tmp_path: Path, **kwargs):
    kwargs.setdefault("port", None)
    kwargs.setdefault("confirm_app_stopped", True)
    return personal.create_backup(storage, tmp_path / "backups", now=STAMP, **kwargs)


# BK-1 ------------------------------------------------------------------------

def test_backup_is_complete_hashed_and_verifiable(storage, tmp_path):
    before = _tree_state(storage)
    result = _backup(storage, tmp_path)
    assert result.path.name == "personal-backup-20261114T120000Z"
    assert not (tmp_path / "backups" / "personal-backup-20261114T120000Z.partial").exists()
    metadata = personal.verify_backup(result.path)
    assert metadata["summary"]["library"]["count"] == 1
    assert metadata["summary"]["study_bank"]["count"] == 2
    assert metadata["summary"]["speaking"] == {**metadata["summary"]["speaking"], "count": 1, "events": 1}
    assert metadata["summary"]["chunk_memory"]["count"] == 2
    sqlite_summary = metadata["summary"]["sqlite"]["dictionary.sqlite3"]
    assert sqlite_summary["integrity"] == "ok" and sqlite_summary["rows"] == {"entries": 2}
    assert len(sqlite_summary["logical_sha256"]) == 64
    copied = {p.relative_to(result.path / "storage").as_posix() for p in (result.path / "storage").rglob("*") if p.is_file()}
    assert copied == {"learning_library.json", "library_media/synthetic.mp3", "study_bank.json",
                      "speaking_sessions.json", "chunk_bank.json", "dictionary.sqlite3"}
    for line in (result.path / "MANIFEST.sha256").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        assert hashlib.sha256((result.path / relative).read_bytes()).hexdigest() == digest
    assert _tree_state(storage) == before


# BK-2 ------------------------------------------------------------------------

def test_restore_into_new_directory_loads_identical_records_and_srs_state(storage, tmp_path):
    original_study = StudyBankStore(storage / "study_bank.json")._read_items()
    result = _backup(storage, tmp_path)
    before = _tree_state(storage)
    target = tmp_path / "restore-check"
    report = personal.restore_backup(result.path, target, live_storage=storage)
    assert report["matches_backup"] is True
    restored_study = StudyBankStore(target / "study_bank.json")._read_items()
    assert [(i.id, i.review) for i in restored_study] == [(i.id, i.review) for i in original_study]
    assert {i.review.last_rating for i in restored_study} == {"good", "again"}
    library = LearningLibraryStore(target / "learning_library.json", target / "library_media", tmp_path / "x")
    assert library.list_items()[0].progress.stage_index == 2
    assert (target / "library_media" / "synthetic.mp3").read_bytes() == (storage / "library_media" / "synthetic.mp3").read_bytes()
    database = sqlite3.connect(target / "dictionary.sqlite3")
    assert database.execute("SELECT meaning FROM entries WHERE word='weather'").fetchone() == ("天气",)
    database.close()
    assert _tree_state(storage) == before


# BK-3 ------------------------------------------------------------------------

def test_backup_refuses_while_the_app_is_listening(storage, tmp_path):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        with pytest.raises(BackupRefused, match="stop the app before backing up"):
            _backup(storage, tmp_path, port=port)
    assert not (tmp_path / "backups").exists()


# BK-4 ------------------------------------------------------------------------

@pytest.mark.parametrize("victim", ["storage/study_bank.json", "storage/library_media/synthetic.mp3", "backup.json"])
def test_a_single_flipped_byte_fails_verification_and_blocks_restore(storage, tmp_path, victim):
    result = _backup(storage, tmp_path)
    path = result.path / victim
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(BackupInvalid, match="Checksum mismatch"):
        personal.verify_backup(result.path)
    with pytest.raises(BackupInvalid):
        personal.restore_backup(result.path, tmp_path / "restore", live_storage=storage)
    assert not (tmp_path / "restore").exists()


def test_missing_or_extra_files_fail_verification(storage, tmp_path):
    result = _backup(storage, tmp_path)
    (result.path / "storage" / "unexpected.json").write_text("{}")
    with pytest.raises(BackupInvalid, match="Files not in manifest"):
        personal.verify_backup(result.path)
    (result.path / "storage" / "unexpected.json").unlink()
    (result.path / "storage" / "chunk_bank.json").unlink()
    with pytest.raises(BackupInvalid, match="Files missing from backup"):
        personal.verify_backup(result.path)


def test_restored_data_that_no_longer_loads_identically_is_rejected(storage, tmp_path, monkeypatch):
    result = _backup(storage, tmp_path)
    real = personal.summarize

    def drifted(directory):
        summary = real(directory)
        summary["study_bank"]["srs_digest"] = "0" * 64
        return summary

    monkeypatch.setattr(personal, "summarize", drifted)
    with pytest.raises(BackupInvalid, match="study_bank"):
        personal.restore_backup(result.path, tmp_path / "restore", live_storage=storage)


# BK-5 ------------------------------------------------------------------------

def test_existing_backup_is_never_overwritten(storage, tmp_path):
    first = _backup(storage, tmp_path)
    marker = _tree_state(first.path)
    with pytest.raises(BackupRefused, match="already exists"):
        _backup(storage, tmp_path)
    assert _tree_state(first.path) == marker


def test_destination_inside_storage_is_refused(storage, tmp_path):
    with pytest.raises(BackupRefused, match="outside the storage directory"):
        personal.create_backup(storage, storage / "backups", port=None, now=STAMP, confirm_app_stopped=True)
    assert not (storage / "backups").exists()


@pytest.mark.parametrize("where", ["live", "inside_live", "existing"])
def test_restore_never_targets_live_storage_or_an_existing_directory(storage, tmp_path, where):
    result = _backup(storage, tmp_path)
    before = _tree_state(storage)
    target = {"live": storage, "inside_live": storage / "restored",
              "existing": tmp_path / "already-here"}[where]
    if where == "existing":
        target.mkdir()
    with pytest.raises(BackupRefused):
        personal.restore_backup(result.path, target, live_storage=storage)
    assert _tree_state(storage) == before
    assert not (storage / "restored").exists()


# Consistency -----------------------------------------------------------------

def test_storage_changing_during_the_copy_discards_the_backup(storage, tmp_path, monkeypatch):
    real_copy = personal._copy_bytes
    touched = []

    def copy_then_write(source, target):
        result = real_copy(source, target)
        if not touched:
            touched.append(source)
            with open(storage / "study_bank.json", "a", encoding="utf-8") as handle:
                handle.write(" ")
        return result

    monkeypatch.setattr(personal, "_copy_bytes", copy_then_write)
    with pytest.raises(BackupRefused, match="Storage changed while the backup was running"):
        _backup(storage, tmp_path)
    assert list((tmp_path / "backups").iterdir()) == []


def test_symbolic_links_in_storage_are_refused(storage, tmp_path):
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("not learning data")
    os.symlink(outside, storage / "library_media" / "link.mp3")
    with pytest.raises(BackupRefused, match="Symbolic link"):
        _backup(storage, tmp_path)


def test_sqlite_wal_content_is_captured_without_copying_wal_files(storage, tmp_path):
    database = sqlite3.connect(storage / "dictionary.sqlite3")
    database.execute("PRAGMA wal_autocheckpoint=0")
    database.execute("INSERT INTO entries VALUES ('reunion', '重聚')")
    database.commit()
    try:
        assert (storage / "dictionary.sqlite3-wal").exists()
        result = _backup(storage, tmp_path)
    finally:
        database.close()
    names = {p.name for p in (result.path / "storage").iterdir()}
    assert not {"dictionary.sqlite3-wal", "dictionary.sqlite3-shm"} & names
    copy = sqlite3.connect(result.path / "storage" / "dictionary.sqlite3")
    assert copy.execute("SELECT count(*) FROM entries").fetchone()[0] == 3
    copy.close()


def test_empty_storage_backs_up_and_restores(tmp_path):
    root = tmp_path / "empty-storage"
    root.mkdir()
    result = personal.create_backup(root, tmp_path / "b", port=None, now=STAMP, confirm_app_stopped=True)
    assert result.summary == {}
    assert personal.restore_backup(result.path, tmp_path / "r", live_storage=root)["matches_backup"]


# CLI -------------------------------------------------------------------------

def _cli(*args):
    env = {**os.environ, "PYTHONPATH": str(SCRIPT.parents[1])}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          env=env, timeout=120)


def test_cli_backup_verify_restore_round_trip(storage, tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]  # nothing listens once closed
    before = _tree_state(storage)
    made = _cli("backup", "--confirm-app-stopped", "--storage", str(storage), "--dest", str(tmp_path / "cli"),
                "--port", str(free_port))
    assert made.returncode == 0, made.stdout + made.stderr
    backup_path = json.loads(made.stdout)["backup"]
    assert json.loads(made.stdout)["datasets"]["study_bank"] == 2
    assert _cli("verify", "--backup", backup_path).returncode == 0
    restored = _cli("restore", "--backup", backup_path, "--target", str(tmp_path / "cli-restore"),
                    "--live-storage", str(storage))
    assert restored.returncode == 0, restored.stdout
    assert json.loads(restored.stdout)["matches_backup"] is True
    refused = _cli("restore", "--backup", backup_path, "--target", str(storage), "--live-storage", str(storage))
    assert refused.returncode == 2
    assert _tree_state(storage) == before


# ----------------------------------------------------------------------------
# Remediation batch 2 (independent review CHANGES_REQUIRED). Test IDs PRP-R*.
# ----------------------------------------------------------------------------

def _no_restore_staging_left(parent: Path, name: str) -> bool:
    return not list(parent.glob(f".{name}.restore-staging-*"))


def _flip_middle_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))


# PRP-R1: the operator confirmation is the precondition, not the port probe.
def test_prp_r1_backup_requires_explicit_operator_confirmation(storage, tmp_path):
    for value in (False, None, "yes", 1):
        with pytest.raises(BackupRefused, match="confirm it explicitly"):
            personal.create_backup(storage, tmp_path / "backups", port=None, now=STAMP,
                                   confirm_app_stopped=value)
    assert not (tmp_path / "backups").exists()
    refused = _cli("backup", "--storage", str(storage), "--dest", str(tmp_path / "cli"), "--port", "1")
    assert refused.returncode == 2
    assert "confirm it explicitly" in refused.stdout
    assert not (tmp_path / "cli").exists()


# PRP-R2: the best-effort probe also covers loopback IPv6.
def test_prp_r2_port_probe_covers_ipv6_loopback(storage, tmp_path):
    try:
        server = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        server.bind(("::1", 0))
    except OSError:
        pytest.skip("IPv6 loopback is not available on this host")
    with server:
        server.listen()
        port = server.getsockname()[1]
        assert personal.service_is_listening(port)
        with pytest.raises(BackupRefused, match="listening on loopback port"):
            _backup(storage, tmp_path, port=port)
    assert not (tmp_path / "backups").exists()


def _corrupting_copy(monkeypatch, *, when):
    real_copy = personal._copy_bytes

    def copy(source, target):
        digest = real_copy(source, target)
        if when(Path(source)):
            _flip_middle_byte(Path(target))
        return digest

    monkeypatch.setattr(personal, "_copy_bytes", copy)


# PRP-R3: media bytes corrupted while being written into the backup.
def test_prp_r3_backup_rejects_media_corrupted_during_copy(storage, tmp_path, monkeypatch):
    _corrupting_copy(monkeypatch, when=lambda source: source.suffix == ".mp3")
    with pytest.raises(BackupInvalid, match="Copy verification failed for synthetic.mp3"):
        _backup(storage, tmp_path)
    assert list((tmp_path / "backups").iterdir()) == []


# PRP-R4: the SQLite copy produced by the backup API is damaged.
@pytest.mark.parametrize("damage", ["page_bytes", "content"])
def test_prp_r4_backup_rejects_sqlite_copy_corruption(storage, tmp_path, monkeypatch, damage):
    real = personal._sqlite_backup

    def damaged(staged, target):
        real(staged, target)
        if damage == "page_bytes":
            data = bytearray(target.read_bytes())
            data[100:4096] = b"\xff" * (4096 - 100)
            target.write_bytes(bytes(data))
        else:  # valid database, wrong content
            connection = sqlite3.connect(target)
            connection.execute("DELETE FROM entries WHERE word = 'weather'")
            connection.commit()
            connection.close()

    monkeypatch.setattr(personal, "_sqlite_backup", damaged)
    with pytest.raises(BackupInvalid, match="SQLite copy of dictionary.sqlite3"):
        _backup(storage, tmp_path)
    assert list((tmp_path / "backups").iterdir()) == []


# PRP-R5: a failure halfway through the backup keeps nothing.
def test_prp_r5_backup_midway_failure_keeps_nothing(storage, tmp_path, monkeypatch):
    real_copy = personal._copy_bytes
    calls = []

    def failing(source, target):
        calls.append(source)
        if len(calls) == 3:
            raise OSError("synthetic disk failure")
        return real_copy(source, target)

    monkeypatch.setattr(personal, "_copy_bytes", failing)
    with pytest.raises(OSError, match="synthetic disk failure"):
        _backup(storage, tmp_path)
    assert list((tmp_path / "backups").iterdir()) == []


# PRP-R6: media bytes corrupted while being restored; nothing is published.
def test_prp_r6_restore_media_corruption_is_never_published(storage, tmp_path, monkeypatch):
    result = _backup(storage, tmp_path)
    before = _tree_state(storage)
    _corrupting_copy(monkeypatch, when=lambda source: source.suffix == ".mp3")
    target = tmp_path / "restore"
    with pytest.raises(BackupInvalid, match="Copy verification failed for synthetic.mp3"):
        personal.restore_backup(result.path, target, live_storage=storage)
    assert not target.exists()
    assert _no_restore_staging_left(tmp_path, "restore")
    assert _tree_state(storage) == before


# PRP-R7: a SQLite file damaged inside the backup, even with a recomputed
# manifest, is caught by integrity_check / the logical digest; not published.
@pytest.mark.parametrize("damage", ["page_bytes", "content"])
def test_prp_r7_restore_sqlite_corruption_is_never_published(storage, tmp_path, damage):
    result = _backup(storage, tmp_path)
    database = result.path / "storage" / "dictionary.sqlite3"
    if damage == "page_bytes":
        data = bytearray(database.read_bytes())
        data[100:4096] = b"\xff" * (4096 - 100)
        database.write_bytes(bytes(data))
    else:
        connection = sqlite3.connect(database)
        connection.execute("UPDATE entries SET meaning = 'tampered' WHERE word = 'weather'")
        connection.commit()
        connection.close()
    personal._write_manifest(result.path)  # an attacker or bug that also rewrote the manifest
    personal.verify_backup(result.path)  # hashes alone now agree
    target = tmp_path / "restore"
    with pytest.raises(BackupInvalid, match="sqlite"):
        personal.restore_backup(result.path, target, live_storage=storage)
    assert not target.exists()
    assert _no_restore_staging_left(tmp_path, "restore")


# PRP-R8: a failure halfway through the restore publishes nothing.
def test_prp_r8_restore_midway_failure_is_never_published(storage, tmp_path, monkeypatch):
    result = _backup(storage, tmp_path)
    real_copy = personal._copy_bytes
    calls = []

    def failing(source, target):
        calls.append(source)
        if len(calls) == 2:
            raise OSError("synthetic disk failure")
        return real_copy(source, target)

    monkeypatch.setattr(personal, "_copy_bytes", failing)
    target = tmp_path / "restore"
    with pytest.raises(OSError, match="synthetic disk failure"):
        personal.restore_backup(result.path, target, live_storage=storage)
    assert not target.exists()
    assert _no_restore_staging_left(tmp_path, "restore")


# PRP-R9: every restored file is re-hashed against the manifest, and the
# summary load must not change the tree before it is published.
def test_prp_r9_restored_tree_equals_manifest_before_and_after_loading(storage, tmp_path, monkeypatch):
    result = _backup(storage, tmp_path)
    manifest_files = [line.split("  ", 1)[1] for line in
                      (result.path / "MANIFEST.sha256").read_text().splitlines() if line.strip()]
    report = personal.restore_backup(result.path, tmp_path / "good", live_storage=storage)
    storage_files = [name for name in manifest_files if name.startswith("storage/")]
    assert report["files_verified"] == len(storage_files) == 6
    for name in storage_files:
        relative = name[len("storage/"):]
        assert hashlib.sha256((tmp_path / "good" / relative).read_bytes()).hexdigest() == \
            hashlib.sha256((result.path / name).read_bytes()).hexdigest()

    real_summarize = personal.summarize

    def summarize_with_side_effect(directory):
        summary = real_summarize(directory)
        (directory / "side-effect.json").write_text("{}")
        return summary

    monkeypatch.setattr(personal, "summarize", summarize_with_side_effect)
    with pytest.raises(BackupInvalid, match="Unexpected restored files: side-effect.json"):
        personal.restore_backup(result.path, tmp_path / "bad", live_storage=storage)
    assert not (tmp_path / "bad").exists()
    assert _no_restore_staging_left(tmp_path, "bad")


# PRP-R10: a backup file changed between verify_backup and the copy is caught
# by comparing the bytes read to the manifest digest.
def test_prp_r10_restore_detects_source_changed_after_verification(storage, tmp_path, monkeypatch):
    result = _backup(storage, tmp_path)
    real_verify = personal.verify_backup

    def verify_then_tamper(backup):
        metadata = real_verify(backup)
        _flip_middle_byte(backup / "storage" / "chunk_bank.json")
        return metadata

    monkeypatch.setattr(personal, "verify_backup", verify_then_tamper)
    with pytest.raises(BackupInvalid, match="Source changed or was corrupt while copying chunk_bank.json"):
        personal.restore_backup(result.path, tmp_path / "restore", live_storage=storage)
    assert not (tmp_path / "restore").exists()
    assert _no_restore_staging_left(tmp_path, "restore")
