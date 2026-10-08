import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL

from services.schemas.media_discovery import (
    LearningSuitabilityScore,
    MediaAvailability,
    MediaCandidate,
    MediaDiscoveryRequest,
    SubtitleTrackSummary,
)


MAX_PROVIDER_RESULTS = 25
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class VideoSearchProviderError(RuntimeError):
    """A sanitized failure raised by a video search provider."""


@dataclass(slots=True)
class VideoSearchResult:
    provider: str
    provider_id: str
    url: str
    title: str
    channel: str | None
    duration_seconds: float | None
    thumbnail: str | None
    is_live: bool
    availability: MediaAvailability
    subtitle_tracks: list[SubtitleTrackSummary]


class VideoSearchProvider(Protocol):
    def search(self, query: str, limit: int) -> Sequence[VideoSearchResult]: ...


def _safe_text(value: object, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:maximum]


def _safe_http_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2_048:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        return None
    return value


def _duration(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0 or parsed > 86_400:
        return None
    return parsed


def _availability(entry: dict) -> MediaAvailability:
    raw = _safe_text(entry.get("availability"), 32).casefold()
    if entry.get("is_private") or raw == "private":
        return "private"
    if raw in {
        "unavailable",
        "needs_auth",
        "subscriber_only",
        "premium_only",
    }:
        return "unavailable"
    if raw in {"public", "unlisted"}:
        return "public"
    return "unknown"


def _subtitle_tracks(entry: dict) -> list[SubtitleTrackSummary]:
    by_language: dict[str, dict[str, bool]] = {}
    for field, flag in (
        ("subtitles", "has_human"),
        ("automatic_captions", "has_automatic"),
    ):
        source = entry.get(field)
        if not isinstance(source, dict):
            continue
        for raw_language in source:
            language = _safe_text(raw_language, 32)
            if not language or (
                language not in by_language and len(by_language) >= 100
            ):
                continue
            values = by_language.setdefault(
                language, {"has_human": False, "has_automatic": False}
            )
            values[flag] = True
    return [
        SubtitleTrackSummary(language=language, **by_language[language])
        for language in sorted(by_language, key=str.casefold)
    ]


class YtDlpVideoSearchProvider:
    """YouTube metadata search that never requests media or subtitle downloads."""

    def __init__(self, ydl_factory: Callable[..., YoutubeDL] = YoutubeDL):
        self._ydl_factory = ydl_factory

    def search(self, query: str, limit: int) -> list[VideoSearchResult]:
        printable = "".join(
            character if character.isprintable() else " " for character in query
        )
        normalized_query = " ".join(printable.split())[:200]
        if not normalized_query:
            raise VideoSearchProviderError("YouTube search query is empty")
        bounded_limit = max(1, min(int(limit), MAX_PROVIDER_RESULTS))
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "playlistend": bounded_limit,
            "socket_timeout": 15,
            "retries": 2,
            "ignoreerrors": True,
            "usenetrc": False,
        }
        try:
            with self._ydl_factory(options) as downloader:
                info = downloader.extract_info(
                    f"ytsearch{bounded_limit}:{normalized_query}", download=False
                )
        except Exception as exc:
            raise VideoSearchProviderError("YouTube metadata search failed") from exc

        entries = info.get("entries", []) if isinstance(info, dict) else []
        if not isinstance(entries, (list, tuple)):
            entries = []
        results = []
        for entry in entries[:bounded_limit]:
            if not isinstance(entry, dict):
                continue
            provider_id = _safe_text(entry.get("id"), 64)
            if not YOUTUBE_ID.fullmatch(provider_id):
                continue
            title = _safe_text(entry.get("title"), 300) or "Untitled video"
            channel = _safe_text(
                entry.get("channel") or entry.get("uploader"), 200
            ) or None
            live_status = _safe_text(entry.get("live_status"), 32).casefold()
            results.append(
                VideoSearchResult(
                    provider="youtube",
                    provider_id=provider_id,
                    url=f"https://www.youtube.com/watch?v={provider_id}",
                    title=title,
                    channel=channel,
                    duration_seconds=_duration(entry.get("duration")),
                    thumbnail=_safe_http_url(entry.get("thumbnail")),
                    is_live=bool(entry.get("is_live"))
                    or live_status in {"is_live", "is_upcoming"},
                    availability=_availability(entry),
                    subtitle_tracks=_subtitle_tracks(entry),
                )
            )
        return results


