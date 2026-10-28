from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.schemas.lesson import SourceLanguage


class DictionaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


DictionaryStatus = Literal["found", "ambiguous", "not_found", "not_configured"]
ExplanationStatus = Literal["available", "unavailable"]
InflectionsStatus = Literal[
    "not_requested", "available", "index_unavailable", "index_stale", "index_invalid"
]


class DictionaryLookupRequest(DictionaryModel):
    language: SourceLanguage
    word: str = Field(min_length=1, max_length=100)
    sentence: str = Field(default="", max_length=2_000)
    target_language: Literal["zh-CN"] = "zh-CN"
    include_inflections: bool = False

    @field_validator("word")
    @classmethod
    def lexical_word_only(cls, value: str):
        if not any(character.isalpha() for character in value):
            raise ValueError("word must contain lexical characters")
        return value


class DictionaryExplanation(DictionaryModel):
    language: str
    value: str | None = None
    status: ExplanationStatus
    source: str | None = None
    source_url: str | None = None
    license: str | None = None
    kind: Literal["dictionary_definition", "dictionary_translation"]


class DictionaryExplanationLevels(DictionaryModel):
    monolingual: DictionaryExplanation
    cross_language: DictionaryExplanation
    chinese: DictionaryExplanation


class DictionaryPronunciation(DictionaryModel):
    ipa: str | None = None
    accent: str
    source: str
    source_url: str | None = None
    license: str | None = None
    audio_url: str | None = None
    audio_available: bool
    tts_fallback: bool = False


class DictionaryAttribution(DictionaryModel):
    source: str
    source_url: str
    license: str


class DictionarySense(DictionaryModel):
    definition: str
    examples: list[str] = Field(default_factory=list, max_length=5)
    source: str


class DictionaryContext(DictionaryModel):
    source_sentence: str
    selection_method: Literal[
        "lexical_overlap", "part_of_speech", "morphology", "source_order",
        "ambiguous", "unavailable"
    ]
    contextual_sense: str | None = None
    contextual_explanation: str | None = None


class DictionaryLookupResponse(DictionaryModel):
    query: str
    surface_form: str
    lemma: str | None = None
    lemma_candidates: list[str] = Field(default_factory=list, max_length=20)
    language: SourceLanguage
    part_of_speech: str | None = None
    status: DictionaryStatus
    pronunciations: list[DictionaryPronunciation] = Field(default_factory=list)
    explanations: DictionaryExplanationLevels
    context: DictionaryContext
    inflections: list[str] = Field(default_factory=list, max_length=30)
    inflections_status: InflectionsStatus = "not_requested"
    inflections_message: str | None = None
    examples: list[str] = Field(default_factory=list, max_length=10)
    alternate_senses: list[DictionarySense] = Field(default_factory=list)
    source_attribution: list[DictionaryAttribution] = Field(default_factory=list)


class DictionaryRuntimeStatus(DictionaryModel):
    configured: bool
    release_id: str | None = None
    forms_index_status: InflectionsStatus
