import json
import os
import re
from typing import Any

from services.ai.llm_client import llm
from services.schemas.lesson import Chunk, Keyword


_CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _as_analysis_list(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("sentences", "analyses", "results", "items"):
            items = value.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        if "id" in value:
            return [value]
    raise ValueError("AI response must contain a JSON array of sentence analyses")


def parse_analysis_json(content: str) -> list[dict]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("AI response is empty")

    cleaned = content.strip()
    fenced = _CODE_FENCE.match(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()

    try:
        return _as_analysis_list(json.loads(cleaned))
    except (json.JSONDecodeError, ValueError):
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
            return _as_analysis_list(value)
        except (json.JSONDecodeError, ValueError):
            continue

    raise ValueError("AI response does not contain valid JSON analysis")

def _mock_analysis(sentence: dict) -> dict:
    text = sentence["text"]

    words = text.replace(".", "").split()

    keyword = words[-1] if words else ""

    return {
        "id": sentence["id"],
        "text": text,
        "translation": f"Mock translation: {text}",
        "start": sentence["start"],
        "end": sentence["end"],
        "keywords": [
            {
                "word": keyword,
                "meaning": "core expression",
                "lemma": keyword,
                "pos": "unknown",
                "example": text
            }
        ],
        "chunks": [
            {
                "text": text,
                "meaning": "reusable spoken expression",
                "usage": "daily conversation"
            }
        ],
        "grammar": "Mock grammar analysis",
        "ai_status": "ok",
    }

def mock_analyze_sentences(sentences: list[dict]) -> list[dict]:
    return [
        _mock_analysis(sentence)
        for sentence in sentences
    ]
def get_batch_size() -> int:
    raw_value = os.getenv("LESSON_AI_BATCH_SIZE", "10")
    try:
        value = int(raw_value)
    except ValueError:
        return 10
    return value if value > 0 else 10


def _empty_analysis(sentence: dict) -> dict:
    return {
        "id": sentence["id"],
        "text": sentence["text"],
        "translation": "",
        "start": sentence["start"],
        "end": sentence["end"],
        "keywords": [],
        "chunks": [],
        "grammar": "",
        "ai_status": "failed",
    }


def _validated_items(items: Any, model_type) -> list[dict]:
    if not isinstance(items, list):
        return []
    validated = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            validated.append(model_type.model_validate(item).model_dump())
        except (TypeError, ValueError):
            continue
    return validated


def _merge_batch(batch: list[dict], analyses: list[dict]) -> list[dict]:
    allowed_ids = {sentence["id"] for sentence in batch}
    by_id: dict[int, dict] = {}
    for analysis in analyses:
        analysis_id = analysis.get("id")
        if isinstance(analysis_id, bool) or not isinstance(analysis_id, int):
            continue
        if analysis_id in allowed_ids and analysis_id not in by_id:
            by_id[analysis_id] = analysis

    merged = []
    for sentence in batch:
        result = _empty_analysis(sentence)
        analysis = by_id.get(sentence["id"])
        if analysis is not None:
            translation = analysis.get("translation", "")
            grammar = analysis.get("grammar", "")
            result.update(
                {
                    "translation": translation if isinstance(translation, str) else "",
                    "keywords": _validated_items(analysis.get("keywords"), Keyword),
                    "chunks": _validated_items(analysis.get("chunks"), Chunk),
                    "grammar": grammar if isinstance(grammar, str) else "",
                    "ai_status": "ok",
                }
            )
        merged.append(result)
    return merged


def _prompt_for_batch(
    batch: list[dict], source_language: str, target_language: str
) -> str:
    payload = [{"id": sentence["id"], "text": sentence["text"]} for sentence in batch]
    return f"""You are a language lesson analyst for English and German source material.
Source language: {source_language}
Translation target language: {target_language}

Analyze every supplied sentence exactly once.
- Preserve each id exactly and do not invent IDs.
- Do not rewrite or return the source text.
- Return JSON only: one array with one object per supplied id.
- Each object must contain id, translation, keywords, chunks, and grammar.
- Each keyword must contain word, meaning, lemma, pos, and example.
- Choose only genuinely useful learning keywords.
- Each chunk must contain text, meaning, and usage. Prefer natural spoken collocations,
  fixed expressions, and reusable phrases.
- Keep grammar concise and return an empty string when nothing needs explanation.
- Write translation, meanings, usage, and grammar in {target_language}.

Input:
{json.dumps(payload, ensure_ascii=False)}
"""


def analyze_sentences(
    sentences: list[dict],
    source_language: str,
    target_language: str = "zh-CN",
    batch_size: int | None = None,
    llm_client=None,
) -> list[dict]:
    if llm_client is None:
        if os.getenv("AI_MODE", "").strip().lower() == "mock":
            return mock_analyze_sentences(sentences)
        llm_client = llm

    size = batch_size or get_batch_size()

    if size <= 0:
        size = get_batch_size()

    analyzed: list[dict] = []

    for offset in range(0, len(sentences), size):

        batch = sentences[offset : offset + size]

        try:
            content = llm_client.chat(
                [
                    {
                        "role": "user",
                        "content": _prompt_for_batch(
                            batch,
                            source_language=source_language,
                            target_language=target_language,
                        ),
                    }
                ]
            )

            analyses = parse_analysis_json(content)

            analyzed.extend(
                _merge_batch(batch, analyses)
            )

        except Exception:
            analyzed.extend(
                _empty_analysis(sentence)
                for sentence in batch
            )

    return analyzed
