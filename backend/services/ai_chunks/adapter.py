from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Annotated, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from services.schemas.lesson import SourceLanguage


class AdapterModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceSegment(AdapterModel):
    segment_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=2_000)


class ProposedChunk(AdapterModel):
    text: str = Field(min_length=1, max_length=500)
    suggested_meaning_zh: str = Field(default="", max_length=1_000)
    usage_note: str = Field(default="", max_length=2_000)
    example: str = Field(default="", max_length=2_000)
    explanation_of_value: str = Field(default="", max_length=1_000)
    quality_flags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=20
    )
    evidence_segment_id: str = Field(min_length=1, max_length=64)


@runtime_checkable
class ChunkModelAdapter(Protocol):
    model_id: str
    proposal_version: str

    def propose(
        self,
        *,
        language: SourceLanguage,
        segments: list[SourceSegment],
        max_candidates: int,
    ) -> list[ProposedChunk]: ...


_KNOWN = {
    "en": {
        "as far as i know": "据我所知",
        "it depends on": "这取决于",
        "look forward to": "期待",
        "take into account": "把……考虑在内",
    },
    "de": {
        "auf jeden fall": "无论如何；肯定",
        "es kommt darauf an": "这取决于具体情况",
        "sich freuen auf": "期待",
        "im großen und ganzen": "总的来说",
    },
}


def normalize_phrase(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w'’äöüß]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


class DeterministicMockChunkAdapter:
    """Bounded test adapter. It never performs I/O or calls a model provider."""

    model_id = "mock-chunk-organizer"
    proposal_version = "1"

    def propose(
        self,
        *,
        language: SourceLanguage,
        segments: list[SourceSegment],
        max_candidates: int,
    ) -> list[ProposedChunk]:
        results: list[ProposedChunk] = []
        seen: set[str] = set()
        for segment in segments:
            normalized = normalize_phrase(segment.text)
            for phrase, meaning in _KNOWN[language].items():
                if phrase not in normalized or phrase in seen:
                    continue
                seen.add(phrase)
                results.append(ProposedChunk(
                    text=phrase,
                    suggested_meaning_zh=meaning,
                    usage_note="可作为完整表达在口语中复用。",
                    example=segment.text,
                    explanation_of_value="可复用的多词表达。",
                    quality_flags=["mock_adapter", "source_grounded"],
                    evidence_segment_id=segment.segment_id,
                ))
                if len(results) >= max_candidates:
                    return results
        if not results and segments:
            words = re.findall(r"[\w'’äöüÄÖÜß]+", segments[0].text, flags=re.UNICODE)
            if len(words) >= 2:
                phrase = " ".join(words[: min(4, len(words))])
                results.append(ProposedChunk(
                    text=phrase,
                    suggested_meaning_zh="",
                    usage_note="",
                    example=segments[0].text,
                    explanation_of_value="候选短语需要学习者确认。",
                    quality_flags=["mock_adapter", "meaning_uncertain"],
                    evidence_segment_id=segments[0].segment_id,
                ))
        return results[:max_candidates]
