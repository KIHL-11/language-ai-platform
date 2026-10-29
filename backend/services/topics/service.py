import re
import unicodedata
from datetime import datetime, timezone

from services.schemas.speaking import (
    SpeakingContext, SpeakingCorrection, SpeakingEventRequest, SpeakingTarget,
)
from services.schemas.topics import (
    SpeakingTopic, TopicAttemptState, TopicCheckpointResult, TopicFeedback,
    TopicPreviewResponse,
)
from services.speaking.store import SpeakingSessionStore
from services.study_bank.owner_repository import OwnerScopedStudyBankRepository
from services.study_bank.store import StudyBankStore
from services.topics.catalog import get_topic


LIFECYCLE = ["setup", "preview", "connecting", "live", "ended", "feedback", "saved"]
MOCK_MODEL = "deterministic-topic-mock-v1"
TOPIC_MARKER = "topic-curriculum:"


class TopicStorageUnavailable(RuntimeError):
    pass


class TopicTargetUnavailable(KeyError):
    pass


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+(?:['’][^\W_]+)?", value, flags=re.UNICODE))


def _contains(text: str, term: str) -> bool:
    haystack, needle = _normalized(text).split(), _normalized(term).split()
    return bool(needle) and any(haystack[index:index + len(needle)] == needle for index in range(len(haystack) - len(needle) + 1))


def _priority(item, now: datetime):
    review = item.review
    if review.last_rating == "again":
        category = 0
    elif review.last_rating == "hard":
        category = 1
    elif review.review_count > 0 and review.due_at <= now:
        category = 2
    elif review.review_count == 0:
        category = 3
    else:
        category = 4
    return (category, -review.lapse_count, review.due_at, item.created_at, item.id)


def select_topic_chunks(items, topic: SpeakingTopic, limit: int, now: datetime):
    keywords = [_normalized(value) for value in topic.keywords]

    def relevance(item):
        searchable = _normalized(" ".join((item.text, item.meaning, item.note or "")))
        return sum(1 for keyword in keywords if keyword and keyword in searchable)

    return sorted(items, key=lambda item: (-relevance(item), *_priority(item, now)))[:limit]


class TopicCurriculumService:
    def __init__(
        self,
        study_bank: StudyBankStore | OwnerScopedStudyBankRepository,
        sessions: SpeakingSessionStore | None = None,
        *,
        cloud: bool = False,
        clock=lambda: datetime.now(timezone.utc),
    ):
        self.study_bank = study_bank
        self.sessions = sessions
        self.cloud = cloud
        self.clock = clock

    def _items(self, owner: str, topic: SpeakingTopic):
        try:
            if self.cloud:
                self.study_bank.assert_readable()
            return self.study_bank.list_items(
                owner, kind="chunk", language=topic.language, active=True
            )
        except Exception as exc:
            raise TopicStorageUnavailable("Topic targets are unavailable") from exc

    def preview(self, owner: str, topic_id: str, limit: int) -> TopicPreviewResponse:
        topic = get_topic(topic_id)
        selected = select_topic_chunks(self._items(owner, topic), topic, limit, self.clock())
        return TopicPreviewResponse(
            topic=topic,
            target_chunks=[
                SpeakingTarget(study_item_id=item.id, text=item.text, meaning=item.meaning)
                for item in selected
            ],
            lifecycle=LIFECYCLE,
            evidence_notice=(
                "Only exact normalized matches in finalized learner turns count as observed use. "
                "This is not a fluency, pronunciation, or CEFR assessment."
            ),
        )

    def start(self, owner: str, topic_id: str, target_ids: list[str]):
        if self.cloud or self.sessions is None:
            raise TopicStorageUnavailable("Cloud topic attempts require owner-scoped Speaking storage")
        topic = get_topic(topic_id)
        active = self._items(owner, topic)
        by_id = {item.id: item for item in active}
        ids = list(dict.fromkeys(target_ids))
        if any(item_id not in by_id for item_id in ids):
            raise TopicTargetUnavailable("Topic target not found")
        targets = [
            SpeakingTarget(study_item_id=item.id, text=item.text, meaning=item.meaning)
            for item in (by_id[item_id] for item_id in ids)
        ]
        marker = f"{TOPIC_MARKER}{topic.topic_id}@{topic.version}"
        context = SpeakingContext(
            language=topic.language,
            level=topic.level,
            mode="custom",
            session_goal=topic.goal,
            target_chunks=targets,
            support_words=[],
            library_item_id=None,
            media_title=None,
            media_sentences=[],
            system_prompt=(
                f"{marker}. Deterministic local mock practice. Never infer fluency or CEFR. "
                f"Scenario: {topic.scenario}"
            ),
        )
        session = self.sessions.create(
            user_id=owner,
            provider="topic-mock",
            model=MOCK_MODEL,
            duration=180,
            context=context,
            goal=f"{marker} {topic.goal}",
        )
        return self._state(session, topic, "live", topic.guided_prompts[0])

    def turn(self, owner: str, topic_id: str, session_id: str, expected: int, text: str):
        topic = get_topic(topic_id)
        session = self._session(owner, session_id, topic)
        learner = self.sessions.append_event(
            owner,
            session.id,
            SpeakingEventRequest(
                expected_event_count=expected,
                type="turn",
                role="learner",
                text=text,
                provider_turn_id=f"topic:{session.id}:{expected}:learner",
            ),
        )
        prompt_index = min(learner.learner_turn_count, len(topic.guided_prompts) - 1)
        reply = topic.guided_prompts[prompt_index]
        updated = self.sessions.append_event(
            owner,
            session.id,
            SpeakingEventRequest(
                expected_event_count=learner.event_count,
                type="turn",
                role="agent",
                text=reply,
                provider_turn_id=f"topic:{session.id}:{expected}:agent",
            ),
        )
        return self._state(updated, topic, "live", reply), reply

    def finish(self, owner: str, topic_id: str, session_id: str, expected: int, interrupted: bool):
        topic = get_topic(topic_id)
        session = self._session(owner, session_id, topic)
        finished = self.sessions.finish(
            owner,
            session.id,
            expected,
            "interrupted" if interrupted else "completed",
            "user",
        )
        feedback = self._feedback(topic, finished, interrupted)
        return self._state(finished, topic, "saved"), feedback

    def _session(self, owner, session_id, topic):
        if self.cloud or self.sessions is None:
            raise TopicStorageUnavailable("Cloud topic attempts require owner-scoped Speaking storage")
        session = self.sessions.get(owner, session_id)
        expected = f"{TOPIC_MARKER}{topic.topic_id}@{topic.version}"
        if session.model != MOCK_MODEL or not (session.goal or "").startswith(expected):
            raise KeyError(session_id)
        return session

    @staticmethod
    def _state(session, topic, phase, prompt=None):
        return TopicAttemptState(
            session_id=session.id,
            topic_id=topic.topic_id,
            topic_version=topic.version,
            phase=phase,
            event_count=session.event_count,
            learner_turn_count=session.learner_turn_count,
            target_usage=session.target_usage,
            prompt=prompt,
        )

    @staticmethod
    def _feedback(topic, session, interrupted):
        turns = [turn for turn in session.turns if turn.role == "learner"]
        checkpoints = []
        for checkpoint in topic.success_criteria:
            evidence = next(
                (turn.text for turn in turns if any(_contains(turn.text, term) for term in checkpoint.evidence_terms)),
                None,
            )
            checkpoints.append(TopicCheckpointResult(
                checkpoint_id=checkpoint.id,
                label=checkpoint.label,
                observed=evidence is not None,
                evidence=evidence,
            ))
        corrections = []
        patterns = (
            ((r"\bi am agree\b", "I agree", "Use ‘agree’ directly without ‘am’."),) if topic.language == "en"
            else ((r"\bmit das\b", "mit dem", "‘Mit’ takes the dative case."),)
        )
        for turn in turns:
            for pattern, replacement, explanation in patterns:
                if re.search(pattern, turn.text, flags=re.IGNORECASE):
                    corrections.append(SpeakingCorrection(
                        learner_text=turn.text,
                        corrected_text=re.sub(pattern, replacement, turn.text, flags=re.IGNORECASE),
                        short_explanation=explanation,
                        category="grammar",
                    ))
                    break
            if len(corrections) >= 8:
                break
        return TopicFeedback(
            topic_id=topic.topic_id,
            topic_version=topic.version,
            completion="interrupted" if interrupted else "completed",
            learner_turn_count=len(turns),
            checkpoints=checkpoints,
            target_usage=session.target_usage,
            corrections=corrections,
            limitations=[
                "Evidence is limited to finalized learner text entered in this mock practice.",
                "Expression matches do not measure pronunciation, fluency, or CEFR level.",
                "No Study Bank or Daily Review state was changed.",
            ],
        )
