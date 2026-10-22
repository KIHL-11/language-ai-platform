from datetime import datetime, timezone

from services.library.store import LearningLibraryStore
from services.retrieval.chunk_memory import ChunkMemoryService
from services.schemas.integration import ShadowingSource, StudyPracticeEvidence
from services.schemas.study_bank import MediaStudyProvenance, StudyItem
from services.speaking.store import SpeakingSessionStore


MAX_RECENT_SPEAKING_SESSIONS = 100


class EvidenceBuilder:
    def __init__(
        self,
        library: LearningLibraryStore,
        speaking: SpeakingSessionStore,
        chunks: ChunkMemoryService,
        *,
        clock=lambda: datetime.now(timezone.utc),
    ):
        self.library, self.speaking, self.chunks, self.clock = library, speaking, chunks, clock

    def build(
        self,
        user_id: str,
        item: StudyItem,
        *,
        sessions=None,
        library_items=None,
        chunks=None,
    ) -> StudyPracticeEvidence:
        now = self.clock().astimezone(timezone.utc)
        sessions = sessions if sessions is not None else self.speaking.list_recent(
            user_id, MAX_RECENT_SPEAKING_SESSIONS
        )
        library_items = library_items if library_items is not None else self.library.list_items()
        library = {value.id: value for value in library_items}
        chunks = chunks if chunks is not None else self.chunks.list_chunks(
            user_id, include_archived=True,
        )
        chunk = (
            next(
                (
                    value
                    for value in chunks
                    if value.source_language == item.source_language
                    and value.normalized_text == item.normalized_text
                ),
                None,
            )
            if item.kind == "chunk"
            else None
        )

        targeted = []
        used = []
        for session in sessions:
            usage = next((value for value in session.target_usage if value.study_item_id == item.id), None)
            if usage is None:
                continue
            targeted.append((session, usage))
            if usage.use_count > 0:
                used.append((session, usage))
        last_used = max(used, key=lambda value: value[0].ended_at or value[0].started_at) if used else None

        sources = []
        seen = set()
        for provenance in item.provenance:
            if not isinstance(provenance, MediaStudyProvenance):
                continue
            key = (provenance.library_item_id, provenance.sentence_id)
            library_item = library.get(provenance.library_item_id)
            sentence = next(
                (
                    value for value in library_item.lesson.sentences
                    if value.id == provenance.sentence_id
                ),
                None,
            ) if library_item else None
            timing_matches = bool(
                sentence
                and min(sentence.end, provenance.end) > max(sentence.start, provenance.start)
            )
            if key in seen or library_item is None or not timing_matches:
                continue
            seen.add(key)
            progress = library_item.shadowing_progress.sentences.get(str(provenance.sentence_id))
            sources.append((provenance, library_item, progress))

        attempted = [value for value in sources if value[2] and value[2].attempt_count > 0]
        stable = [value for value in sources if value[2] and value[2].consecutive_target_hits >= 3]
        non_stable = [value for value in sources if value not in stable]
        preferred = None
        if non_stable:
            def source_priority(value):
                provenance, library_item, progress = value
                category = 0 if progress is None or progress.attempt_count == 0 else 1
                timestamp = progress.last_attempt_at if progress else None
                return (category, -(timestamp.timestamp() if timestamp else 0), provenance.library_item_id, provenance.sentence_id)
            provenance, library_item, _ = min(non_stable, key=source_priority)
            preferred = ShadowingSource(
                library_item_id=provenance.library_item_id,
                sentence_id=provenance.sentence_id,
                media_title=library_item.lesson.title,
            )
        latest_shadow = max(
            (value[2].last_attempt_at for value in attempted if value[2] and value[2].last_attempt_at),
            default=None,
        )

        review_status = "new" if item.review.review_count == 0 else "due" if item.review.due_at <= now else "scheduled"
        return StudyPracticeEvidence(
            study_item_id=item.id,
            kind=item.kind,
            text=item.text,
            source_language=item.source_language,
            review_status=review_status,
            due_at=item.review.due_at,
            review_count=item.review.review_count,
            lapse_count=item.review.lapse_count,
            last_rating=item.review.last_rating,
            last_reviewed_at=item.review.last_reviewed_at,
            recent_targeted_session_count=len(targeted),
            recent_used_session_count=len(used),
            recent_use_count=sum(value.use_count for _, value in used),
            last_used_at=(last_used[0].ended_at or last_used[0].started_at) if last_used else None,
            speaking_evidence=last_used[1].evidence[0] if last_used and last_used[1].evidence else None,
            source_sentence_count=len(sources),
            attempted_source_sentence_count=len(attempted),
            stable_source_sentence_count=len(stable),
            shadowing_last_practiced_at=latest_shadow,
            preferred_shadowing_source=preferred,
            guided_practice_count=chunk.practice_count if chunk else 0,
            guided_success_count=chunk.success_count if chunk else 0,
            guided_failure_count=chunk.failure_count if chunk else 0,
            guided_last_practiced_at=chunk.last_practiced_at if chunk else None,
        )
