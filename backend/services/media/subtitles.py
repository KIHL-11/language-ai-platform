import re
from pathlib import Path


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


def parse_vtt(path: str | Path) -> list[tuple[float, float, str]]:
    """Parse the existing supported VTT subset without network side effects."""

    inline_ts = re.compile(r"<\d+:\d+:\d+[.,]\d+>")
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    timestamp = re.compile(
        r"(\d+:\d+:\d+[.,]\d+)\s*-->\s*(\d+:\d+:\d+[.,]\d+)"
    )
    cues = []
    for block in re.split(r"\n\n+", text):
        match = None
        tagged, plain = [], []
        for line in block.splitlines():
            hit = timestamp.search(line)
            if hit:
                match = hit
            elif (
                line.strip()
                and "WEBVTT" not in line
                and not line.strip().isdigit()
                and not line.startswith(("Kind:", "Language:", "NOTE"))
            ):
                has_tag = bool(inline_ts.search(line))
                clean = re.sub(r"<[^>]+>", "", line).strip()
                if clean:
                    (tagged if has_tag else plain).append(clean)
        lines = tagged if tagged else plain
        if match and lines:
            cues.append(
                (
                    _vtt_to_sec(match.group(1)),
                    _vtt_to_sec(match.group(2)),
                    " ".join(lines),
                )
            )
    return cues
