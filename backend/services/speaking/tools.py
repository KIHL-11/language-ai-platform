from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.library.store import LearningLibraryStore
from services.schemas.lesson import SourceLanguage
from services.schemas.speaking import SpeakingCorrection, SpeakingSuggestion
from services.study_bank.store import StudyBankStore


class ToolError(ValueError):
    pass


class BoundedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: SourceLanguage
    limit: int = Field(default=5, ge=1, le=10)


class MediaQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    library_item_id: str = Field(min_length=1, max_length=128)
    sentence_id: int | None = None


class ToolDispatcher:
    allowed_tools = {"get_due_chunks", "get_saved_words", "get_media_context", "record_correction", "suggest_expression"}

    def __init__(self, study_bank: StudyBankStore, library: LearningLibraryStore):
        self.study_bank, self.library = study_bank, library

    def execute(self, name: str, arguments: dict, *, user_id: str, language: str, library_item_id: str | None = None):
        if name not in self.allowed_tools:
            raise ToolError("Unknown speaking tool")
        try:
            if name in {"get_due_chunks", "get_saved_words"}:
                query = BoundedQuery.model_validate(arguments)
                if query.language != language:
                    raise ToolError("Tool language is outside this session")
                kind = "chunk" if name == "get_due_chunks" else "word"
                items = self.study_bank.list_items(user_id, kind=kind, language=language, active=True)
                if kind == "chunk":
                    items = sorted(items, key=lambda item: (item.review.due_at, -item.review.lapse_count, item.id))
                return [{"id": item.id, "text": item.text, "meaning": item.meaning} for item in items[:query.limit]]
            if name == "get_media_context":
                query = MediaQuery.model_validate(arguments)
                if library_item_id != query.library_item_id:
                    raise ToolError("Library item is outside this session")
                item = self.library.get_item(query.library_item_id)
                if item.lesson.source_language != language:
                    raise ToolError("Library language is outside this session")
                sentences = item.lesson.sentences
                center = next((i for i, sentence in enumerate(sentences) if sentence.id == query.sentence_id), 0)
                window = sentences[max(0, center - 5):max(0, center - 5) + 12]
                return {"title": item.lesson.title, "sentences": [{"id": s.id, "text": s.text, "translation": s.translation, "start": s.start, "end": s.end} for s in window]}
            if name == "record_correction":
                return SpeakingCorrection.model_validate(arguments).model_dump(mode="json")
            return SpeakingSuggestion.model_validate(arguments).model_dump(mode="json")
        except ValidationError as exc:
            raise ToolError("Invalid speaking tool arguments") from exc
