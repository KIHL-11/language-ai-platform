from datetime import datetime, timezone

from services.integration.evidence import EvidenceBuilder
from services.library.store import LearningLibraryStore
from services.retrieval.chunk_memory import ChunkMemoryService
from services.review.service import ReviewService
from services.schemas.integration import (
    ContinueListeningItem, IntegrationOverview, NextAction,
    SpeakingSuggestionCandidate, StudyPracticeEvidence,
)
from services.schemas.study_bank import SpeakingStudyProvenance
from services.speaking.store import SpeakingSessionStore
from services.study_bank.store import StudyBankStore


class IntegrationService:
    def __init__(self, study: StudyBankStore, library: LearningLibraryStore, speaking: SpeakingSessionStore, chunks: ChunkMemoryService, *, clock=lambda: datetime.now(timezone.utc)):
        self.study, self.library, self.speaking, self.chunks, self.clock = study, library, speaking, chunks, clock

    def evidence(self, user_id: str, item_id: str) -> StudyPracticeEvidence:
        item = self.study.get_item(user_id, item_id)
        return EvidenceBuilder(self.library, self.speaking, self.chunks, clock=self.clock).build(user_id, item)

    def target_languages(self, user_id: str, source_language: str) -> list[str]:
        return sorted({item.target_language for item in self.study.list_items(
            user_id, language=source_language, active=None,
        )})

    def overview(self, user_id: str, language: str | None = None) -> IntegrationOverview:
        now = self.clock().astimezone(timezone.utc)
        items = self.study.list_items(user_id, language=language, active=True)
        sessions = [
            session
            for session in self.speaking.list_recent(user_id, 100)
            if language is None or session.language == language
        ]
        library_items = [value for value in self.library.list_items() if language is None or value.lesson.source_language == language]
        chunks = self.chunks.list_chunks(user_id, include_archived=True)
        evidence_builder = EvidenceBuilder(self.library, self.speaking, self.chunks, clock=self.clock)
        evidence = {
            item.id: evidence_builder.build(
                user_id,
                item,
                sessions=sessions,
                library_items=library_items,
                chunks=chunks,
            )
            for item in items
        }

        weak_items = [item for item in items if item.kind == "chunk" and item.review.review_count > 0 and (
            item.review.last_rating in {"again", "hard"}
            or item.review.due_at <= now
            or item.review.lapse_count > 0
        )]
        def weak_priority(item):
            review = item.review
            category = 0 if review.last_rating == "again" else 1 if review.last_rating == "hard" else 2 if review.due_at <= now and review.lapse_count else 3 if review.due_at <= now else 4
            return (category, -review.lapse_count, evidence[item.id].recent_used_session_count > 0, review.due_at, item.created_at, item.id)
        weak = [evidence[item.id] for item in sorted(weak_items, key=weak_priority)[:5]]

        listening = sorted(
            library_items,
            key=lambda value: (
                value.listening_progress.last_position_seconds <= 0,
                -value.updated_at.timestamp(),
                value.id,
            ),
        )[:3]
        continue_listening = [ContinueListeningItem(
            library_item_id=value.id,
            title=value.lesson.title,
            source_language=value.lesson.source_language,
            last_position_seconds=value.listening_progress.last_position_seconds,
            current_sentence_id=value.listening_progress.current_sentence_id,
            updated_at=value.updated_at,
        ) for value in listening]

        saved = {
            (provenance.speaking_session_id, provenance.suggestion_index)
            for item in self.study.list_items(user_id, active=None)
            for provenance in item.provenance
            if isinstance(provenance, SpeakingStudyProvenance)
        }
        target_languages = {
            source_language: self.target_languages(user_id, source_language)
            for source_language in {session.language for session in sessions[:10]}
        }
        suggestions = []
        for session in sessions[:10]:
            languages = target_languages[session.language]
            for index, suggestion in enumerate(session.suggestions):
                if (session.id, index) in saved:
                    continue
                suggestions.append(SpeakingSuggestionCandidate(
                    speaking_session_id=session.id,
                    suggestion_index=index,
                    source_language=session.language,
                    text=suggestion.text,
                    meaning=suggestion.meaning,
                    kind=suggestion.kind,
                    originating_turn=suggestion.originating_turn,
                    started_at=session.started_at,
                    target_languages=languages,
                ))
                if len(suggestions) == 5:
                    break
            if len(suggestions) == 5:
                break

        review = ReviewService(self.study, clock=self.clock).summary(user_id, language=language)
        actions: list[NextAction] = []
        if review.due_words + review.due_chunks:
            actions.append(NextAction(type="review", label="Review due Words & Chunks"))
        spoken_ids = set()
        for item in weak:
            if item.recent_used_session_count == 0 and len(actions) < 6:
                actions.append(NextAction(type="speak", label=f'Practice “{item.text}” in Speaking', source_language=item.source_language, study_item_id=item.study_item_id))
                spoken_ids.add(item.study_item_id)
                break
        for item in weak:
            source = item.preferred_shadowing_source
            if source and item.study_item_id not in spoken_ids and len(actions) < 6:
                actions.append(NextAction(type="shadow", label=f'Practice the source sentence for “{item.text}”', source_language=item.source_language, study_item_id=item.study_item_id, library_item_id=source.library_item_id, sentence_id=source.sentence_id))
                break
        if suggestions and len(actions) < 6:
            value = suggestions[0]
            actions.append(NextAction(type="save_speaking_suggestion", label=f'Save “{value.text}” from Speaking', source_language=value.source_language, speaking_session_id=value.speaking_session_id, suggestion_index=value.suggestion_index))
        if continue_listening and len(actions) < 6:
            value = continue_listening[0]
            actions.append(NextAction(type="listen", label=f'Continue listening to {value.title}', source_language=value.source_language, library_item_id=value.library_item_id))
        if review.due_words + review.due_chunks == 0 and review.new_words + review.new_chunks and len(actions) < 6:
            actions.append(NextAction(type="review", label="Review new Words & Chunks"))
        return IntegrationOverview(
            review=review,
            weak_chunks=weak,
            continue_listening=continue_listening,
            speaking_suggestions=suggestions,
            next_actions=actions[:6],
            has_study_items=bool(items),
            has_library_items=bool(library_items),
            has_speaking_sessions=bool(sessions),
        )
