from collections.abc import Callable

from services.library.store import LearningLibraryStore
from services.schemas.shadowing import ShadowingMetrics
from services.shadowing.metrics import calculate_metrics
from services.shadowing.reference import ReferenceTimingService
from services.transcription.local_whisper import LocalTranscription, transcribe_local_words


class ShadowingAnalyzer:
    def __init__(
        self,
        store: LearningLibraryStore,
        *,
        transcriber: Callable[[str, str], LocalTranscription] = transcribe_local_words,
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
        attempt = self.transcriber(attempt_wav, item.lesson.source_language)
        reference, reason = self.references.get_words(item_id, sentence_id)
        return calculate_metrics(
            sentence.text,
            attempt.text,
            attempt.words,
            reference,
            timing_unavailable_reason=reason,
        )
