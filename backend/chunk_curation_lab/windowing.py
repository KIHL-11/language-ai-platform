"""Full-transcript windowing by token budget on sentence boundaries.

Windowing decides what each provider call sees; it never ranks anything.
Rules (FULL-TRANSCRIPT-RANKING-SPEC.md §2):

- A sentence that fits the budget is one segment and is never split across
  windows.
- An oversized sentence is split at clause punctuation, then at word
  boundaries; every piece keeps the sentence id and its character range.
  If a single word still exceeds the budget the whole sentence is skipped
  with a reason; it is never truncated.
- The last `overlap` citable segments of the previous window are repeated as
  context only. Context is not citable and never counts as coverage.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from chunk_curation_lab.snapshot import SnapshotSentence

# Conservative characters-per-token ratios when no tokenizer is available.
# German compounds tokenise longer, so it gets the smaller ratio.
CHARS_PER_TOKEN = {"en": 3.2, "de": 2.8}

_CLAUSE_BREAK = re.compile(r"(?<=[,;:!?.–—])\s+")


def estimate_tokens(text: str, language: str) -> int:
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN[language]))


@dataclass(frozen=True)
class Segment:
    sentence_id: int
    part: int
    parts: int
    char_start: int
    char_end: int
    text: str
    tokens: int


@dataclass(frozen=True)
class Window:
    index: int
    citable: tuple[Segment, ...]
    context: tuple[Segment, ...]

    @property
    def tokens(self) -> int:
        return sum(value.tokens for value in self.citable + self.context)

    @property
    def citable_sentence_ids(self) -> frozenset[int]:
        return frozenset(value.sentence_id for value in self.citable)


@dataclass(frozen=True)
class SentenceSkip:
    sentence_id: int
    reason: str  # "empty_sentence" | "sentence_exceeds_window"


@dataclass(frozen=True)
class WindowPlan:
    language: str
    budget_tokens: int
    sentence_ids: tuple[int, ...]  # every snapshot sentence, transcript order
    windows: tuple[Window, ...]
    skipped: tuple[SentenceSkip, ...]


def _pieces(text: str, offset: int, pattern) -> list[tuple[int, int]]:
    """Character ranges of `text` split after each match of `pattern`."""
    ranges, start = [], 0
    for match in pattern.finditer(text):
        ranges.append((offset + start, offset + match.start()))
        start = match.end()
    ranges.append((offset + start, offset + len(text)))
    return [(a, b) for a, b in ranges if b > a]


def _pack(text: str, ranges: list[tuple[int, int]], language: str, budget: int):
    """Greedily join adjacent ranges while they fit the budget."""
    packed: list[tuple[int, int]] = []
    for start, end in ranges:
        if packed and estimate_tokens(text[packed[-1][0]:end], language) <= budget:
            packed[-1] = (packed[-1][0], end)
        else:
            packed.append((start, end))
    return packed


def split_sentence(sentence: SnapshotSentence, language: str, budget: int) -> list[Segment] | None:
    """Segments of one sentence, or None when it cannot fit at all."""
    text = sentence.text
    if estimate_tokens(text, language) <= budget:
        ranges = [(0, len(text))]
    else:
        ranges = []
        for start, end in _pack(text, _pieces(text, 0, _CLAUSE_BREAK), language, budget):
            if estimate_tokens(text[start:end], language) <= budget:
                ranges.append((start, end))
                continue
            words = _pieces(text[start:end], start, re.compile(r"\s+"))
            if any(estimate_tokens(text[a:b], language) > budget for a, b in words):
                return None
            ranges.extend(_pack(text, words, language, budget))
    return [
        Segment(sentence.sentence_id, part, len(ranges), start, end, text[start:end],
                estimate_tokens(text[start:end], language))
        for part, (start, end) in enumerate(ranges)
    ]


def build_windows(
    sentences: tuple[SnapshotSentence, ...] | list[SnapshotSentence],
    *,
    language: str,
    budget_tokens: int,
    overlap: int = 1,
) -> WindowPlan:
    if budget_tokens < 1 or overlap < 0:
        raise ValueError("budget_tokens must be positive and overlap non-negative")
    segments: list[Segment] = []
    skipped: list[SentenceSkip] = []
    for sentence in sentences:
        if not sentence.text.strip():
            skipped.append(SentenceSkip(sentence.sentence_id, "empty_sentence"))
            continue
        parts = split_sentence(sentence, language, budget_tokens)
        if parts is None:
            skipped.append(SentenceSkip(sentence.sentence_id, "sentence_exceeds_window"))
        else:
            segments.extend(parts)

    windows: list[Window] = []
    current: list[Segment] = []
    context: tuple[Segment, ...] = ()

    def close():
        nonlocal current, context
        windows.append(Window(len(windows), tuple(current), context))
        context = tuple(current[-overlap:]) if overlap else ()
        current = []

    for segment in segments:
        used = sum(value.tokens for value in current + list(context))
        if current and used + segment.tokens > budget_tokens:
            close()
            used = sum(value.tokens for value in context)
        if not current and used + segment.tokens > budget_tokens:
            context = ()  # context is optional; the citable segment is not
        current.append(segment)
    if current:
        close()
    return WindowPlan(
        language=language,
        budget_tokens=budget_tokens,
        sentence_ids=tuple(value.sentence_id for value in sentences),
        windows=tuple(windows),
        skipped=tuple(skipped),
    )
