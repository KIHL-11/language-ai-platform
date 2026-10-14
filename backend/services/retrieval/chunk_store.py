import json
import os
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from pydantic import ValidationError

from services.schemas.retrieval import StoredChunk


DEFAULT_CHUNK_BANK_PATH = (
    Path(__file__).resolve().parents[2] / "storage" / "chunk_bank.json"
)


class ChunkStoreError(RuntimeError):
    """Raised when the persistent chunk bank cannot be read safely."""


class ChunkStore:
    def __init__(self, path: str | Path = DEFAULT_CHUNK_BANK_PATH):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_chunks([])

    def read_chunks(self) -> list[StoredChunk]:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ChunkStoreError(
                    f"Malformed chunk bank JSON: {self.path}"
                ) from exc
            except OSError as exc:
                raise ChunkStoreError(
                    f"Could not read chunk bank: {self.path}"
                ) from exc

            records = payload.get("chunks") if isinstance(payload, dict) else None
            version = payload.get("version") if isinstance(payload, dict) else None
            if version not in {1, 2}:
                raise ChunkStoreError(
                    f"Unsupported chunk bank version in {self.path}"
                )
            if not isinstance(records, list):
                raise ChunkStoreError(
                    f"Malformed chunk bank JSON: expected a chunks list in {self.path}"
                )

            try:
                return [StoredChunk.model_validate(record) for record in records]
            except ValidationError as exc:
                raise ChunkStoreError(
                    f"Malformed chunk record in chunk bank: {self.path}"
                ) from exc

    def add_chunks(self, chunks: Iterable[StoredChunk]) -> list[StoredChunk]:
        with self._lock:
            existing = self.read_chunks()
            seen = {self._dedupe_key(chunk) for chunk in existing}
            added = []
            for chunk in chunks:
                key = self._dedupe_key(chunk)
                if key in seen:
                    continue
                seen.add(key)
                added.append(chunk)

            if added:
                self._write_chunks([*existing, *added])
            return added

    def upsert_chunk(self, chunk: StoredChunk) -> tuple[StoredChunk, bool]:
        with self._lock:
            chunks = self.read_chunks()
            key = self._dedupe_key(chunk)
            for index, existing in enumerate(chunks):
                if self._dedupe_key(existing) != key:
                    continue
                updated = existing.model_copy(
                    update={
                        "meaning": chunk.meaning or existing.meaning,
                        "updated_at": chunk.updated_at,
                        "source_lesson_id": (
                            existing.source_lesson_id or chunk.source_lesson_id
                        ),
                        "source_media_id": (
                            existing.source_media_id or chunk.source_media_id
                        ),
                        "source_sentence_id": (
                            existing.source_sentence_id or chunk.source_sentence_id
                        ),
                        "source_stage": existing.source_stage or chunk.source_stage,
                    }
                )
                chunks[index] = updated
                self._write_chunks(chunks)
                return updated, False
            chunks.append(chunk)
            self._write_chunks(chunks)
            return chunk, True

    def update_chunk(
        self,
        user_id: str,
        chunk_id: str,
        update: Callable[[StoredChunk], StoredChunk],
    ) -> StoredChunk:
        with self._lock:
            chunks = self.read_chunks()
            for index, chunk in enumerate(chunks):
                if chunk.id != chunk_id or chunk.user_id != user_id:
                    continue
                updated = update(chunk)
                chunks[index] = updated
                self._write_chunks(chunks)
                return updated
        raise KeyError(chunk_id)

    def count(self) -> int:
        return len(self.read_chunks())

    @staticmethod
    def _dedupe_key(chunk: StoredChunk) -> tuple[str, str, str]:
        return (
            chunk.user_id,
            chunk.source_language,
            chunk.normalized_text,
        )

    def _write_chunks(self, chunks: list[StoredChunk]) -> None:
        payload = {
            "version": 2,
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
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
            raise ChunkStoreError(f"Could not write chunk bank: {self.path}") from exc

