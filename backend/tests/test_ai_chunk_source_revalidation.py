"""R-H3: Personal accept-time re-validation of the Library source.

A `library_sentence` candidate is accepted only if the current Library still
holds exactly the source it was generated from, and the check and the Study
Bank save happen under the same Library guard that every Library mutation
uses (threads, store instances and processes). Concurrency tests use
Events/Barriers; timeouts appear only as deadlock detectors.
"""
from __future__ import annotations

import ast
import multiprocessing
import pathlib
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
from library_guard_child import hold_guard_then_delete, probe_then_delete
from services.ai_chunks.service import AiChunkOrganizerService
from services.ai_chunks.store import CandidateConflictError, InMemoryAiChunkStore
from services.api import ai_chunk_api
from services.library.store import LearningLibraryStore
from services.retrieval.chunk_memory import ChunkMemoryService
from services.retrieval.chunk_store import ChunkStore
from services.schemas.ai_chunks import CandidateDecisionRequest, ChunkSuggestionRequest
from services.schemas.lesson import Lesson
from services.schemas.study_bank import MediaStudyProvenance, StudyItemInput, StudyItemPatch
from services.study_bank.store import StudyBankStore

NOW = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)
USER = "local-learner"
TEXT = "As far as I know, it depends on the weather."
DEADLOCK_SECONDS = 20  # failure detector only; never part of an ordering argument
BACKEND = pathlib.Path(__file__).resolve().parents[1]
client = TestClient(app_module.app)


def _lesson(text=TEXT, *, start=1.0, end=4.0, translation="据我所知。", title="Synthetic podcast",
            url="https://example.invalid/podcast", lesson_id="lesson-1", target="zh-CN"):
    return Lesson.model_validate({
        "id": lesson_id, "title": title, "source_url": url, "source_language": "en",
        "target_language": target, "level": "B2", "source": "subtitles",
        "sentences": [{"id": 1, "text": text, "translation": translation, "start": start, "end": end},
                      {"id": 2, "text": "A second synthetic line.", "translation": "", "start": 5.0, "end": 7.0}],
        "training_order": ["active_recall", "blind_listening", "comprehension", "mini_dictation",
                           "shadowing", "retell", "live_dialogue"],
        "ai": {"provider": "mock", "status": "complete"},
    })


def _library(path):
    return LearningLibraryStore(path / "library.json", media_root=path / "media",
                                source_media_root=path / "source", clock=lambda: NOW)


class World:
    def __init__(self, tmp_path, **lesson):
        self.root = tmp_path
        self.library = _library(tmp_path)
        self.item = self.library.save_item(_lesson(**lesson))
        self.study = StudyBankStore(tmp_path / "study.json", clock=lambda: NOW)
        self.chunks = ChunkStore(tmp_path / "chunks.json")
        self.memory = ChunkMemoryService(self.chunks, clock=lambda: NOW)
        self.service = AiChunkOrganizerService(self.library, self.study, store=InMemoryAiChunkStore(),
                                               chunk_memory=self.memory, clock=lambda: NOW)
        self.batch = self.service.suggest(USER, ChunkSuggestionRequest.model_validate({
            "user_id": USER, "source_language": "en", "idempotency_key": "rh3-generate-01",
            "source": {"source_type": "library_passage", "library_item_id": self.item.id, "sentence_ids": [1]},
        }))
        self.candidate = self.batch.candidates[0]

    def decide(self, action="accept", key="rh3-decision-001", edit=None):
        return self.service.decide(USER, self.batch.batch_id, CandidateDecisionRequest.model_validate({
            "user_id": USER, "candidate_id": self.candidate.candidate_id, "action": action,
            "expected_version": self.candidate.version, "idempotency_key": key, **({"edit": edit} if edit else {}),
        }))

    def files(self):
        return {name: (self.root / name).read_bytes() for name in ("study.json", "chunks.json")}

    def candidate_state(self):
        return self.service.get_batch(USER, self.batch.batch_id).candidates[0].state


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _reimport(world, **lesson):
    """Re-import the same source (same item id) with changed content."""
    return world.library.save_item(_lesson(**lesson))


# --- G3: source checks --------------------------------------------------------

def test_unchanged_source_is_accepted_with_current_provenance(world):
    result = world.decide()

    assert result.created is True
    [provenance] = result.study_item.provenance
    assert isinstance(provenance, MediaStudyProvenance)
    assert (provenance.library_item_id, provenance.sentence_text, provenance.start) == (world.item.id, TEXT, 1.0)
    assert world.candidate_state() == "accepted"


