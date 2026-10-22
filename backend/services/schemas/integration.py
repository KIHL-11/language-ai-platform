from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.schemas.lesson import SourceLanguage
from services.schemas.review import ReviewSummary
from services.schemas.study_bank import ReviewRating, StudyItem, StudyKind


class IntegrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ShadowingSource(IntegrationModel):
    library_item_id: str
    sentence_id: int
    media_title: str


class StudyPracticeEvidence(IntegrationModel):
    study_item_id: str
    kind: StudyKind
    text: str
    source_language: SourceLanguage
    review_status: Literal["new", "due", "scheduled"]
    due_at: datetime
    review_count: int
    lapse_count: int
    last_rating: ReviewRating | None
    last_reviewed_at: datetime | None
    recent_targeted_session_count: int = 0
    recent_used_session_count: int = 0
    recent_use_count: int = 0
    last_used_at: datetime | None = None
    speaking_evidence: str | None = None
    source_sentence_count: int = 0
    attempted_source_sentence_count: int = 0
    stable_source_sentence_count: int = 0
    shadowing_last_practiced_at: datetime | None = None
    preferred_shadowing_source: ShadowingSource | None = None
    guided_practice_count: int = 0
    guided_success_count: int = 0
    guided_failure_count: int = 0
    guided_last_practiced_at: datetime | None = None


class ContinueListeningItem(IntegrationModel):
    library_item_id: str
    title: str
    source_language: SourceLanguage
    last_position_seconds: float
    current_sentence_id: int | None
    updated_at: datetime


class SpeakingSuggestionCandidate(IntegrationModel):
    speaking_session_id: str
    suggestion_index: int
    source_language: SourceLanguage
    text: str
    meaning: str
    kind: StudyKind
    originating_turn: int
    started_at: datetime
    target_languages: list[str]


class NextAction(IntegrationModel):
    type: Literal["review", "speak", "shadow", "listen", "save_speaking_suggestion"]
    label: str
    source_language: SourceLanguage | None = None
    study_item_id: str | None = None
    library_item_id: str | None = None
    sentence_id: int | None = None
    speaking_session_id: str | None = None
    suggestion_index: int | None = None


class IntegrationOverview(IntegrationModel):
    review: ReviewSummary
    weak_chunks: list[StudyPracticeEvidence]
    continue_listening: list[ContinueListeningItem]
    speaking_suggestions: list[SpeakingSuggestionCandidate]
    next_actions: list[NextAction]
    has_study_items: bool
    has_library_items: bool
    has_speaking_sessions: bool


class SpeakingSuggestionSaveRequest(IntegrationModel):
    user_id: str = Field(min_length=1, max_length=128)
    target_language: str = Field(min_length=1, max_length=64)
    meaning: str | None = Field(default=None, max_length=1_000)
    note: str | None = Field(default=None, max_length=2_000)


class SpeakingSuggestionSaveResponse(IntegrationModel):
    item: StudyItem
    created: bool
    chunk_memory_synchronized: bool


class TargetLanguageList(IntegrationModel):
    target_languages: list[str]
