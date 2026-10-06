from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from services.schemas.lesson import SourceLanguage


class RetrievalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StoredChunk(RetrievalModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    meaning: str = ""
    source_language: SourceLanguage
    source_lesson_id: str = Field(min_length=1)
    source_sentence_id: int = Field(gt=0)
    created_at: datetime


class RetrievedChunk(StoredChunk):
    score: float = Field(ge=-1.0, le=1.0)


class ChunkSearchRequest(RetrievalModel):
    query: str = Field(min_length=1)
    source_language: SourceLanguage
    top_k: int = Field(default=5, ge=1, le=50)


class ChunkSearchResponse(RetrievalModel):
    results: list[RetrievedChunk]


class StoreLessonChunksResponse(RetrievalModel):
    added: int
    total: int

