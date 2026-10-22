from datetime import datetime, timezone

from services.library.store import LearningLibraryStore
from services.schemas.speaking import (
    SpeakingContext, SpeakingContextRequest, SpeakingMediaSentence,
    SpeakingTarget, SpeakingWord,
)
from services.schemas.study_bank import MediaStudyProvenance
from services.speaking.prompt import build_system_prompt
from services.study_bank.store import StudyBankStore


class SpeakingContextBuilder:
    def __init__(self, study_bank: StudyBankStore, library: LearningLibraryStore, clock=lambda: datetime.now(timezone.utc)):
        self.study_bank, self.library, self.clock = study_bank, library, clock

    def build(self, request: SpeakingContextRequest) -> SpeakingContext:
        now = self.clock()
        chunks = self.study_bank.list_items(request.user_id, kind="chunk", language=request.language, active=True)
        words = self.study_bank.list_items(request.user_id, kind="word", language=request.language, active=True)
        media_item = None
        if request.mode == "media":
            if not request.library_item_id:
                raise ValueError("Discuss saved media requires a Library item")
            media_item = self.library.get_item(request.library_item_id)
            if media_item.lesson.source_language != request.language:
                raise ValueError("Library item language does not match speaking language")
        selected = self._select_chunks(chunks, request, now, media_item.id if media_item else None)
        support = sorted(words, key=lambda item: (item.review.due_at, item.created_at, item.id))[:5]
        sentences = self._media_window(media_item) if media_item else []
        goal = request.goal or {
            "review_weak": "Use difficult saved expressions in natural conversation.",
            "media": f"Discuss {media_item.lesson.title if media_item else 'the saved media'} naturally.",
            "custom": "Have a natural conversation at the selected level.",
        }[request.mode]
        context = SpeakingContext(
            language=request.language, level=request.level, mode=request.mode,
            session_goal=goal,
            target_chunks=[SpeakingTarget(study_item_id=item.id, text=item.text, meaning=item.meaning) for item in selected],
            support_words=[SpeakingWord(study_item_id=item.id, text=item.text, meaning=item.meaning) for item in support],
            library_item_id=media_item.id if media_item else None,
            media_title=media_item.lesson.title if media_item else None,
            media_sentences=sentences,
            system_prompt="placeholder",
        )
        return context.model_copy(update={"system_prompt": build_system_prompt(context)})

    def _select_chunks(self, chunks, request, now, media_id):
        def priority(item):
            review = item.review
            if review.last_rating == "again": category = 0
            elif review.last_rating == "hard": category = 1
            elif review.review_count > 0 and review.due_at <= now: category = 2
            elif review.review_count == 0: category = 3
            else: category = 4
            return (category, -review.lapse_count, review.due_at, review.review_count, item.created_at, item.id)
        ordered = sorted(chunks, key=priority)
        if media_id:
            media = [item for item in ordered if any(
                isinstance(p, MediaStudyProvenance) and p.library_item_id == media_id
                for p in item.provenance
            )]
            ordered = media + [item for item in ordered if item not in media]
        if request.target_ids is not None:
            ids = list(dict.fromkeys(request.target_ids))
            by_id = {item.id: item for item in ordered}
            if any(item_id not in by_id for item_id in ids):
                raise ValueError("Selected target is unavailable")
            ordered = [by_id[item_id] for item_id in ids]
        return ordered[:request.target_limit]

    @staticmethod
    def _media_window(item):
        sentences = item.lesson.sentences
        if not sentences:
            return []
        current_id = item.listening_progress.current_sentence_id
        center = next((i for i, sentence in enumerate(sentences) if sentence.id == current_id), 0)
        start = max(0, center - 5)
        end = min(len(sentences), start + 12)
        start = max(0, end - 12)
        return [SpeakingMediaSentence(
            sentence_id=value.id, text=value.text, translation=value.translation,
            start=value.start, end=value.end,
        ) for value in sentences[start:end]]
