from dataclasses import dataclass

from services.transcript.utterances import (
    TranscriptToken,
    normalized_token,
    timed_text_tokens,
    tokens_to_utterances,
)


REVISION_ADJACENCY_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class CaptionWindow:
    start: float
    end: float
    text: str
    tokens: tuple[TranscriptToken, ...] = ()
    speaker: str | None = None


def _normalized(tokens: list[TranscriptToken]) -> list[str]:
    return [normalized_token(token.text) for token in tokens]


def _longest_overlap(existing: list[str], incoming: list[str]) -> int:
    for size in range(min(len(existing), len(incoming)), 0, -1):
        if existing[-size:] == incoming[:size]:
            return size
    return 0


def canonical_caption_tokens(windows: list[CaptionWindow]) -> list[TranscriptToken]:
    emitted: list[TranscriptToken] = []
    previous: CaptionWindow | None = None
    previous_normalized: list[str] = []
    for window in sorted(windows, key=lambda value: (value.start, value.end)):
        incoming = list(window.tokens) or timed_text_tokens(
            window.text,
            window.start,
            window.end,
            speaker=window.speaker,
        )
        if not incoming:
            previous = window
            previous_normalized = []
            continue
        normalized = _normalized(incoming)
        overlap_in_time = previous is not None and window.start < previous.end
        adjacent = previous is not None and 0 <= window.start - previous.end <= REVISION_ADJACENCY_SECONDS
        overlap = _longest_overlap(_normalized(emitted), normalized)
        contained_revision = bool(
            overlap_in_time
            and previous_normalized
            and len(normalized) <= len(previous_normalized)
            and any(
                previous_normalized[index:index + len(normalized)] == normalized
                for index in range(len(previous_normalized) - len(normalized) + 1)
            )
        )
        cumulative_revision = bool(
            (overlap_in_time or adjacent)
            and overlap
            and len(normalized) > overlap
            and (overlap_in_time or overlap >= 2)
        )
        exact_revision = bool(overlap_in_time and normalized == previous_normalized)
        if not (contained_revision or exact_revision):
            emitted.extend(incoming[overlap:] if cumulative_revision or overlap_in_time else incoming)
        previous = window
        previous_normalized = normalized
    return emitted


def caption_windows_to_utterances(
    windows: list[CaptionWindow], *, offset: float = 0.0
) -> list[dict]:
    return tokens_to_utterances(canonical_caption_tokens(windows), offset=offset)


def cues_to_utterances(cues: list[tuple[float, float, str]], *, offset: float = 0.0) -> list[dict]:
    return caption_windows_to_utterances(
        [CaptionWindow(start, end, text) for start, end, text in cues],
        offset=offset,
    )
