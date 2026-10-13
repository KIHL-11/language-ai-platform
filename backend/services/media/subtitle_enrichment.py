import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from services.media.subtitles import get_subtitle_languages, parse_vtt
from services.schemas.media_discovery import (
    MediaCandidate,
    SubtitleEnrichmentStatus,
    SubtitleKind,
    SubtitleTrackSummary,
)


MAX_SUBTITLE_FILE_BYTES = 2 * 1024 * 1024
MAX_WORKSPACE_FILES = 10
MAX_SUBTITLE_CUES = 5_000
MAX_TRANSCRIPT_CHARACTERS = 500_000
SUBTITLE_SOCKET_TIMEOUT_SECONDS = 10
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SUBTITLE_LANGUAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


@dataclass(frozen=True, slots=True)
class NormalizedTranscriptCue:
    start: float
    end: float
    text: str

    def as_mapping(self) -> dict[str, float | str]:
        return {"start": self.start, "end": self.end, "text": self.text}


@dataclass(frozen=True, slots=True)
class SubtitleEnrichmentResult:
    status: SubtitleEnrichmentStatus
    subtitle_kind: SubtitleKind | None = None
    cues: tuple[NormalizedTranscriptCue, ...] = ()


class SubtitleEnrichmentProvider(Protocol):
    def enrich(
        self, candidate: MediaCandidate, target_language: str
    ) -> SubtitleEnrichmentResult: ...


def _language_matches(actual: str, target: str) -> bool:
    normalized = actual.replace("_", "-").casefold()
    target = target.casefold()
    return normalized == target or normalized.startswith(f"{target}-")


def _choose_track(
    tracks: list[SubtitleTrackSummary], target_language: str
) -> tuple[SubtitleTrackSummary, SubtitleKind] | None:
    priorities = {
        value.casefold(): index
        for index, value in enumerate(get_subtitle_languages(target_language))
    }
    matching = [
        track
        for track in tracks
        if SUBTITLE_LANGUAGE.fullmatch(track.language)
        and _language_matches(track.language, target_language)
    ]
    for kind, attribute in (("human", "has_human"), ("automatic", "has_automatic")):
        available = [track for track in matching if getattr(track, attribute)]
        if available:
            selected = min(
                available,
                key=lambda track: (
                    priorities.get(track.language.casefold(), len(priorities)),
                    track.language.casefold(),
                ),
            )
            return selected, kind
    return None


def _canonical_youtube_url(candidate: MediaCandidate) -> str | None:
    if candidate.provider != "youtube" or not YOUTUBE_ID.fullmatch(
        candidate.provider_id
    ):
        return None
    value = str(candidate.url)
    try:
        parsed = urlsplit(value)
        query = parse_qs(parsed.query, keep_blank_values=True)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.youtube.com"
        or port is not None
        or parsed.username
        or parsed.password
        or parsed.path != "/watch"
        or parsed.fragment
        or set(query) != {"v"}
        or query["v"] != [candidate.provider_id]
    ):
        return None
    return value


def _is_rate_limited(error: DownloadError) -> bool:
    message = str(error).casefold()
    return bool(
        re.search(r"(?:^|\D)429(?:\D|$)", message)
        or "too many requests" in message
        or "rate limit" in message
    )


class YtDlpSubtitleEnrichmentProvider:
    """Retrieve one known YouTube subtitle track without downloading media."""

    def __init__(self, ydl_factory: Callable[..., YoutubeDL] = YoutubeDL):
        self._ydl_factory = ydl_factory

    def enrich(
        self, candidate: MediaCandidate, target_language: str
    ) -> SubtitleEnrichmentResult:
        url = _canonical_youtube_url(candidate)
        if url is None:
            return SubtitleEnrichmentResult(status="failed")
        selected = _choose_track(candidate.subtitle_tracks, target_language)
        if selected is None:
            return SubtitleEnrichmentResult(status="unavailable")
        track, subtitle_kind = selected

        with tempfile.TemporaryDirectory(prefix="subtitle-enrichment-") as workspace:
            root = Path(workspace)
            options = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "outtmpl": str(root / "subtitle.%(ext)s"),
                "skip_download": True,
                "writesubtitles": subtitle_kind == "human",
                "writeautomaticsub": subtitle_kind == "automatic",
                "subtitleslangs": [track.language],
                "subtitlesformat": "vtt",
                "socket_timeout": SUBTITLE_SOCKET_TIMEOUT_SECONDS,
                "retries": 0,
                "fragment_retries": 0,
                "extractor_retries": 0,
                "file_access_retries": 0,
                "max_filesize": MAX_SUBTITLE_FILE_BYTES,
                "usenetrc": False,
                "cachedir": False,
                "ignoreconfig": True,
            }
            try:
                with self._ydl_factory(options) as downloader:
                    downloader.extract_info(url, download=True)
            except DownloadError as exc:
                status: SubtitleEnrichmentStatus = (
                    "rate_limited" if _is_rate_limited(exc) else "failed"
                )
                return SubtitleEnrichmentResult(status=status)
            except Exception:
                return SubtitleEnrichmentResult(status="failed")

            try:
                root_resolved = root.resolve()
                paths = list(root.rglob("*"))
                if any(path.is_symlink() for path in paths):
                    return SubtitleEnrichmentResult(status="failed")
                files = [path for path in paths if path.is_file()]
                if len(files) > MAX_WORKSPACE_FILES:
                    return SubtitleEnrichmentResult(status="failed")
                sizes = []
                for path in files:
                    path.resolve().relative_to(root_resolved)
                    sizes.append(path.stat().st_size)
                if any(size > MAX_SUBTITLE_FILE_BYTES for size in sizes) or sum(
                    sizes
                ) > MAX_SUBTITLE_FILE_BYTES:
                    return SubtitleEnrichmentResult(status="failed")
                subtitle_files = [
                    path for path in files if path.suffix.casefold() == ".vtt"
                ]
                if len(subtitle_files) != 1:
                    return SubtitleEnrichmentResult(status="failed")
                parsed = parse_vtt(subtitle_files[0])
                if not parsed or len(parsed) > MAX_SUBTITLE_CUES:
                    return SubtitleEnrichmentResult(status="failed")
                if sum(len(text) for _, _, text in parsed) > MAX_TRANSCRIPT_CHARACTERS:
                    return SubtitleEnrichmentResult(status="failed")
                cues = tuple(
                    NormalizedTranscriptCue(start, end, text)
                    for start, end, text in parsed
                )
                return SubtitleEnrichmentResult(
                    status="success",
                    subtitle_kind=subtitle_kind,
                    cues=cues,
                )
            except Exception:
                return SubtitleEnrichmentResult(status="failed")
