import re
from pathlib import Path

from services.transcript.rolling_captions import CaptionWindow
from services.transcript.hygiene import clean_transcript_text
from services.transcript.utterances import TranscriptToken, timed_text_tokens


_TIMESTAMP = re.compile(r"(\d+:\d+:\d+[.,]\d+)\s*-->\s*(\d+:\d+:\d+[.,]\d+)")
_INLINE_TIMESTAMP = re.compile(r"<(\d+:\d+:\d+[.,]\d+)>")
_VOICE = re.compile(r"<v(?:\.\w+)*\s+([^>]+)>", re.IGNORECASE)


def get_subtitle_languages(source_language: str) -> list[str]:
    if source_language == "en":
        return ["en", "en-US", "en-GB", "en-orig"]
    if source_language == "de":
        return ["de", "de-DE", "de-orig"]
    raise ValueError(f"Unsupported source language: {source_language}")


def _vtt_to_sec(value: str) -> float:
    value = value.replace(",", ".")
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _inline_tokens(
    value: str,
    start: float,
    end: float,
    speaker: str | None,
) -> list[TranscriptToken]:
    """Preserve WebVTT inline timestamps as token timing boundaries."""
    value = re.sub(r"<(?!\d+:\d+:\d+[.,]\d+>)[^>]+>", "", value)
    tokens: list[TranscriptToken] = []
    cursor = start
    position = 0
    for marker in _INLINE_TIMESTAMP.finditer(value):
        boundary = min(end, max(start, _vtt_to_sec(marker.group(1))))
        tokens.extend(
            timed_text_tokens(
                value[position:marker.start()], cursor, boundary, speaker=speaker
            )
        )
        cursor = boundary
        position = marker.end()
    tokens.extend(timed_text_tokens(value[position:], cursor, end, speaker=speaker))
    return tokens


def parse_vtt_windows(path: str | Path) -> list[CaptionWindow]:
    """Parse caption windows while retaining provider word timing when present."""
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    windows: list[CaptionWindow] = []
    for block in re.split(r"\r?\n(?:\r?\n)+", text):
        match = None
        tagged, plain = [], []
        for line in block.splitlines():
            hit = _TIMESTAMP.search(line)
            if hit:
                match = hit
            elif (
                line.strip()
                and "WEBVTT" not in line
                and not line.strip().isdigit()
                and not line.startswith(("Kind:", "Language:", "NOTE"))
            ):
                has_tag = bool(_INLINE_TIMESTAMP.search(line))
                clean = re.sub(r"<[^>]+>", "", line).strip()
                if clean:
                    (tagged if has_tag else plain).append(line.strip())
        lines = tagged if tagged else plain
        if match and lines:
            start = _vtt_to_sec(match.group(1))
            end = _vtt_to_sec(match.group(2))
            raw = " ".join(lines)
            voice = _VOICE.search(raw)
            speaker = voice.group(1).strip() if voice else None
            tokens = tuple(_inline_tokens(raw, start, end, speaker))
            windows.append(
                CaptionWindow(
                    start=start,
                    end=end,
                    text=clean_transcript_text(re.sub(r"<[^>]+>", "", raw)),
                    tokens=tokens,
                    speaker=speaker,
                )
            )
    return windows


def parse_vtt(path: str | Path) -> list[tuple[float, float, str]]:
    """Return the legacy cue tuple API for subtitle enrichment callers."""
    return [(window.start, window.end, window.text) for window in parse_vtt_windows(path)]