def _assert_refused_without_side_effects(world, message):
    before = world.files()
    with pytest.raises(CandidateConflictError, match=message):
        world.decide()
    assert world.files() == before  # Study Bank and Chunk Memory files byte-identical
    assert world.study.list_items(USER, kind="chunk", active=None) == []
    assert world.candidate_state() == "proposed"


def test_accept_after_the_library_item_was_deleted_is_refused(world):
    world.library.delete_item(world.item.id)

    _assert_refused_without_side_effects(world, "no longer available")


def test_accept_after_the_sentence_text_changed_is_refused(world):
    _reimport(world, text="As far as I know, it depends on the timetable.")

    _assert_refused_without_side_effects(world, "no longer matches")


@pytest.mark.parametrize("change", [
    {"start": 1.5}, {"end": 4.5}, {"translation": "另一个翻译。"}, {"title": "Renamed"},
    {"url": "https://example.invalid/podcast?v=2"}, {"lesson_id": "lesson-reimported"},
])
def test_any_changed_trusted_field_is_refused(tmp_path, change):
    world = World(tmp_path)
    updated = _lesson(**change)
    if "url" in change:  # a different URL is a different source key: replace in place
        world.library.replace_transcript(world.item.id, updated, {1: 1, 2: 2},
                                         expected_updated_at=world.library.get_item(world.item.id).updated_at)
    else:
        _reimport(world, **change)

    _assert_refused_without_side_effects(world, "no longer matches")


def test_a_missing_sentence_is_refused(world):
    current = world.library.get_item(world.item.id)
    without = current.lesson.model_copy(update={"sentences": current.lesson.sentences[1:]})
    world.library.replace_transcript(world.item.id, without, {1: None, 2: 2}, expected_updated_at=current.updated_at)

    _assert_refused_without_side_effects(world, "no longer matches")


def test_the_api_returns_409_and_the_candidate_stays_pending(world):
    world.library.delete_item(world.item.id)
    ai_chunk_api._service = world.service
    try:
        response = client.post(f"/api/ai/chunk-suggestions/{world.batch.batch_id}/decisions", json={
            "user_id": USER, "candidate_id": world.candidate.candidate_id, "action": "accept",
            "expected_version": 1, "idempotency_key": "rh3-api-decision",
        })
    finally:
        ai_chunk_api._service = None

    assert response.status_code == 409
    assert "no longer available" in response.json()["detail"]
    assert world.candidate_state() == "proposed"


def test_a_refused_accept_is_not_replayed_once_the_source_is_restored(world):
    original = world.library.get_item(world.item.id)
    _reimport(world, text="Something different.")
    with pytest.raises(CandidateConflictError):
        world.decide(key="rh3-retry-key-01")
    _reimport(world)  # restore the exact original source
    assert world.library.get_item(world.item.id).lesson == original.lesson

    assert world.decide(key="rh3-retry-key-01").created is True


def test_a_completed_decision_replays_even_after_the_source_is_deleted(world):
    first = world.decide(key="rh3-replay-key-01")
    world.library.delete_item(world.item.id)

    replay = world.decide(key="rh3-replay-key-01")

    assert replay == first


def test_edit_and_accept_is_checked_too(world):
    world.library.delete_item(world.item.id)
    with pytest.raises(CandidateConflictError):
        world.decide(action="edit_and_accept", edit={"text": "it depends on", "suggested_meaning_zh": "看情况",
                                                     "usage_note": "", "example": ""})


def test_reject_and_manual_text_keep_their_rules(world):
    world.library.delete_item(world.item.id)
    assert world.decide(action="reject").candidate.state == "rejected"

    manual = world.service.suggest(USER, ChunkSuggestionRequest.model_validate({
        "user_id": USER, "source_language": "en", "idempotency_key": "rh3-manual-0001",
        "source": {"source_type": "manual_text", "text": "As far as I know, it works."},
    }))
    accepted = world.service.decide(USER, manual.batch_id, CandidateDecisionRequest.model_validate({
        "user_id": USER, "candidate_id": manual.candidates[0].candidate_id, "action": "accept",
        "expected_version": 1, "idempotency_key": "rh3-manual-accept",
    }))
    assert accepted.created is True


def test_a_non_chinese_target_lesson_still_accepts(tmp_path):
    world = World(tmp_path, target="en")

    assert world.decide().created is True


