import json
import mimetypes
import os
import re
import shutil
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from pydantic import ValidationError

from services.schemas.lesson import Lesson
from services.schemas.library import (
    LibraryItem,
    LibraryItemSummary,
    LibraryProgress,
    ListeningProgress,
    PersistentAudio,
)


BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LIBRARY_PATH = BACKEND_DIR / "storage" / "learning_library.json"
DEFAULT_LIBRARY_MEDIA = BACKEND_DIR / "storage" / "library_media"
DEFAULT_MEDIA_DIR = BACKEND_DIR / "media"
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


class LibraryStoreError(RuntimeError):
    """Raised when local Library data cannot be read or written safely."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_progress(lesson: Lesson) -> LibraryProgress:
    return LibraryProgress(training_order=lesson.training_order.copy())


def validated_progress(value: Any, lesson: Lesson) -> LibraryProgress:
    try:
        progress = LibraryProgress.model_validate(value)
    except ValidationError as exc:
        raise ValueError("Invalid Library progress") from exc
    if progress.training_order != lesson.training_order:
        raise ValueError("Progress training order does not match Lesson")
    if progress.stage_index >= len(lesson.training_order):
        raise ValueError("Progress stage index is out of range")
    if lesson.sentences:
        if progress.sentence_index >= len(lesson.sentences):
            raise ValueError("Progress sentence index is out of range")
    elif progress.sentence_index != 0:
        raise ValueError("Progress sentence index is out of range")
    expected_prefix = lesson.training_order[: len(progress.completed_stages)]
    if progress.completed_stages != expected_prefix:
        raise ValueError("Completed stages are not a canonical prefix")
    if any(
        lesson.training_order.index(stage) > progress.stage_index
        for stage in progress.completed_stages
    ):
        raise ValueError("Completed stages exceed current progress")
    return progress


def validated_listening_progress(value: Any, lesson: Lesson) -> ListeningProgress:
    try:
        progress = ListeningProgress.model_validate(value)
    except ValidationError as exc:
        raise ValueError("Invalid Listening progress") from exc
    duration = max((sentence.end for sentence in lesson.sentences), default=0.0)
    if duration == 0 and progress.last_position_seconds != 0:
        raise ValueError("Listening position is out of range")
    if duration > 0 and progress.last_position_seconds > duration:
        raise ValueError("Listening position is out of range")
    sentence_ids = {sentence.id for sentence in lesson.sentences}
    if (
        progress.current_sentence_id is not None
        and progress.current_sentence_id not in sentence_ids
    ):
        raise ValueError("Listening sentence id is out of range")
    return progress


def _youtube_identity(source_url: str) -> str | None:
    try:
        parsed = urlsplit(source_url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    video_id: str | None = None
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif host in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith(("/shorts/", "/embed/")):
            video_id = parsed.path.split("/", 2)[2].split("/", 1)[0]
    if video_id and YOUTUBE_ID.fullmatch(video_id):
        return f"youtube:{video_id}"
    return None


def source_key_for_lesson(lesson: Lesson) -> str:
    identity = _youtube_identity(lesson.source_url)
    if identity is None:
        try:
            parsed = urlsplit(lesson.source_url.strip())
            identity = urlunsplit(
                (
                    parsed.scheme.lower(),
                    parsed.netloc.lower(),
                    parsed.path,
                    parsed.query,
                    "",
                )
            )
        except ValueError:
            identity = lesson.source_url.strip()
    return "|".join(
        [identity, lesson.source_language, lesson.target_language, lesson.level]
    )


class LearningLibraryStore:
    def __init__(
        self,
        path: str | Path = DEFAULT_LIBRARY_PATH,
        media_root: str | Path = DEFAULT_LIBRARY_MEDIA,
        source_media_root: str | Path = DEFAULT_MEDIA_DIR,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.path = Path(path)
        self.media_root = Path(media_root)
        self.source_media_root = Path(source_media_root)
        self.clock = clock
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.media_root.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_items([])

    def list_items(self) -> list[LibraryItem]:
        with self._lock:
            items = self._read_items()
            return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def list_summaries(self) -> list[LibraryItemSummary]:
        return [self._summary(item) for item in self.list_items()]

    def get_item(self, item_id: str) -> LibraryItem:
        with self._lock:
            for item in self._read_items():
                if item.id == item_id:
                    return item
        raise KeyError(item_id)

    def save_item(
        self,
        lesson: Lesson,
        progress_value: Any | None = None,
    ) -> LibraryItem:
        with self._lock:
            items = self._read_items()
            source_key = source_key_for_lesson(lesson)
            existing_index = next(
                (i for i, item in enumerate(items) if item.source_key == source_key),
                None,
            )
            existing = items[existing_index] if existing_index is not None else None
            now = self._now()
            progress = (
                validated_progress(progress_value, lesson)
                if progress_value is not None
                else default_progress(lesson)
            )
            item_id = existing.id if existing else uuid.uuid4().hex
            audio = self._persist_audio(lesson, item_id, existing)
            listening_progress = ListeningProgress()
            if existing:
                try:
                    listening_progress = validated_listening_progress(
                        existing.listening_progress, lesson
                    )
                except ValueError:
                    pass
            item = LibraryItem(
                id=item_id,
                source_key=source_key,
                created_at=existing.created_at if existing else now,
                updated_at=now,
                lesson=lesson,
                progress=progress,
                persistent_audio=audio,
                listening_progress=listening_progress,
            )
            if existing_index is None:
                items.append(item)
            else:
                items[existing_index] = item
            self._write_items(items)
            return item

    def update_progress(self, item_id: str, progress_value: Any) -> LibraryItem:
        with self._lock:
            items = self._read_items()
            for index, item in enumerate(items):
                if item.id != item_id:
                    continue
                progress = validated_progress(progress_value, item.lesson)
                updated = item.model_copy(
                    update={"progress": progress, "updated_at": self._now()}
                )
                items[index] = updated
                self._write_items(items)
                return updated
        raise KeyError(item_id)

    def update_listening_progress(
        self, item_id: str, progress_value: Any
    ) -> LibraryItem:
        with self._lock:
            items = self._read_items()
            for index, item in enumerate(items):
                if item.id != item_id:
                    continue
                progress = validated_listening_progress(progress_value, item.lesson)
                updated = item.model_copy(
                    update={
                        "listening_progress": progress,
                        "updated_at": self._now(),
                    }
                )
                items[index] = updated
                self._write_items(items)
                return updated
        raise KeyError(item_id)

    def delete_item(self, item_id: str) -> None:
        with self._lock:
            items = self._read_items()
            remaining = [item for item in items if item.id != item_id]
            if len(remaining) == len(items):
                raise KeyError(item_id)
            self._write_items(remaining)
            owned_directory = self._owned_media_directory(item_id)
            if owned_directory.exists():
                shutil.rmtree(owned_directory)

    def audio_path(self, item_id: str) -> tuple[Path, str]:
        item = self.get_item(item_id)
        metadata = item.persistent_audio
        if metadata.status != "available" or not metadata.filename:
            raise KeyError(item_id)
        candidate = (self._owned_media_directory(item_id) / metadata.filename).resolve()
        root = self._owned_media_directory(item_id).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise LibraryStoreError("Unsafe persistent Library audio path") from exc
        if not candidate.is_file():
            raise KeyError(item_id)
        return candidate, metadata.content_type or "application/octet-stream"

    def item_for_response(self, item: LibraryItem) -> LibraryItem:
        if item.persistent_audio.status != "available":
            return item
        lesson = item.lesson.model_copy(
            update={"audio_url": f"/api/library/items/{item.id}/audio"}
        )
        return item.model_copy(update={"lesson": lesson})

    def _read_items(self) -> list[LibraryItem]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LibraryStoreError(f"Malformed Learning Library JSON: {self.path}") from exc
        except OSError as exc:
            raise LibraryStoreError(f"Could not read Learning Library: {self.path}") from exc
        if not isinstance(payload, dict) or payload.get("version") not in {1, 2}:
            raise LibraryStoreError(f"Unsupported Learning Library version: {self.path}")
        records = payload.get("items")
        if not isinstance(records, list):
            raise LibraryStoreError(f"Malformed Learning Library items: {self.path}")
        items: list[LibraryItem] = []
        for record in records:
            if not isinstance(record, dict):
                raise LibraryStoreError(f"Malformed Learning Library item: {self.path}")
            try:
                lesson = Lesson.model_validate(record.get("lesson"))
                try:
                    progress = validated_progress(record.get("progress"), lesson)
                except ValueError:
                    progress = default_progress(lesson)
                try:
                    listening_progress = validated_listening_progress(
                        record.get("listening_progress", {}), lesson
                    )
                except ValueError:
                    listening_progress = ListeningProgress()
                items.append(
                    LibraryItem.model_validate(
                        {
                            **record,
                            "lesson": lesson,
                            "progress": progress,
                            "listening_progress": listening_progress,
                        }
                    )
                )
            except ValidationError as exc:
                raise LibraryStoreError(
                    f"Malformed Learning Library item: {self.path}"
                ) from exc
        return items

    def _write_items(self, items: list[LibraryItem]) -> None:
        payload = {
            "version": 2,
            "items": [item.model_dump(mode="json") for item in items],
        }
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise LibraryStoreError(f"Could not write Learning Library: {self.path}") from exc

    def _persist_audio(
        self,
        lesson: Lesson,
        item_id: str,
        existing: LibraryItem | None,
    ) -> PersistentAudio:
        source = self._safe_source_audio(lesson.audio_url)
        if source is None:
            if existing and existing.persistent_audio.status == "available":
                try:
                    self.audio_path(existing.id)
                except (KeyError, LibraryStoreError):
                    pass
                else:
                    return existing.persistent_audio
            return PersistentAudio()
        extension = source.suffix.lower() if len(source.suffix) <= 10 else ""
        filename = f"audio{extension}"
        directory = self._owned_media_directory(item_id)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / filename
        temporary = directory / f".{filename}.tmp"
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
            for other in directory.iterdir():
                if other.is_file() and other != destination:
                    other.unlink()
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise LibraryStoreError("Could not persist Library audio") from exc
        content_type = mimetypes.guess_type(destination.name)[0] or "application/octet-stream"
        return PersistentAudio(
            status="available",
            filename=filename,
            content_type=content_type,
        )

    def _safe_source_audio(self, audio_url: str | None) -> Path | None:
        if not audio_url or not audio_url.startswith("/media/"):
            return None
        if "?" in audio_url or "#" in audio_url or "\\" in audio_url:
            return None
        relative = audio_url.removeprefix("/media/")
        root = self.source_media_root.resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate

    def _owned_media_directory(self, item_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item_id):
            raise LibraryStoreError("Unsafe Library item id")
        return self.media_root / item_id

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise LibraryStoreError("Library clock must return a timezone-aware value")
        return value

    @staticmethod
    def _summary(item: LibraryItem) -> LibraryItemSummary:
        progress = item.progress
        complete = len(progress.completed_stages) == len(progress.training_order)
        started = (
            progress.is_training
            or progress.stage_index > 0
            or progress.sentence_index > 0
            or bool(progress.completed_stages)
        )
        status = "complete" if complete else "in_progress" if started else "not_started"
        duration = max((sentence.end for sentence in item.lesson.sentences), default=0.0)
        return LibraryItemSummary(
            id=item.id,
            lesson_id=item.lesson.id,
            title=item.lesson.title,
            source_url=item.lesson.source_url,
            source_language=item.lesson.source_language,
            target_language=item.lesson.target_language,
            level=item.lesson.level,
            source=item.lesson.source,
            sentence_count=len(item.lesson.sentences),
            duration_seconds=duration or None,
            created_at=item.created_at,
            updated_at=item.updated_at,
            progress_status=status,
            audio_available=item.persistent_audio.status == "available",
            last_position_seconds=item.listening_progress.last_position_seconds,
            has_listening_progress=item.listening_progress.last_position_seconds > 0,
        )
