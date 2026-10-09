import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.api import media_discovery_api
from services.media.discovery import (
    MediaDiscoveryService,
    VideoSearchProviderError,
    VideoSearchResult,
    YtDlpVideoSearchProvider,
)
from services.schemas.media_discovery import (
    MediaDiscoveryRequest,
    SubtitleTrackSummary,
)
from services.schemas.requests import LessonFromUrlRequest


client = TestClient(app_module.app)


class FakeProvider:
    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    def search(self, query, limit):
        self.calls.append((query, limit))
        if self.error:
            raise self.error
        return self.results


def result(
    provider_id,
    *,
    title="English lesson",
    duration=600,
    human=False,
    automatic=False,
    is_live=False,
    availability="public",
):
    tracks = []
    if human or automatic:
        tracks.append(
            SubtitleTrackSummary(
                language="en", has_human=human, has_automatic=automatic
            )
        )
    return VideoSearchResult(
        provider="youtube",
        provider_id=provider_id,
        url=f"https://www.youtube.com/watch?v={provider_id}",
        title=title,
        channel="Teacher",
        duration_seconds=duration,
        thumbnail="https://i.ytimg.com/example.jpg",
        is_live=is_live,
        availability=availability,
        subtitle_tracks=tracks,
    )


def request(**overrides):
    values = {
        "query": "English lesson",
        "target_language": "en",
        "min_duration_seconds": 60,
        "max_duration_seconds": 1200,
        "limit": 5,
    }
    values.update(overrides)
    return MediaDiscoveryRequest(**values)


def test_human_subtitles_always_rank_above_automatic_captions():
    provider = FakeProvider(
        [
            result("auto", automatic=True),
            result("human", human=True),
        ]
    )

    candidates = MediaDiscoveryService(provider).discover(request())

    assert [candidate.provider_id for candidate in candidates] == ["human", "auto"]
    assert candidates[0].suitability.total > candidates[1].suitability.total


def test_missing_target_language_subtitles_are_filtered():
    no_english = result("de", human=False)
    no_english.subtitle_tracks = [
        SubtitleTrackSummary(language="de", has_human=True, has_automatic=False)
    ]

    candidates = MediaDiscoveryService(FakeProvider([no_english])).discover(request())

    assert candidates == []


def test_live_private_and_unavailable_candidates_are_excluded():
    provider = FakeProvider(
        [
            result("live", human=True, is_live=True),
            result("private", human=True, availability="private"),
            result("gone", human=True, availability="unavailable"),
            result("public", human=True),
        ]
    )

    candidates = MediaDiscoveryService(provider).discover(request())

    assert [candidate.provider_id for candidate in candidates] == ["public"]


def test_duration_bounds_are_inclusive_and_filter_outside_values():
    provider = FakeProvider(
        [
            result("too-short", duration=59, human=True),
            result("minimum", duration=60, human=True),
            result("maximum", duration=1200, human=True),
            result("too-long", duration=1201, human=True),
        ]
    )

    candidates = MediaDiscoveryService(provider).discover(request())

    assert {candidate.provider_id for candidate in candidates} == {"minimum", "maximum"}


def test_equal_scores_have_stable_order_and_respect_limit():
    provider = FakeProvider(
        [
            result("z-id", title="Same lesson", duration=630, human=True),
            result("a-id", title="Same lesson", duration=630, human=True),
            result("m-id", title="Same lesson", duration=630, human=True),
        ]
    )

    candidates = MediaDiscoveryService(provider).discover(request(limit=2))

    assert [candidate.provider_id for candidate in candidates] == ["a-id", "m-id"]
    assert provider.calls == [("English lesson", 10)]


def test_ranking_reasons_are_deterministic_and_explain_components():
    provider = FakeProvider([result("explain", duration=630, human=True)])
    service = MediaDiscoveryService(provider)

    first = service.discover(request())[0].suitability
    second = service.discover(request())[0].suitability

    assert first == second
    assert first.reasons == [
        "Human English subtitles available.",
        "Title or channel closely matches your search topic.",
        "Duration is near the center of your requested 1–20 minute range.",
        "For this search, general practice uses your requested duration range.",
    ]


