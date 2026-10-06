from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SourceLanguage = Literal["en", "de"]
LessonLevel = Literal["B1", "B2", "C1"]
AnalysisStatus = Literal["ok", "failed"]
LessonAIStatus = Literal["complete", "partial", "failed"]
TrainingStage = Literal[
    "active_recall",
    "blind_listening",
    "comprehension",
    "mini_dictation",
    "shadowing",
    "retell",
    "live_dialogue",
]

TRAINING_ORDER: list[TrainingStage] = [
    "active_recall",
    "blind_listening",
    "comprehension",
    "mini_dictation",
    "shadowing",
    "retell",
    "live_dialogue",
]


class SchemaModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Keyword(SchemaModel):
    word: str = ""
    meaning: str = ""
    lemma: str = ""
    pos: str = ""
    example: str = ""


class Chunk(SchemaModel):
    text: str = ""
    meaning: str = ""
    usage: str = ""


class SentenceTraining(SchemaModel):
    blind_listening: bool = True
    dictation: bool = True
    shadowing: bool = True
    retell: bool = True


class LessonSentence(SchemaModel):
    id: int = Field(gt=0)
    text: str
    translation: str = ""
    start: float = Field(default=0.0, ge=0)
    end: float = Field(default=0.0, ge=0)
    keywords: list[Keyword] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)
    grammar: str = ""
    training: SentenceTraining = Field(default_factory=SentenceTraining)
    ai_status: AnalysisStatus = "failed"

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end < self.start:
            raise ValueError("sentence end must be greater than or equal to start")
        return self


class EnabledTraining(SchemaModel):
    enabled: bool = True


class ActiveRecallTraining(EnabledTraining):
    count: int = Field(default=5, ge=0)


class TrainingPlan(SchemaModel):
    active_recall: ActiveRecallTraining = Field(default_factory=ActiveRecallTraining)
    blind_listening: EnabledTraining = Field(default_factory=EnabledTraining)
    comprehension: EnabledTraining = Field(default_factory=EnabledTraining)
    mini_dictation: EnabledTraining = Field(default_factory=EnabledTraining)
    shadowing: EnabledTraining = Field(default_factory=EnabledTraining)
    retell: EnabledTraining = Field(default_factory=EnabledTraining)
    live_dialogue: EnabledTraining = Field(default_factory=EnabledTraining)


class LessonAI(SchemaModel):
    provider: str
    status: LessonAIStatus


class Lesson(SchemaModel):
    id: str
    title: str
    source_url: str
    source_language: SourceLanguage
    target_language: str = "zh-CN"
    level: LessonLevel
    audio_url: str | None = None
    source: Literal["subtitles", "whisper"]
    sentences: list[LessonSentence]
    training_plan: TrainingPlan = Field(default_factory=TrainingPlan)
    training_order: list[TrainingStage] = Field(
        default_factory=lambda: TRAINING_ORDER.copy()
    )
    ai: LessonAI

    @model_validator(mode="after")
    def validate_training_order(self):
        if self.training_order != TRAINING_ORDER:
            raise ValueError("training_order must use the fixed M1 training sequence")
        return self
