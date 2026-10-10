import html
import math
import re
import statistics
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Any

from services.schemas.transcript_features import TranscriptLearningFeatures


# Product-analysis heuristics. These describe subtitle structure, not learner
# ability, acoustic quality, pronunciation, or CEFR level.
SHORT_CUE_MAX_WORDS = 3
LONG_CUE_MIN_WORDS = 15
LONG_GAP_SECONDS = 2.0
MIN_TRANSCRIPT_CUES = 3
MIN_TRANSCRIPT_WORDS = 20
EXCESSIVE_OVERLAP_RATIO = 0.25
SPARSE_TIMING_LONG_GAP_RATIO = 0.5
DUPLICATE_CUE_MAX_GAP_SECONDS = 0.25
MIN_CUE_DURATION_SECONDS = 0.001
MAX_TIMESTAMP_SECONDS = 86_400.0

_FORMATTING_TAG = re.compile(
    r"</?(?:b|i|u|font(?:\s+[^>]*)?|c(?:\.[^ >]+)?|v(?:\s+[^>]*)?)\s*>",
    flags=re.IGNORECASE,
)
_INLINE_TIMESTAMP = re.compile(r"<\d{1,2}:\d{2}:\d{2}[.,]\d+>")
_ASS_FORMATTING = re.compile(r"\{\\[^{}]*\}")
_TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", flags=re.UNICODE)


@dataclass(frozen=True)
class _Cue:
    start: float
    end: float
    text: str
    tokens: tuple[str, ...]
    original_index: int


def _normalize_text(value: str) -> str:
    text = html.unescape(value)
    text = _INLINE_TIMESTAMP.sub("", text)
    text = _FORMATTING_TAG.sub("", text)
    text = _ASS_FORMATTING.sub("", text)
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split())


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _TOKEN.findall(text))


def _timestamp(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= MAX_TIMESTAMP_SECONDS:
        return None
    return result


def _read_cues(cues: Sequence[Mapping[str, Any] | Any]) -> tuple[list[_Cue], bool]:
    valid: list[_Cue] = []
    malformed = False
    for index, raw in enumerate(cues):
        if not isinstance(raw, Mapping):
            malformed = True
            continue
        start, end = _timestamp(raw.get("start")), _timestamp(raw.get("end"))
        raw_text = raw.get("text")
        if (
            start is None
            or end is None
            or end - start < MIN_CUE_DURATION_SECONDS
            or not isinstance(raw_text, str)
        ):
            malformed = True
            continue
        text = _normalize_text(raw_text)
        tokens = _tokens(text)
        if not text or not tokens:
            malformed = True
            continue
        valid.append(_Cue(start, end, text, tokens, index))
    valid.sort(key=lambda cue: (cue.start, cue.end, cue.original_index))
    return valid, malformed


def _deduplicate_adjacent(cues: list[_Cue]) -> tuple[list[_Cue], bool]:
    result: list[_Cue] = []
    duplicated = False
    for cue in cues:
        previous = result[-1] if result else None
        if (
            previous is not None
            and cue.text.casefold() == previous.text.casefold()
            and cue.start <= previous.end + DUPLICATE_CUE_MAX_GAP_SECONDS
        ):
            duplicated = True
            result[-1] = _Cue(
                start=previous.start,
                end=max(previous.end, cue.end),
                text=previous.text,
                tokens=previous.tokens,
                original_index=previous.original_index,
            )
            continue
        result.append(cue)
    return result, duplicated


def _timing(cues: list[_Cue]) -> tuple[float, float, list[float], float]:
    if not cues:
        return 0.0, 0.0, [], 0.0

    timeline_duration = max(cue.end for cue in cues) - min(cue.start for cue in cues)
    active_duration = 0.0
    gaps: list[float] = []
    overlap_count = 0
    interval_start, interval_end = cues[0].start, cues[0].end

    for cue in cues[1:]:
        if cue.start < interval_end:
            overlap_count += 1
            gaps.append(0.0)
            interval_end = max(interval_end, cue.end)
        else:
            active_duration += interval_end - interval_start
            gaps.append(cue.start - interval_end)
            interval_start, interval_end = cue.start, cue.end
    active_duration += interval_end - interval_start

    overlap_ratio = overlap_count / (len(cues) - 1) if len(cues) > 1 else 0.0
    return timeline_duration, active_duration, gaps, overlap_ratio


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def extract_transcript_learning_features(
    raw_cues: Sequence[Mapping[str, Any] | Any],
) -> TranscriptLearningFeatures:
    """Return deterministic text/timing features for existing transcript cues.

    ``duration_seconds`` is the transcript timeline from the earliest valid cue
    start to the latest valid cue end. ``active_duration_seconds`` is the union
    of valid cue intervals, so overlaps are counted once and gaps are excluded.
    WPM uses active duration and is only a transcript timing metric; it does not
    measure acoustic speech rate or clarity.
    """

    valid_cues, malformed = _read_cues(raw_cues)
    cues, duplicated = _deduplicate_adjacent(valid_cues)
    duration, active_duration, gaps, _ = _timing(cues)
    _, _, _, raw_overlap_ratio = _timing(valid_cues)

    all_tokens = [token for cue in cues for token in cue.tokens]
    word_counts = [len(cue.tokens) for cue in cues]
    token_frequencies = Counter(all_tokens)
    word_count, cue_count = len(all_tokens), len(cues)
    long_gap_count = sum(gap > LONG_GAP_SECONDS for gap in gaps)
    long_gap_ratio = _ratio(long_gap_count, len(gaps))

    flags = []
    if cue_count < MIN_TRANSCRIPT_CUES or word_count < MIN_TRANSCRIPT_WORDS:
        flags.append("insufficient_transcript")
    if not cues or active_duration <= 0:
        flags.append("insufficient_timing")
    if raw_overlap_ratio >= EXCESSIVE_OVERLAP_RATIO and len(valid_cues) > 1:
        flags.append("excessive_overlap")
    if gaps and long_gap_ratio >= SPARSE_TIMING_LONG_GAP_RATIO:
        flags.append("sparse_timing")
    if duplicated:
        flags.append("duplicated_cues")
    if malformed:
        flags.append("malformed_cues")

    return TranscriptLearningFeatures(
        duration_seconds=duration,
        active_duration_seconds=active_duration,
        word_count=word_count,
        words_per_minute=_ratio(word_count * 60, active_duration),
        cue_count=cue_count,
        cues_per_minute=_ratio(cue_count * 60, duration),
        average_words_per_cue=_ratio(word_count, cue_count),
        median_words_per_cue=statistics.median(word_counts) if word_counts else 0.0,
        lexical_diversity=_ratio(len(token_frequencies), word_count),
        repeated_token_ratio=_ratio(
            sum(count for count in token_frequencies.values() if count > 1),
            word_count,
        ),
        question_cue_ratio=_ratio(
            sum("?" in cue.text or "？" in cue.text for cue in cues), cue_count
        ),
        short_cue_ratio=_ratio(
            sum(count <= SHORT_CUE_MAX_WORDS for count in word_counts), cue_count
        ),
        long_cue_ratio=_ratio(
            sum(count >= LONG_CUE_MIN_WORDS for count in word_counts), cue_count
        ),
        median_gap_seconds=statistics.median(gaps) if gaps else 0.0,
        long_gap_ratio=long_gap_ratio,
        data_quality_flags=tuple(flags),
    )
