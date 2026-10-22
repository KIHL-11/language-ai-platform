import re
import unicodedata
from dataclasses import dataclass

from services.transcript.hygiene import clean_transcript_text


STRONG_PAUSE_SECONDS = 0.9
CLAUSE_PAUSE_SECONDS = 0.5
_TOKEN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*|\d+(?:[.,]\d+)*|[.!?…]+|[,;:]+|[^\w\s]", re.UNICODE)
_STRONG_END = re.compile(r"[.!?…]+$")
_CLAUSE_END = re.compile(r"[,;:]+$")


@dataclass(frozen=True, slots=True)
class TranscriptToken:
    text: str
    start: float
    end: float
    speaker: str | None = None

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
) -> list[TranscriptToken]:
    cleaned = clean_transcript_text(text)
    values = _TOKEN.findall(cleaned)
    if not values:
        return []
    duration = max(0.0, end - start)
    width = duration / len(values)
    return [
        TranscriptToken(
            text=value,
            start=start + index * width,
            end=end if index == len(values) - 1 else start + (index + 1) * width,
            speaker=speaker,
        )
        for index, value in enumerate(values)
    ]


def _render(tokens: list[TranscriptToken]) -> str:
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
        text = clean_transcript_text(_render(current))
        if text:
            utterances.append({
                "text": text,
                "start": round(max(0.0, current[0].start - offset), 2),
                "end": round(max(0.0, current[-1].end - offset), 2),
            })
        current.clear()

    for token in tokens:
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
                    ))
        else:
            tokens.extend(timed_text_tokens(
                str(segment.text),
                max(0.0, float(segment.start)),
                max(float(segment.start), float(segment.end)),
            ))
    return tokens_to_utterances(tokens, offset=offset)
