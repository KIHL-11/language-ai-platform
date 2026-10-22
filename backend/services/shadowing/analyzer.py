from collections.abc import Callable

from services.library.store import LearningLibraryStore
from services.schemas.shadowing import ShadowingMetrics
from services.shadowing.metrics import calculate_metrics
from services.shadowing.reference import ReferenceTimingService
from services.transcription.local_whisper import LocalTranscription, transcribe_shadowing_words
from services.transcript.hygiene import clean_transcript_text


class ShadowingAnalyzer:
    def __init__(
        self,
        store: LearningLibraryStore,
        *,
        transcriber: Callable[[str, str], LocalTranscription] = transcribe_shadowing_words,
        references: ReferenceTimingService | None = None,
    ):
        self.store = store
        self.transcriber = transcriber
        self.references = references or ReferenceTimingService(store, transcriber=transcriber)

    def analyze(self, item_id: str, sentence_id: int, attempt_wav: str) -> ShadowingMetrics:
        item = self.store.get_item(item_id)
        sentence = next((value for value in item.lesson.sentences if value.id == sentence_id), None)
        if sentence is None:
            raise ValueError("Invalid shadowing sentence id")
        target_text = clean_transcript_text(sentence.text)
        if not target_text:
            raise ValueError("Sentence has no practiceable transcript text")
        attempt = self.transcriber(attempt_wav, item.lesson.source_language)
        reference, reason = self.references.get_words(item_id, sentence_id)
        metrics = calculate_metrics(
            target_text,
            attempt.text,
            attempt.words,
            reference,
            timing_unavailable_reason=reason,
        )
        return metrics.model_copy(update={
            "asr_model": attempt.model_config,
            "speech_detected": bool(attempt.text.strip()),
        })
