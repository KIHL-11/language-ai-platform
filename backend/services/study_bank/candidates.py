import hashlib
import html
import json
import re
import unicodedata

from services.retrieval.chunk_quality import ChunkTextRejected, prepare_chunk_text
from services.schemas.library import LibraryItem
from services.schemas.study_bank import (
    StudyCandidate,
    StudyItem,
    StudyProvenance,
)
from services.study_bank.store import normalized_identity
from services.schemas.study_bank import StudyItemInput
from services.transcript.hygiene import clean_transcript_text


NOISE_LABEL = re.compile(
    r"^[\[(]\s*(?:music|musik|applause|laughter|singing|singen|cheering|inaudible)\s*[\])]$",
    re.IGNORECASE,
)
WORDS = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


def _candidate_id(parts: list[object]) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _provenance(item: LibraryItem, sentence) -> StudyProvenance:
    return StudyProvenance(
        library_item_id=item.id,
        lesson_id=item.lesson.id,
        source_url=item.lesson.source_url,
        media_title=item.lesson.title,
        sentence_id=sentence.id,
        sentence_text=sentence.text,
        sentence_translation=sentence.translation,
        start=sentence.start,
        end=sentence.end,
    )


def _word_quality(text: str) -> tuple[str, list[str]]:
    cleaned = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", html.unescape(text))).strip()
    if (
        not cleaned
        or not clean_transcript_text(cleaned)
        or NOISE_LABEL.fullmatch(cleaned)
        or not any(ch.isalnum() for ch in cleaned)
    ):
        return "rejected", ["caption noise or no lexical content"]
    if not WORDS.findall(cleaned):
        return "rejected", ["no lexical word content"]
    return "recommended", ["clean analyzed word candidate"]


def _chunk_quality(text: str, language: str) -> tuple[str, list[str], str, str]:
    cleaned_input = re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", html.unescape(text))
    ).strip()
    if not clean_transcript_text(cleaned_input) or NOISE_LABEL.fullmatch(cleaned_input):
        return "rejected", ["caption noise or no reusable spoken language"], cleaned_input, "rejected"
    try:
        prepared = prepare_chunk_text(text, language)
    except ChunkTextRejected:
        return "rejected", ["caption noise or no reusable spoken language"], text.strip(), "rejected"
    words = WORDS.findall(prepared.text)
    lower = prepared.text.casefold()
    if len(words) == 1 or (len(words) <= 2 and (lower.endswith("?") or lower.startswith("to "))):
        return "review", ["short or potentially fragmentary expression"], prepared.text, prepared.normalized_text
    if len(words) > 14 or len(prepared.text) > 180:
        return "review", ["unusually long expression; review context"], prepared.text, prepared.normalized_text
    return "recommended", ["clean multi-word spoken expression"], prepared.text, prepared.normalized_text


def generate_candidates(item: LibraryItem, saved: list[StudyItem]) -> list[StudyCandidate]:
    saved_by_key = {
        (entry.kind, entry.source_language, entry.normalized_text): entry
        for entry in saved
    }
    output: list[StudyCandidate] = []
    seen_candidates: set[tuple[str, int, str]] = set()

    def provenance_saved(existing: StudyItem | None, value: StudyProvenance) -> bool:
        if existing is None:
            return False
        fingerprint = value.model_dump_json()
        return any(record.model_dump_json() == fingerprint for record in existing.provenance)

    for sentence in item.lesson.sentences:
        provenance = _provenance(item, sentence)
        for keyword_index, keyword in enumerate(sentence.keywords):
            quality, reasons = _word_quality(keyword.word)
            try:
                _, normalized = normalized_identity(StudyItemInput(
                    user_id="candidate", kind="word",
                    source_language=item.lesson.source_language,
                    target_language=item.lesson.target_language,
                    text=keyword.word, meaning=keyword.meaning, lemma=keyword.lemma or None,
                    part_of_speech=keyword.pos or None, example=keyword.example or None,
                    provenance=[provenance],
                ))
            except ValueError:
                normalized = "rejected"
                quality, reasons = "rejected", ["caption noise or no lexical content"]
            candidate_key = (
                "word",
                sentence.id,
                normalized if normalized != "rejected" else keyword.word.casefold(),
            )
            if candidate_key in seen_candidates:
                continue
            seen_candidates.add(candidate_key)
            existing = saved_by_key.get(("word", item.lesson.source_language, normalized))
            output.append(StudyCandidate(
                candidate_id=_candidate_id([item.id, "word", sentence.id, keyword_index, normalized, keyword.word]),
                kind="word", source_language=item.lesson.source_language,
                target_language=item.lesson.target_language, text=keyword.word,
                normalized_text=normalized, meaning=keyword.meaning,
                lemma=keyword.lemma or None, part_of_speech=keyword.pos or None,
                example=keyword.example or None, quality=quality,
                quality_reasons=reasons, provenance=provenance,
                already_saved=provenance_saved(existing, provenance),
                study_item_id=existing.id if existing else None,
            ))
        for chunk_index, chunk in enumerate(sentence.chunks):
            quality, reasons, cleaned, normalized = _chunk_quality(
                chunk.text, item.lesson.source_language
            )
            candidate_key = (
                "chunk",
                sentence.id,
                normalized if normalized != "rejected" else chunk.text.casefold(),
            )
            if candidate_key in seen_candidates:
                continue
            seen_candidates.add(candidate_key)
            existing = saved_by_key.get(("chunk", item.lesson.source_language, normalized))
            output.append(StudyCandidate(
                candidate_id=_candidate_id([item.id, "chunk", sentence.id, chunk_index, normalized, chunk.text]),
                kind="chunk", source_language=item.lesson.source_language,
                target_language=item.lesson.target_language, text=cleaned,
                normalized_text=normalized, meaning=chunk.meaning,
                usage=chunk.usage or None, quality=quality,
                quality_reasons=reasons, provenance=provenance,
                already_saved=provenance_saved(existing, provenance),
                study_item_id=existing.id if existing else None,
            ))
    return output
