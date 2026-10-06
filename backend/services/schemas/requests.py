from pydantic import AnyHttpUrl, BaseModel, ConfigDict, model_validator

from services.schemas.lesson import LessonLevel, SourceLanguage


class LessonFromUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl
    source_language: SourceLanguage = "en"
    target_language: str = "zh-CN"
    level: LessonLevel = "B2"
    start: float | None = None
    end: float | None = None
    vocals: bool = False

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.start is not None and self.start < 0:
            raise ValueError("start must be greater than or equal to zero")
        if self.end is not None and self.end < 0:
            raise ValueError("end must be greater than or equal to zero")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("start must be less than end")
        return self
