import re
from dataclasses import replace
from pathlib import Path

from services.transcript.rolling_captions import (
    CaptionWindow,
    caption_window_text,
    reconcile_caption_lines,
)
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
    window_id: int,
    line_id: int,
) -> list[TranscriptToken]:
    """Preserve WebVTT inline timestamps as token timing boundaries."""
    value = re.sub(r"<(?!\d+:\d+:\d+[.,]\d+>)[^>]+>", "", value)
    tokens: list[TranscriptToken] = []
    cursor = start
    position = 0
    timing_source = "estimated"

    def append_chunk(raw: str, chunk_start: float, chunk_end: float) -> None:
        chunk = timed_text_tokens(
            raw,
            chunk_start,
            chunk_end,
            speaker=speaker,
            timing_source=timing_source,
            source_window=window_id,
            source_line=line_id,
        )
        if timing_source == "source_inline":
            found_anchor = False
            adjusted = []
            for token in chunk:
                lexical = any(character.isalnum() for character in token.text)
                if lexical and not found_anchor:
                    found_anchor = True
                    adjusted.append(token)
                else:
                    adjusted.append(replace(token, timing_source="estimated"))
            chunk = adjusted
        tokens.extend(chunk)

    for marker in _INLINE_TIMESTAMP.finditer(value):
        boundary = min(end, max(start, _vtt_to_sec(marker.group(1))))
        append_chunk(value[position:marker.start()], cursor, boundary)
        cursor = boundary
        position = marker.end()
        timing_source = "source_inline"
    append_chunk(value[position:], cursor, end)
    return tokens


def parse_vtt_windows(path: str | Path) -> list[CaptionWindow]:
    """Parse caption windows while retaining provider word timing when present."""
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    windows: list[CaptionWindow] = []
    for window_id, block in enumerate(re.split(r"\r?\n(?:\r?\n)+", text)):
        block_lines = block.splitlines()
        timestamp_entry = next(
            (
                (index, found)
                for index, line in enumerate(block_lines)
                if (found := _TIMESTAMP.search(line))
            ),
            None,
        )
        if timestamp_entry is None:
            continue
        timestamp_index, match = timestamp_entry
        content_lines: list[str] = []
        for line in block_lines[timestamp_index + 1:]:
            if (
                line.strip()
                and "WEBVTT" not in line
                and not line.strip().isdigit()
                and not line.startswith(("Kind:", "Language:", "NOTE"))
            ):
                clean = re.sub(r"<[^>]+>", "", line).strip()
                if clean:
                    content_lines.append(line.strip())
        if content_lines:
            start = _vtt_to_sec(match.group(1))
            end = _vtt_to_sec(match.group(2))
            parsed_lines: list[list[TranscriptToken]] = []
            speakers: list[str | None] = []
            line_anchors = [
                _vtt_to_sec(found.group(1)) if (found := _INLINE_TIMESTAMP.search(raw)) else None
                for raw in content_lines
            ]
            for line_id, raw in enumerate(content_lines):
                voice = _VOICE.search(raw)
                speaker = voice.group(1).strip() if voice else None
                speakers.append(speaker)
                anchor = line_anchors[line_id]
                later_anchors = [
                    value for value in line_anchors[line_id + 1:]
                    if value is not None and (anchor is None or value > anchor + 0.001)
                ]
                line_end = min([end, *later_anchors])
                parsed_lines.append(
                    _inline_tokens(raw, start, line_end, speaker, window_id, line_id)
                )
            merged, line_decisions, quality_flags = reconcile_caption_lines(
                parsed_lines, window_id=window_id
            )
            tokens = tuple(merged)
            windows.append(
                CaptionWindow(
                    start=start,
                    end=end,
                    text=clean_transcript_text(caption_window_text(merged)),
                    tokens=tokens,
                    speaker=next((value for value in speakers if value), None),
                    window_id=window_id,
                    quality_flags=quality_flags,
                    line_decisions=line_decisions,
                )
            )
    return windows


def parse_vtt(path: str | Path) -> list[tuple[float, float, str]]:
    """Return the legacy cue tuple API for subtitle enrichment callers."""
    return [(window.start, window.end, window.text) for window in parse_vtt_windows(path)]
