from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from services.schemas.lesson import Lesson, TrainingStage


class LibraryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LibraryProgress(LibraryModel):
    is_training: bool = False
    stage_index: int = Field(default=0, ge=0)
    sentence_index: int = Field(default=0, ge=0)
    completed_stages: list[TrainingStage] = Field(default_factory=list)
    training_order: list[TrainingStage]
    lesson_memory_imported: bool = False


class PersistentAudio(LibraryModel):
    status: Literal["available", "unavailable"] = "unavailable"
    filename: str | None = None
    content_type: str | None = None


class ListeningProgress(LibraryModel):
    last_position_seconds: float = Field(default=0, ge=0)
    current_sentence_id: int | None = None
    playback_rate: Literal[0.5, 0.75, 1.0, 1.25] = 1.0
    source_visible: bool = True
    translation_visible: bool = True
    mode: Literal["listen", "blind", "dictation"] = "listen"


class LibraryItem(LibraryModel):
    id: str
    source_key: str
    created_at: datetime
    updated_at: datetime
    lesson: Lesson
    progress: LibraryProgress
    persistent_audio: PersistentAudio = Field(default_factory=PersistentAudio)
    listening_progress: ListeningProgress = Field(default_factory=ListeningProgress)


class LibraryItemSummary(LibraryModel):
    id: str
    lesson_id: str
    title: str
    source_url: str
    source_language: str
    target_language: str
    level: str
    source: str
    sentence_count: int
    duration_seconds: float | None
    created_at: datetime
    updated_at: datetime
    progress_status: Literal["not_started", "in_progress", "complete"]
    audio_available: bool
    last_position_seconds: float
    has_listening_progress: bool


class LibraryListResponse(LibraryModel):
    items: list[LibraryItemSummary]


class LibrarySaveRequest(LibraryModel):
    lesson: Lesson
    progress: dict[str, Any] | None = None


class LibraryProgressRequest(LibraryModel):
    is_training: bool
    stage_index: int
    sentence_index: int
    completed_stages: list[TrainingStage]
    training_order: list[TrainingStage]
    lesson_memory_imported: bool


class ListeningProgressRequest(ListeningProgress):
    pass
