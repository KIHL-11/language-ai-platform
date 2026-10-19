import hashlib
import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from services.library.store import LearningLibraryStore
from services.schemas.shadowing import TimedWord
from services.shadowing.audio import prepared_reference_segment
from services.transcription.local_whisper import LocalTranscription, transcribe_local_words


_reference_generation_lock = threading.Lock()
_word_list = TypeAdapter(list[TimedWord])


class ReferenceTimingService:
    def __init__(
        self,
        store: LearningLibraryStore,
        *,
        transcriber: Callable[[str, str], LocalTranscription] = transcribe_local_words,
    ):
        self.store = store
        self.transcriber = transcriber

    def get_words(self, item_id: str, sentence_id: int) -> tuple[list[TimedWord] | None, str | None]:
        item = self.store.get_item(item_id)
        sentence = next((value for value in item.lesson.sentences if value.id == sentence_id), None)
        if sentence is None:
            raise ValueError("Invalid shadowing sentence id")
        try:
            audio_path, _ = self.store.audio_path(item_id)
        except KeyError:
            return None, "reference_audio_unavailable"
        signature = self._signature(sentence.id, item.lesson.source_language, sentence.text, audio_path)
        cache_path = self.store._owned_media_directory(item_id) / "shadowing_reference_v1.json"
        with _reference_generation_lock:
            cached = self._read_cache(cache_path)
            entry = cached.get(str(sentence_id))
            if isinstance(entry, dict) and entry.get("signature") == signature:
                try:
                    return _word_list.validate_python(entry.get("words")), None
                except ValidationError:
                    pass
            with prepared_reference_segment(audio_path, sentence.start, sentence.end) as wav:
                transcription = self.transcriber(str(wav), item.lesson.source_language)
            cached[str(sentence_id)] = {
                "signature": signature,
                "words": [word.model_dump(mode="json") for word in transcription.words],
            }
            self._write_cache(cache_path, cached)
            return transcription.words, None

    @staticmethod
    def _signature(sentence_id: int, language: str, text: str, audio_path: Path) -> str:
        stat = audio_path.stat()
        value = f"{sentence_id}\0{language}\0{text}\0{audio_path.name}\0{stat.st_size}\0{stat.st_mtime_ns}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _read_cache(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value.get("sentences", {}) if isinstance(value, dict) and value.get("version") == 1 and isinstance(value.get("sentences"), dict) else {}

    @staticmethod
    def _write_cache(path: Path, sentences: dict) -> None:
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps({"version": 1, "sentences": sentences}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