def _language_matches(actual: str, target: str) -> bool:
    normalized = actual.replace("_", "-").casefold()
    target = target.casefold()
    return normalized == target or normalized.startswith(f"{target}-")


def _caption_kind(
    tracks: Sequence[SubtitleTrackSummary], target_language: str
) -> str | None:
    matching = [
        track
        for track in tracks
        if _language_matches(track.language, target_language)
    ]
    if any(track.has_human for track in matching):
        return "human"
    if any(track.has_automatic for track in matching):
        return "automatic"
    return None


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"\w+", value.casefold()) if term}


def _score(
    result: VideoSearchResult,
    request: MediaDiscoveryRequest,
    caption_kind: str,
) -> LearningSuitabilityScore:
    caption_score = 70 if caption_kind == "human" else 35
    caption_reason = (
        f"Human subtitles are available for target language "
        f"'{request.target_language}'."
        if caption_kind == "human"
        else f"Automatic captions are available for target language "
        f"'{request.target_language}'."
    )

    query_terms = _terms(request.query)
    metadata_terms = _terms(f"{result.title} {result.channel or ''}")
    matches = len(query_terms & metadata_terms)
    query_score = 20 * matches / len(query_terms) if query_terms else 0
    query_reason = (
        f"Title/channel matches {matches} of {len(query_terms)} query terms."
    )

    duration = result.duration_seconds or 0
    minimum = request.min_duration_seconds
    maximum = request.max_duration_seconds
    if maximum == minimum:
        duration_score = 10.0
    else:
        midpoint = (minimum + maximum) / 2
        half_range = (maximum - minimum) / 2
        duration_score = max(0.0, 10 * (1 - abs(duration - midpoint) / half_range))
    duration_label = (
        int(duration) if float(duration).is_integer() else round(duration, 2)
    )
    duration_reason = (
        f"Duration {duration_label}s is within the requested "
        f"{minimum}-{maximum}s range."
    )

    return LearningSuitabilityScore(
        total=round(caption_score + query_score + duration_score, 2),
        reasons=[caption_reason, query_reason, duration_reason],
    )


class MediaDiscoveryService:
    def __init__(self, provider: VideoSearchProvider):
        self._provider = provider

    def discover(self, request: MediaDiscoveryRequest) -> list[MediaCandidate]:
        search_limit = min(MAX_PROVIDER_RESULTS, max(10, request.limit * 3))
        try:
            results = self._provider.search(request.query, search_limit)
        except VideoSearchProviderError:
            raise
        except Exception as exc:
            raise VideoSearchProviderError("Video search provider failed") from exc

        candidates = []
        for result in results[:MAX_PROVIDER_RESULTS]:
            if (
                result.provider != "youtube"
                or not YOUTUBE_ID.fullmatch(result.provider_id)
            ):
                continue
            if result.is_live or result.availability in {"private", "unavailable"}:
                continue
            duration = result.duration_seconds
            if duration is None or not (
                request.min_duration_seconds
                <= duration
                <= request.max_duration_seconds
            ):
                continue
            caption_kind = _caption_kind(
                result.subtitle_tracks, request.target_language
            )
            if caption_kind is None:
                continue
            candidates.append(
                MediaCandidate(
                    provider="youtube",
                    provider_id=result.provider_id,
                    url=(
                        "https://www.youtube.com/watch?v="
                        f"{result.provider_id}"
                    ),
                    title=result.title,
                    channel=result.channel,
                    duration_seconds=duration,
                    thumbnail=result.thumbnail,
                    is_live=result.is_live,
                    availability=result.availability,
                    subtitle_tracks=result.subtitle_tracks,
                    suitability=_score(result, request, caption_kind),
                )
            )

        candidates.sort(
            key=lambda candidate: (
                -candidate.suitability.total,
                candidate.title.casefold(),
                candidate.provider,
                candidate.provider_id,
            )
        )
        return candidates[: request.limit]