def test_yt_dlp_provider_uses_metadata_only_without_cookies_or_downloads():
    observed = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            observed["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def extract_info(self, target, download):
            observed["target"] = target
            observed["download"] = download
            return {
                "entries": [
                    {
                        "id": "abc_123",
                        "title": "A" * 400,
                        "channel": "Teacher",
                        "duration": 300,
                        "thumbnail": "https://i.ytimg.com/thumb.jpg",
                        "availability": "public",
                        "subtitles": {"en-US": [{"ext": "vtt"}]},
                        "automatic_captions": {"de": [{"ext": "vtt"}]},
                    }
                ]
            }

    provider = YtDlpVideoSearchProvider(ydl_factory=FakeYoutubeDL)
    results = provider.search("  safe\n search  ", 7)

    assert observed["target"] == "ytsearch7:safe search"
    assert observed["download"] is False
    assert observed["options"]["skip_download"] is True
    assert observed["options"]["playlistend"] == 7
    assert "format" not in observed["options"]
    assert "outtmpl" not in observed["options"]
    assert "postprocessors" not in observed["options"]
    assert "cookiefile" not in observed["options"]
    assert "cookiesfrombrowser" not in observed["options"]
    assert observed["options"]["usenetrc"] is False
    assert results[0].url == "https://www.youtube.com/watch?v=abc_123"
    assert len(results[0].title) == 300
    assert [track.model_dump() for track in results[0].subtitle_tracks] == [
        {"language": "de", "has_human": False, "has_automatic": True},
        {"language": "en-US", "has_human": True, "has_automatic": False},
    ]


def test_yt_dlp_provider_safely_bounds_and_sanitizes_malformed_metadata():
    class FakeYoutubeDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def extract_info(self, target, download):
            return {
                "entries": [
                    {"id": "invalid/id", "duration": 300},
                    {
                        "id": "valid-id",
                        "title": ["not", "text"],
                        "duration": "NaN",
                        "thumbnail": "file:///etc/passwd",
                        "subtitles": ["not", "a", "mapping"],
                    },
                ]
            }

    results = YtDlpVideoSearchProvider(ydl_factory=FakeYoutubeDL).search(
        "safe search", 2
    )

    assert len(results) == 1
    assert results[0].provider_id == "valid-id"
    assert results[0].title == "Untitled video"
    assert results[0].duration_seconds is None
    assert results[0].thumbnail is None
    assert results[0].subtitle_tracks == []


@pytest.mark.parametrize("query", ["", "   ", "\0", "x" * 201])
def test_request_rejects_empty_or_excessive_search_input(query):
    response = client.post("/api/media/discover", json={"query": query})

    assert response.status_code == 422


def test_request_rejects_excessive_result_count_and_invalid_duration_range():
    too_many = client.post(
        "/api/media/discover", json={"query": "English", "limit": 11}
    )
    invalid_range = client.post(
        "/api/media/discover",
        json={
            "query": "English",
            "min_duration_seconds": 600,
            "max_duration_seconds": 300,
        },
    )

    assert too_many.status_code == 422
    assert invalid_range.status_code == 422


def test_service_normalizes_provider_url_and_rejects_invalid_provider_ids():
    malicious = result("safe-id", human=True)
    malicious.url = "file:///etc/passwd"
    invalid_id = result("invalid/id", human=True)

    candidates = MediaDiscoveryService(
        FakeProvider([malicious, invalid_id])
    ).discover(request())

    assert [str(candidate.url) for candidate in candidates] == [
        "https://www.youtube.com/watch?v=safe-id"
    ]


def test_api_returns_urls_accepted_by_existing_lesson_contract(monkeypatch):
    service = MediaDiscoveryService(FakeProvider([result("lesson", human=True)]))
    monkeypatch.setattr(media_discovery_api, "discovery_service", service)

    response = client.post("/api/media/discover", json={"query": "English lesson"})

    assert response.status_code == 200
    candidate = response.json()[0]
    assert set(candidate["suitability"]) == {"total", "breakdown", "reasons"}
    assert isinstance(candidate["suitability"]["total"], (int, float))
    assert isinstance(candidate["suitability"]["reasons"], list)
    lesson_input = LessonFromUrlRequest(
        url=candidate["url"], source_language="en"
    )
    assert str(lesson_input.url) == "https://www.youtube.com/watch?v=lesson"


def test_provider_errors_become_controlled_api_errors(monkeypatch):
    service = MediaDiscoveryService(
        FakeProvider(error=RuntimeError("secret upstream diagnostic"))
    )
    monkeypatch.setattr(media_discovery_api, "discovery_service", service)

    response = client.post("/api/media/discover", json={"query": "English lesson"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Media discovery provider failed"}
    assert "secret" not in response.text


def test_declared_provider_errors_are_also_sanitized(monkeypatch):
    service = MediaDiscoveryService(
        FakeProvider(error=VideoSearchProviderError("provider internals"))
    )
    monkeypatch.setattr(media_discovery_api, "discovery_service", service)

    response = client.post("/api/media/discover", json={"query": "English lesson"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Media discovery provider failed"}
