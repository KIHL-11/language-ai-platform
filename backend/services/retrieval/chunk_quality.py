import html
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from services.schemas.lesson import SourceLanguage
from services.schemas.retrieval import StoredChunk, normalize_chunk_text


CAPTION_NOISE_LABELS = frozenset(
    {
        "music",
        "applause",
        "laughter",
        "laughs",
        "cheering",
        "audience cheering",
        "inaudible",
    }
)
REPEATED_INTERJECTION_NOISE = frozenset(
    {"ah", "hm", "hmm", "oh", "ooh", "uh", "ugh", "um", "whoa", "äh", "ähm"}
)

_LABEL_PATTERN = "|".join(
    re.escape(label).replace(r"\ ", r"\s+")
    for label in sorted(CAPTION_NOISE_LABELS, key=len, reverse=True)
)
_CAPTION_TAG_PATTERN = (
    rf"(?:\[\s*(?:{_LABEL_PATTERN})\s*\]|"
    rf"\(\s*(?:{_LABEL_PATTERN})\s*\))"
)
_BOUNDARY_MARKER_PATTERN = rf"(?:{_CAPTION_TAG_PATTERN}|>>|[♪♫]+)"
_LEADING_MARKERS = re.compile(
    rf"^(?:\s*{_BOUNDARY_MARKER_PATTERN}\s*)+", re.IGNORECASE
)
_TRAILING_MARKERS = re.compile(
    rf"(?:\s*{_BOUNDARY_MARKER_PATTERN}\s*)+$", re.IGNORECASE
)
_WORD_PATTERN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
_UPPERCASE_SPEAKER_PREFIX = re.compile(
    r"^[A-ZÄÖÜẞ][A-ZÄÖÜẞ'’.-]*,\s*(?P<remainder>.+)$"
)


class ChunkTextRejected(ValueError):
    """Raised when sanitized text has no reusable spoken-language content."""


@dataclass(frozen=True)
class PreparedChunkText:
    text: str
    normalized_text: str


@dataclass(frozen=True)
class ChunkCleanupPlan:
    chunks: tuple[StoredChunk, ...]
    cleaned_ids: tuple[str, ...]
    removed_ids: tuple[str, ...]
    collision_ids: tuple[str, ...]


def sanitize_chunk_text(text: str) -> str:
    """Return readable chunk text after one bounded subtitle-cleanup pass."""
    cleaned = html.unescape(text)
    cleaned = unicodedata.normalize("NFKC", cleaned)
    cleaned = _LEADING_MARKERS.sub("", cleaned)
    cleaned = _TRAILING_MARKERS.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _words(text: str) -> list[str]:
    return [word.casefold() for word in _WORD_PATTERN.findall(text)]


def _is_repeated_interjection(text: str) -> bool:
    words = _words(text)
    return len(words) >= 2 and all(
        word in REPEATED_INTERJECTION_NOISE for word in words
    )


def _has_reusable_spoken_language(text: str) -> bool:
    if not any(character.isalnum() for character in text):
        return False
    if _is_repeated_interjection(text):
        return False
    speaker_match = _UPPERCASE_SPEAKER_PREFIX.fullmatch(text)
    if speaker_match and _is_repeated_interjection(
        speaker_match.group("remainder")
    ):
        return False
    return True


def prepare_chunk_text(
    text: str, language: SourceLanguage
) -> PreparedChunkText:
    cleaned = sanitize_chunk_text(text)
    if not _has_reusable_spoken_language(cleaned):
        raise ChunkTextRejected(
            "chunk text must contain reusable spoken language"
        )
    return PreparedChunkText(
        text=cleaned,
        normalized_text=normalize_chunk_text(cleaned, language),
    )


def plan_chunk_cleanup(chunks: Sequence[StoredChunk]) -> ChunkCleanupPlan:
    """Plan an explicit cleanup without mutating or writing the source bank."""
    prepared: list[PreparedChunkText | None] = []
    grouped_indexes: dict[tuple[str, SourceLanguage, str], list[int]] = (
        defaultdict(list)
    )
    for index, chunk in enumerate(chunks):
        try:
            value = prepare_chunk_text(chunk.text, chunk.source_language)
        except ChunkTextRejected:
            prepared.append(None)
            continue
        prepared.append(value)
        grouped_indexes[
            (chunk.user_id, chunk.source_language, value.normalized_text)
        ].append(index)

    collision_indexes = {
        index
        for indexes in grouped_indexes.values()
        if len(indexes) > 1
        for index in indexes
    }
    output: list[StoredChunk] = []
    cleaned_ids: list[str] = []
    removed_ids: list[str] = []
    collision_ids: list[str] = []
    for index, (chunk, value) in enumerate(zip(chunks, prepared, strict=True)):
        if value is None:
            removed_ids.append(chunk.id)
            continue
        if index in collision_indexes:
            output.append(chunk)
            collision_ids.append(chunk.id)
            continue
        cleaned = chunk.model_copy(
            update={"text": value.text, "normalized_text": value.normalized_text}
        )
        output.append(cleaned)
        if cleaned != chunk:
            cleaned_ids.append(chunk.id)

    return ChunkCleanupPlan(
        chunks=tuple(output),
        cleaned_ids=tuple(cleaned_ids),
        removed_ids=tuple(removed_ids),
        collision_ids=tuple(collision_ids),
    )
