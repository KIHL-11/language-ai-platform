import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from services.transcript.hygiene import clean_transcript_text


STRONG_PAUSE_SECONDS = 0.9
CLAUSE_PAUSE_SECONDS = 0.5
PUNCTUATION_PAUSE_SECONDS = 0.1
_TOKEN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*|\d+(?:[.,]\d+)*|[.!?…]+|[,;:]+|[^\w\s]", re.UNICODE)
_STRONG_END = re.compile(r"[.!?…]+$")
_CLAUSE_END = re.compile(r"[,;:]+$")
TimingSource = Literal["source_inline", "acoustic", "estimated"]


@dataclass(frozen=True, slots=True)
class TranscriptToken:
    text: str
    start: float
    end: float
    speaker: str | None = None
    timing_source: TimingSource = "estimated"
    source_window: int | None = None
    source_line: int | None = None
    ambiguous: bool = False

    def __post_init__(self):
        if not self.text or self.start < 0 or self.end < self.start:
            raise ValueError("Invalid transcript token")


def normalized_token(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("’", "'")


def timed_text_tokens(
    text: str,
    start: float,
    end: float,
    *,
    speaker: str | None = None,
    timing_source: TimingSource = "estimated",
    source_window: int | None = None,
    source_line: int | None = None,
) -> list[TranscriptToken]:
    cleaned = clean_transcript_text(text)
    values = _TOKEN.findall(cleaned)
    if not values:
        return []
    lexical_count = sum(any(character.isalnum() for character in value) for value in values)
    width = max(0.0, end - start) / max(lexical_count, 1)
    output: list[TranscriptToken] = []
    lexical_index = 0
    for value in values:
        if any(character.isalnum() for character in value):
            token_start = start + lexical_index * width
            lexical_index += 1
            token_end = end if lexical_index == lexical_count else start + lexical_index * width
        else:
            token_start = output[-1].end if output else start
            token_end = token_start
        output.append(TranscriptToken(
            text=value,
            start=token_start,
            end=token_end,
            speaker=speaker,
            timing_source=timing_source,
            source_window=source_window,
            source_line=source_line,
        ))
    return output


def render_tokens(tokens: list[TranscriptToken]) -> str:
    output = ""
    for token in tokens:
        if not output:
            output = token.text
        elif re.fullmatch(r"[.!?…,;:)\]}]+", token.text):
            output += token.text
        elif output.endswith(("(", "[", "{", "“", '"')):
            output += token.text
        else:
            output += f" {token.text}"
    return output.strip()


def tokens_to_utterances(
    tokens: list[TranscriptToken],
    *,
    offset: float = 0.0,
) -> list[dict[str, float | str]]:
    """Build complete practice utterances without provider/length hard splits."""
    utterances: list[dict[str, float | str]] = []
    current: list[TranscriptToken] = []

    def flush() -> None:
        if not current:
            return
        text = clean_transcript_text(render_tokens(current))
        if text and any(character.isalnum() for character in text):
            utterances.append({
                "text": text,
                "start": round(max(0.0, current[0].start - offset), 2),
                "end": round(max(0.0, current[-1].end - offset), 2),
            })
        current.clear()

    for index, token in enumerate(tokens):
        if current:
            gap = max(0.0, token.start - current[-1].end)
            speaker_changed = bool(
                current[-1].speaker
                and token.speaker
                and current[-1].speaker != token.speaker
            )
            if speaker_changed or gap >= STRONG_PAUSE_SECONDS or (
                gap >= CLAUSE_PAUSE_SECONDS and _CLAUSE_END.search(current[-1].text)
            ):
                flush()
        current.append(token)
        if _STRONG_END.search(token.text):
            following = next(
                (value for value in tokens[index + 1:] if any(character.isalnum() for character in value.text)),
                None,
            )
            if following is None:
                flush()
            else:
                punctuation_gap = max(0.0, following.start - token.end)
                explicit_speaker_change = bool(
                    token.speaker
                    and following.speaker
                    and token.speaker != following.speaker
                )
                completed_phrase_evidence = bool(
                    following.text[:1] and following.text[:1].isupper()
                )
                if (
                    punctuation_gap >= PUNCTUATION_PAUSE_SECONDS
                    or explicit_speaker_change
                    or completed_phrase_evidence
                ):
                    flush()
    flush()
    return utterances


def whisper_words_to_utterances(segments, *, offset: float = 0.0) -> list[dict]:
    tokens: list[TranscriptToken] = []
    for segment in segments:
        words = list(segment.words or [])
        if words:
            for word in words:
                parts = _TOKEN.findall(clean_transcript_text(str(word.word)))
                for part in parts:
                    tokens.append(TranscriptToken(
                        part,
                        max(0.0, float(word.start)),
                        max(float(word.start), float(word.end)),
                        timing_source="acoustic",
                    ))
        else:
            tokens.extend(timed_text_tokens(
                str(segment.text),
                max(0.0, float(segment.start)),
                max(float(segment.start), float(segment.end)),
                timing_source="estimated",
            ))
    return tokens_to_utterances(tokens, offset=offset)
