import html
import json
import os
import re
import threading
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from services.retrieval.chunk_quality import prepare_chunk_text
from services.review.scheduler import schedule_review
from services.schemas.study_bank import (
    MediaStudyProvenance,
    ReviewRating,
    ReviewState,
    StudyItem,
    StudyItemInput,
    StudyItemPatch,
)


DEFAULT_STUDY_BANK_PATH = (
    Path(__file__).resolve().parents[2] / "storage" / "study_bank.json"
)
CURATION_NOISE_LABEL = re.compile(
    r"^[\[(]\s*(?:music|musik|applause|laughter|singing|singen|cheering|inaudible)\s*[\])]$",
    re.IGNORECASE,
)


class StudyBankStoreError(RuntimeError):
    """Raised when the local Study Bank cannot be read or written safely."""


class StudyBankConflictError(RuntimeError):
    """Raised when a review transition is stale or targets an inactive item."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_word_text(value: str) -> str:
    cleaned = unicodedata.normalize("NFKC", html.unescape(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if (
        not cleaned
        or CURATION_NOISE_LABEL.fullmatch(cleaned)
        or not any(character.isalnum() for character in cleaned)
    ):
        raise ValueError("word text must contain lexical content")
    return cleaned


def normalized_identity(value: StudyItemInput) -> tuple[str, str]:
    if value.kind == "chunk":
        cleaned = re.sub(
            r"\s+", " ", unicodedata.normalize("NFKC", html.unescape(value.text))
        ).strip()
        if CURATION_NOISE_LABEL.fullmatch(cleaned):
            raise ValueError("chunk text must contain reusable spoken language")
        prepared = prepare_chunk_text(value.text, value.source_language)
        return prepared.text, prepared.normalized_text
    text = _clean_word_text(value.text)
    identity = _clean_word_text(value.lemma) if value.lemma else text
    normalized = identity.rstrip(" .!?。！？").casefold()
    if not normalized:
        raise ValueError("word text must contain lexical content")
    return text, normalized


class StudyBankStore:
    def __init__(
        self,
        path: str | Path = DEFAULT_STUDY_BANK_PATH,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ):
        self.path = Path(path)
        self.clock = clock
        self.id_factory = id_factory
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_items([])

    def list_items(
        self,
        user_id: str,
        *,
        kind: str | None = None,
        language: str | None = None,
        active: bool | None = True,
        library_item_id: str | None = None,
        search: str | None = None,
    ) -> list[StudyItem]:
        query = (search or "").strip().casefold()
        with self._lock:
            items = [
                item for item in self._read_items()
                if item.user_id == user_id
                and (kind is None or item.kind == kind)
                and (language is None or item.source_language == language)
                and (active is None or item.active is active)
                and (library_item_id is None or any(
                    isinstance(p, MediaStudyProvenance)
                    and p.library_item_id == library_item_id
                    for p in item.provenance
                ))
                and (
                    not query
                    or query in item.text.casefold()
                    or query in item.meaning.casefold()
                    or any(
                        query in p.media_title.casefold()
                        for p in item.provenance
                        if isinstance(p, MediaStudyProvenance)
                    )
                )
            ]
        return sorted(items, key=lambda item: (item.updated_at, item.id), reverse=True)

    def get_item(self, user_id: str, item_id: str) -> StudyItem:
        with self._lock:
            for item in self._read_items():
                if item.id == item_id and item.user_id == user_id:
                    return item
        raise KeyError(item_id)

    def assert_readable(self) -> None:
        with self._lock:
            self._read_items()

    def save_items(self, values: Iterable[StudyItemInput]) -> tuple[list[StudyItem], int]:
        with self._lock:
            items, storage_version = self._read_items_and_version()
            results: list[StudyItem] = []
            created = 0
            changed = False
            for value in values:
                saved, was_created, was_changed = self._upsert(items, value)
                results.append(saved)
                created += int(was_created)
                changed = changed or was_changed
            if changed or storage_version in {1, 2}:
                self._write_items(items)
            return results, created

    def update_item(self, user_id: str, item_id: str, patch: StudyItemPatch) -> StudyItem:
        with self._lock:
            items = self._read_items()
            for index, item in enumerate(items):
                if item.id != item_id or item.user_id != user_id:
                    continue
                updates = {
                    key: value
                    for key, value in patch.model_dump().items()
                    if value is not None
                }
                updated = item.model_copy(update={**updates, "updated_at": self._now()})
                items[index] = updated
                self._write_items(items)
                return updated
        raise KeyError(item_id)

    def delete_item(self, user_id: str, item_id: str) -> None:
        with self._lock:
            items = self._read_items()
            remaining = [
                item for item in items
                if item.id != item_id or item.user_id != user_id
            ]
            if len(remaining) == len(items):
                raise KeyError(item_id)
            self._write_items(remaining)

    def rate_item(
        self,
        user_id: str,
        item_id: str,
        rating: ReviewRating,
        expected_review_count: int,
    ) -> tuple[StudyItem, datetime]:
        """Apply one review transition while holding the persistence lock."""
        with self._lock:
            items = self._read_items()
            for index, item in enumerate(items):
                if item.id != item_id or item.user_id != user_id:
                    continue
                if not item.active:
                    raise StudyBankConflictError("Archived Study Bank items cannot be rated")
                if item.review.review_count != expected_review_count:
                    raise StudyBankConflictError("Study Bank review state is stale")
                now = self._now()
                previous_due_at = item.review.due_at
                review = schedule_review(item.review, rating, now)
                updated = item.model_copy(update={"review": review, "updated_at": now})
                items[index] = updated
                self._write_items(items)
                return updated, previous_due_at
        raise KeyError(item_id)

    def active_chunk_keys(self, user_id: str, language: str) -> set[str]:
        return {
            item.normalized_text
            for item in self.list_items(
                user_id, kind="chunk", language=language, active=True
            )
        }

    def _upsert(
        self, items: list[StudyItem], value: StudyItemInput
    ) -> tuple[StudyItem, bool, bool]:
        cleaned_text, normalized = normalized_identity(value)
        key = (value.user_id, value.source_language, value.kind, normalized)
        existing_index = next(
            (
                index for index, item in enumerate(items)
                if (item.user_id, item.source_language, item.kind, item.normalized_text) == key
            ),
            None,
        )
        now = self._now()
        if existing_index is None:
            item = StudyItem(
                id=self.id_factory(),
                created_at=now,
                updated_at=now,
                normalized_text=normalized,
                text=cleaned_text,
                review=ReviewState.new(now),
                **value.model_dump(exclude={"text"}),
            )
            items.append(item)
            return item, True, True

        existing = items[existing_index]
        provenance = list(existing.provenance)
        fingerprints = {record.model_dump_json() for record in provenance}
        for record in value.provenance:
            if record.model_dump_json() not in fingerprints:
                provenance.append(record)
                fingerprints.add(record.model_dump_json())
        if len(provenance) > 1_000:
            raise ValueError("Study Bank provenance limit exceeded")
        updates = {
            "provenance": provenance,
            "meaning": existing.meaning or value.meaning,
            "lemma": existing.lemma or value.lemma,
            "part_of_speech": existing.part_of_speech or value.part_of_speech,
            "example": existing.example or value.example,
            "usage": existing.usage or value.usage,
            "note": existing.note or value.note,
        }
        changed = any(getattr(existing, key) != result for key, result in updates.items())
        if not changed:
            return existing, False, False
        updated = existing.model_copy(update={**updates, "updated_at": now})
        items[existing_index] = updated
        return updated, False, True

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise StudyBankStoreError("Study Bank clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _read_items(self) -> list[StudyItem]:
        items, _ = self._read_items_and_version()
        return items

    def _read_items_and_version(self) -> tuple[list[StudyItem], int]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StudyBankStoreError(f"Malformed Study Bank JSON: {self.path}") from exc
        except OSError as exc:
            raise StudyBankStoreError(f"Could not read Study Bank: {self.path}") from exc
        if not isinstance(payload, dict) or payload.get("version") not in (1, 2, 3):
            raise StudyBankStoreError(f"Unsupported Study Bank version: {self.path}")
        version = payload["version"]
        records = payload.get("items")
        if not isinstance(records, list):
            raise StudyBankStoreError(f"Malformed Study Bank items: {self.path}")
        try:
            normalized_records = []
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("Study Bank item must be an object")
                normalized = dict(record)
                if version == 1:
                    normalized["review"] = ReviewState.new(
                        datetime.fromisoformat(str(normalized.get("created_at")).replace("Z", "+00:00"))
                    ).model_dump(mode="json")
                if version in {1, 2}:
                    provenance = normalized.get("provenance")
                    if not isinstance(provenance, list):
                        raise ValueError("Study Bank provenance must be a list")
                    normalized["provenance"] = [
                        {"source_type": "media", **value}
                        if isinstance(value, dict) else value
                        for value in provenance
                    ]
                normalized_records.append(normalized)
            return [StudyItem.model_validate(record) for record in normalized_records], version
        except (TypeError, ValueError, ValidationError) as exc:
            raise StudyBankStoreError(f"Malformed Study Bank item: {self.path}") from exc

    def _write_items(self, items: list[StudyItem]) -> None:
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        payload = {"version": 3, "items": [item.model_dump(mode="json") for item in items]}
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise StudyBankStoreError(f"Could not write Study Bank: {self.path}") from exc