def test_archive_manual_meaning_note_and_srs_are_preserved(world):
    [existing], _ = world.study.save_items([StudyItemInput(
        user_id=USER, kind="chunk", source_language="en", target_language="zh-CN", text="as far as I know",
        meaning="manual meaning", provenance=[MediaStudyProvenance(
            library_item_id="older", lesson_id="older", source_url="https://example.invalid/old",
            media_title="Older", sentence_id=1, sentence_text="As far as I know.", start=0, end=1)],
    )])
    rated, _ = world.study.rate_item(USER, existing.id, "good", 0)
    archived = world.study.update_item(USER, rated.id, StudyItemPatch(active=False, note="learner note"))

    result = world.decide()

    item = result.study_item
    assert result.created is False and item.id == archived.id
    assert (item.active, item.meaning, item.note, item.review) == (False, "manual meaning", "learner note", archived.review)
    assert [p.library_item_id for p in item.provenance] == ["older", world.item.id]


# --- G3: guard identity and process boundary --------------------------------------

def test_store_instances_on_one_path_share_one_guard(tmp_path):
    a, b, other = _library(tmp_path), _library(tmp_path), _library(tmp_path / "other")

    assert a._lock is b._lock
    assert a._lock is not other._lock
    assert a._lock.lock_path == (tmp_path / "library.json").resolve().with_name("library.json.lock")


def _probe_from_another_thread(guard) -> bool:
    result = []

    def probe():
        got = guard.acquire(blocking=False)
        if got:
            guard.release()
        result.append(got)

    thread = threading.Thread(daemon=True, target=probe)
    thread.start()
    thread.join(DEADLOCK_SECONDS)
    return result[0]


def _pause_study_bank_save(world, monkeypatch):
    """Make the accept stop inside the guarded section, right at the Study
    Bank save, until the test releases it."""
    reached, release = threading.Event(), threading.Event()
    real = world.study.save_items

    def paused(values):
        reached.set()
        assert release.wait(DEADLOCK_SECONDS)
        return real(values)

    monkeypatch.setattr(world.study, "save_items", paused)
    return reached, release


def test_race_accept_first_then_transcript_update(world, monkeypatch):
    reached, release = _pause_study_bank_save(world, monkeypatch)
    second_store = _library(world.root)  # another instance on the same file
    outcome = {}
    accept = threading.Thread(daemon=True, target=lambda: outcome.setdefault("accept", world.decide()))
    accept.start()
    assert reached.wait(DEADLOCK_SECONDS)

    # The accept is between validation and save: nothing may interleave.
    assert _probe_from_another_thread(second_store._lock) is False
    updater_ready = threading.Event()

    def update():
        current = second_store
        updater_ready.set()
        item = current.get_item(world.item.id)
        changed = item.lesson.model_copy(update={"sentences": [
            item.lesson.sentences[0].model_copy(update={"text": "Rewritten line."}), item.lesson.sentences[1]]})
        outcome["updated"] = current.replace_transcript(world.item.id, changed, {1: 1, 2: 2},
                                                        expected_updated_at=item.updated_at)

    updater = threading.Thread(daemon=True, target=update)
    updater.start()
    assert updater_ready.wait(DEADLOCK_SECONDS)
    release.set()
    accept.join(DEADLOCK_SECONDS)
    updater.join(DEADLOCK_SECONDS)

    assert not accept.is_alive() and not updater.is_alive()
    [provenance] = outcome["accept"].study_item.provenance
    assert provenance.sentence_text == TEXT  # saved with the source it validated
    assert world.library.get_item(world.item.id).lesson.sentences[0].text == "Rewritten line."


def test_race_transcript_update_first_then_accept(world):
    held, proceed = threading.Event(), threading.Event()
    second_store = _library(world.root)

    def update_while_holding():
        with second_store._lock:
            held.set()
            assert proceed.wait(DEADLOCK_SECONDS)
            item = second_store.get_item(world.item.id)
            changed = item.lesson.model_copy(update={"sentences": [
                item.lesson.sentences[0].model_copy(update={"start": 9.0, "end": 12.0}), item.lesson.sentences[1]]})
            second_store.replace_transcript(world.item.id, changed, {1: 1, 2: 2}, expected_updated_at=item.updated_at)

    updater = threading.Thread(daemon=True, target=update_while_holding)
    updater.start()
    assert held.wait(DEADLOCK_SECONDS)
    assert _probe_from_another_thread(world.library._lock) is False
    errors = []
    accept = threading.Thread(daemon=True, target=lambda: _capture(errors, world.decide))
    accept.start()
    proceed.set()
    updater.join(DEADLOCK_SECONDS)
    accept.join(DEADLOCK_SECONDS)

    assert not accept.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], CandidateConflictError)
    assert world.study.list_items(USER, kind="chunk", active=None) == []


def _capture(errors, call):
    try:
        call()
    except Exception as exc:  # noqa: BLE001 - surfaced to the test thread
        errors.append(exc)


def _spawn():
    return multiprocessing.get_context("spawn")


def test_another_process_holding_the_guard_blocks_and_its_delete_wins(world):
    ctx = _spawn()
    acquired, proceed, done = ctx.Event(), ctx.Event(), ctx.Event()
    child = ctx.Process(target=hold_guard_then_delete, daemon=True,
                        args=(str(world.root / "library.json"), world.item.id, acquired, proceed, done))
    child.start()
    try:
        assert acquired.wait(DEADLOCK_SECONDS)
        assert _probe_from_another_thread(world.library._lock) is False  # cross-process exclusion
        errors = []
        accept = threading.Thread(daemon=True, target=lambda: _capture(errors, world.decide))
        accept.start()
        proceed.set()
        assert done.wait(DEADLOCK_SECONDS)
        accept.join(DEADLOCK_SECONDS)
    finally:
        proceed.set()
        child.join(DEADLOCK_SECONDS)

    assert child.exitcode == 0 and not accept.is_alive()
    assert len(errors) == 1 and "no longer available" in str(errors[0])
    assert world.study.list_items(USER, kind="chunk", active=None) == []


def test_an_accept_in_progress_blocks_another_process(world, monkeypatch):
    reached, release = _pause_study_bank_save(world, monkeypatch)
    outcome = {}
    accept = threading.Thread(daemon=True, target=lambda: outcome.setdefault("accept", world.decide()))
    accept.start()
    assert reached.wait(DEADLOCK_SECONDS)
    ctx = _spawn()
    probed, proceed, done, results = ctx.Event(), ctx.Event(), ctx.Event(), ctx.Queue()
    child = ctx.Process(target=probe_then_delete, daemon=True,
                        args=(str(world.root / "library.json"), world.item.id, probed, results, proceed, done))
    child.start()
    try:
        assert probed.wait(DEADLOCK_SECONDS)
        assert results.get(timeout=DEADLOCK_SECONDS) == ("probe_acquired", False)
        proceed.set()  # the child now blocks on the guard
        release.set()
        accept.join(DEADLOCK_SECONDS)
        assert done.wait(DEADLOCK_SECONDS)
        assert results.get(timeout=DEADLOCK_SECONDS) == ("deleted", True)
    finally:
        release.set()
        proceed.set()
        child.join(DEADLOCK_SECONDS)

    [provenance] = outcome["accept"].study_item.provenance
    assert provenance.sentence_text == TEXT
    with pytest.raises(KeyError):
        world.library.get_item(world.item.id)


# --- lock order / deadlock regression ------------------------------------------------

def test_lock_order_has_no_reverse_edge():
    """Library code never calls the Study Bank, Chunk Memory or candidate
    stores, and the Study Bank never calls the Library, so the decision-path
    order (decision -> candidates -> Library -> Study Bank -> Chunk Memory)
    has no reverse edge."""
    def imports(rel):
        tree = ast.parse((BACKEND / rel).read_text(encoding="utf-8"))
        return {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}

    library = imports("services/library/store.py") | imports("services/library/source_guard.py")
    assert not {m for m in library if m.startswith(("services.study_bank", "services.retrieval", "services.ai_chunks"))}
    study = imports("services/study_bank/store.py")
    assert not {m for m in study if m.startswith(("services.library", "services.ai_chunks"))}


def test_concurrent_accept_library_and_study_bank_work_all_finish(world, monkeypatch):
    reached, release = _pause_study_bank_save(world, monkeypatch)
    other = world.library.save_item(_lesson(url="https://example.invalid/other", lesson_id="lesson-2"))
    progress = world.library.get_item(world.item.id).progress.model_copy(update={"sentence_index": 1})
    start = threading.Barrier(4)
    errors = []
    work = [
        lambda: world.decide(),
        lambda: _library(world.root).delete_item(other.id),
        lambda: world.library.update_progress(world.item.id, progress.model_dump(mode="json")),
        lambda: StudyBankStore(world.root / "study.json", clock=lambda: NOW).list_items(USER, kind="chunk", active=None),
    ]
    threads = [threading.Thread(daemon=True, target=lambda call=call: (start.wait(), _capture(errors, call)))
               for call in work]
    for thread in threads:
        thread.start()
    assert reached.wait(DEADLOCK_SECONDS)
    release.set()
    for thread in threads:
        thread.join(DEADLOCK_SECONDS)

    assert not any(thread.is_alive() for thread in threads), "deadlock"
    assert errors == []
    assert world.candidate_state() == "accepted"
    assert world.library.get_item(world.item.id).progress.sentence_index == 1
    with pytest.raises(KeyError):
        world.library.get_item(other.id)
