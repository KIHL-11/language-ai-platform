from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from yt_dlp.utils import DownloadError

import app as app_module
from services.api import media_discovery_api
from services.media.discovery import (
    MediaDiscoveryService,
    VideoSearchResult,
)
from services.media.subtitle_enrichment import (
    MAX_SUBTITLE_CUES,
    MAX_SUBTITLE_FILE_BYTES,
    MAX_TRANSCRIPT_CHARACTERS,
    NormalizedTranscriptCue,
    SubtitleEnrichmentResult,
    YtDlpSubtitleEnrichmentProvider,
)
from services.schemas.media_discovery import (
    MediaDiscoveryRequest,
    SubtitleTrackSummary,
)


client = TestClient(app_module.app)


class FakeSearchProvider:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, limit):
        self.calls.append((query, limit))
        return self.results


class FakeEnrichmentProvider:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def enrich(self, candidate, target_language):
        self.calls.append((candidate.provider_id, target_language))
        response = self.responses.get(candidate.provider_id)
        if isinstance(response, Exception):
            raise response
        return response or SubtitleEnrichmentResult(status="unavailable")


def search_result(
    provider_id,
    *,
    language="en",
    human=True,
    automatic=False,
):
    return VideoSearchResult(
        provider="youtube",
        provider_id=provider_id,
        url=f"https://www.youtube.com/watch?v={provider_id}",
        title="Language lesson",
        channel="Teacher",
        duration_seconds=600,
        thumbnail=None,
        is_live=False,
        availability="public",
        subtitle_tracks=[
            SubtitleTrackSummary(
                language=language,
                has_human=human,
                has_automatic=automatic,
            )
        ],
    )


def discovery_request(**overrides):
    values = {
        "query": "Language lesson",
        "target_language": "en",
        "min_duration_seconds": 60,
        "max_duration_seconds": 1200,
        "limit": 5,
    }
    values.update(overrides)
    return MediaDiscoveryRequest(**values)


def success_result(text="one two three four", kind="human"):
    return SubtitleEnrichmentResult(
        status="success",
        subtitle_kind=kind,
        cues=(NormalizedTranscriptCue(0, 2, text),),
    )


def candidate(*, language="en", human=True, automatic=False):
    return MediaDiscoveryService(
        FakeSearchProvider(
            [
                search_result(
                    "candidate",
                    language=language,
                    human=human,
                    automatic=automatic,
                )
            ]
        )
    ).discover(discovery_request(target_language=language.split("-")[0]))[0]


def vtt(text="Hello from subtitles."):
    return f"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n{text}\n"


def ydl_factory(*, content=None, error=None, observed=None, barrier=None):
    observed = observed if observed is not None else []

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def extract_info(self, url, download):
            workspace = Path(self.options["outtmpl"]).parent
            observed.append(
                {
                    "options": self.options.copy(),
                    "url": url,
                    "download": download,
                    "workspace": workspace,
                }
            )
            if content is not None:
                output = workspace / "subtitle.en.vtt"
                if isinstance(content, bytes):
                    output.write_bytes(content)
                else:
                    output.write_text(content, encoding="utf-8")
            if barrier is not None:
                barrier.wait(timeout=5)
            if error is not None:
                (workspace / "subtitle.en.vtt.part").write_text(
                    "partial", encoding="utf-8"
                )
                raise error
            return {"id": "candidate"}

    return FakeYoutubeDL


def test_enrichment_disabled_makes_no_subtitle_calls_and_matches_metadata_only():
    results = [search_result("a"), search_result("b")]
    enricher = FakeEnrichmentProvider()
    service = MediaDiscoveryService(FakeSearchProvider(results), enricher)

    enriched_capable = service.discover(discovery_request())
    metadata_only = MediaDiscoveryService(FakeSearchProvider(results)).discover(
        discovery_request()
    )

    assert enricher.calls == []
    assert enriched_capable == metadata_only
    assert all(item.transcript_enrichment is None for item in enriched_capable)


def test_enrichment_defaults_to_ranked_top_two_and_preserves_order_and_scores():
    results = [search_result(value) for value in ["d", "b", "a", "c"]]
    enricher = FakeEnrichmentProvider(
        {value: success_result() for value in ["a", "b", "c", "d"]}
    )
    baseline = MediaDiscoveryService(FakeSearchProvider(results)).discover(
        discovery_request()
    )

    enriched = MediaDiscoveryService(
        FakeSearchProvider(results), enricher
    ).discover(discovery_request(enrich_transcript=True))

    assert enricher.calls == [("a", "en"), ("b", "en")]
    assert [item.provider_id for item in enriched] == [
        item.provider_id for item in baseline
    ]
    assert [item.suitability for item in enriched] == [
        item.suitability for item in baseline
    ]
    assert enriched[0].transcript_enrichment.status == "success"
    assert enriched[1].transcript_enrichment.status == "success"
    assert all(item.transcript_enrichment is None for item in enriched[2:])


def test_custom_limit_three_is_allowed_but_more_than_three_is_rejected():
    results = [search_result(value) for value in ["a", "b", "c", "d"]]
    enricher = FakeEnrichmentProvider()

    MediaDiscoveryService(FakeSearchProvider(results), enricher).discover(
        discovery_request(enrich_transcript=True, enrichment_limit=3)
    )

    assert [item[0] for item in enricher.calls] == ["a", "b", "c"]
    with pytest.raises(ValidationError):
        discovery_request(enrich_transcript=True, enrichment_limit=4)


def test_generic_enrichment_failure_is_sanitized_and_next_candidate_is_attempted(
    monkeypatch,
):
    enricher = FakeEnrichmentProvider(
        {"a": RuntimeError("private upstream details"), "b": success_result()}
    )

    candidates = MediaDiscoveryService(
        FakeSearchProvider([search_result("a"), search_result("b")]), enricher
    ).discover(discovery_request(enrich_transcript=True))

    assert [item[0] for item in enricher.calls] == ["a", "b"]
    assert candidates[0].transcript_enrichment.model_dump(exclude_none=True) == {
        "status": "failed"
    }
    assert candidates[1].transcript_enrichment.status == "success"
    assert "private upstream" not in str(candidates)

    monkeypatch.setattr(
        media_discovery_api,
        "discovery_service",
        MediaDiscoveryService(
            FakeSearchProvider([search_result("a")]),
            FakeEnrichmentProvider(
                {"a": RuntimeError("private upstream details")}
            ),
        ),
    )
    response = client.post(
        "/api/media/discover",
        json={"query": "Language lesson", "enrich_transcript": True},
    )
    assert response.status_code == 200
    assert response.json()[0]["transcript_enrichment"]["status"] == "failed"
    assert "private upstream" not in response.text


def test_rate_limit_marks_candidate_stops_remaining_calls_and_keeps_api_200(
    monkeypatch,
):
    enricher = FakeEnrichmentProvider(
        {
            "a": SubtitleEnrichmentResult(status="rate_limited"),
            "b": success_result(),
        }
    )
    service = MediaDiscoveryService(
        FakeSearchProvider([search_result("a"), search_result("b")]), enricher
    )
    monkeypatch.setattr(media_discovery_api, "discovery_service", service)

    response = client.post(
        "/api/media/discover",
        json={"query": "Language lesson", "enrich_transcript": True},
    )

    assert response.status_code == 200
    assert [item[0] for item in enricher.calls] == ["a"]
    assert (
        response.json()[0]["transcript_enrichment"]["status"]
        == "rate_limited"
    )
    assert response.json()[1]["transcript_enrichment"] is None


def test_unavailable_subtitles_preserve_candidate_and_metadata_score():
    result = search_result("a")
    baseline = MediaDiscoveryService(FakeSearchProvider([result])).discover(
        discovery_request()
    )[0]
    enriched = MediaDiscoveryService(
        FakeSearchProvider([result]), FakeEnrichmentProvider()
    ).discover(discovery_request(enrich_transcript=True))[0]

    assert enriched.provider_id == "a"
    assert enriched.suitability == baseline.suitability
    assert enriched.transcript_enrichment.status == "unavailable"


@pytest.mark.parametrize(
    ("human", "automatic", "expected_kind", "write_human", "write_auto"),
    [
        (True, True, "human", True, False),
        (False, True, "automatic", False, True),
    ],
)
def test_provider_prefers_human_then_falls_back_to_automatic_without_media(
    human, automatic, expected_kind, write_human, write_auto
):
    observed = []
    provider = YtDlpSubtitleEnrichmentProvider(
        ydl_factory=ydl_factory(content=vtt(), observed=observed)
    )

    result = provider.enrich(
        candidate(human=human, automatic=automatic), "en"
    )

    assert result.status == "success"
    assert result.subtitle_kind == expected_kind
    assert len(result.cues) == 1
    call = observed[0]
    options = call["options"]
    assert call["download"] is True
    assert options["skip_download"] is True
    assert options["writesubtitles"] is write_human
    assert options["writeautomaticsub"] is write_auto
    assert options["retries"] == 0
    assert options["fragment_retries"] == 0
    assert options["usenetrc"] is False
    for forbidden in (
        "format",
        "postprocessors",
        "cookiefile",
        "cookiesfrombrowser",
    ):
        assert forbidden not in options
    assert not call["workspace"].exists()


@pytest.mark.parametrize(
    ("language", "text", "expected_words"),
    [
        ("en", "Hello from the English subtitle track.", 6),
        ("de", "Schöne Grüße aus München.", 4),
    ],
)
def test_successful_vtt_generates_transcript_features(
    language, text, expected_words
):
    provider = YtDlpSubtitleEnrichmentProvider(
        ydl_factory=ydl_factory(content=vtt(text))
    )
    result = search_result("candidate", language=language)
    service = MediaDiscoveryService(
        FakeSearchProvider([result]), provider
    )

    enriched = service.discover(
        discovery_request(target_language=language, enrich_transcript=True)
    )[0]

    assert enriched.transcript_enrichment.status == "success"
    assert enriched.transcript_enrichment.subtitle_kind == "human"
    assert enriched.transcript_enrichment.features.word_count == expected_words


def test_no_matching_track_returns_unavailable_without_yt_dlp_call():
    observed = []
    provider = YtDlpSubtitleEnrichmentProvider(
        ydl_factory=ydl_factory(content=vtt(), observed=observed)
    )
    item = candidate()
    item.subtitle_tracks = [
        SubtitleTrackSummary(
            language="de", has_human=True, has_automatic=False
        )
    ]

    result = provider.enrich(item, "en")

    assert result.status == "unavailable"
    assert observed == []


def test_unsafe_subtitle_language_is_rejected_before_creating_files():
    observed = []
    provider = YtDlpSubtitleEnrichmentProvider(
        ydl_factory=ydl_factory(content=vtt(), observed=observed)
    )
    item = candidate()
    item.subtitle_tracks = [
        SubtitleTrackSummary(
            language="en-../../private",
            has_human=True,
            has_automatic=False,
        )
    ]

    result = provider.enrich(item, "en")

    assert result.status == "unavailable"
    assert observed == []


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (DownloadError("HTTP Error 429: Too Many Requests"), "rate_limited"),
        (DownloadError("subtitle download failed: internal detail"), "failed"),
    ],
)
def test_download_errors_are_classified_without_leaking_details(
    error, expected_status
):
    observed = []
    provider = YtDlpSubtitleEnrichmentProvider(
        ydl_factory=ydl_factory(error=error, observed=observed)
    )

    result = provider.enrich(candidate(), "en")

    assert result.status == expected_status
    assert not hasattr(result, "error")
    assert not observed[0]["workspace"].exists()


def test_parse_failure_is_optional_and_cleans_workspace():
    observed = []
    provider = YtDlpSubtitleEnrichmentProvider(
        ydl_factory=ydl_factory(content="not valid vtt", observed=observed)
    )

    result = provider.enrich(candidate(), "en")

    assert result.status == "failed"
    assert not observed[0]["workspace"].exists()

    discovered = MediaDiscoveryService(
        FakeSearchProvider([search_result("kept")]), provider
    ).discover(discovery_request(enrich_transcript=True))
    assert [item.provider_id for item in discovered] == ["kept"]
    assert discovered[0].transcript_enrichment.status == "failed"


def test_oversized_subtitle_is_rejected_and_cleaned():
    observed = []
    provider = YtDlpSubtitleEnrichmentProvider(
        ydl_factory=ydl_factory(
            content=b"x" * (MAX_SUBTITLE_FILE_BYTES + 1), observed=observed
        )
    )

    result = provider.enrich(candidate(), "en")

    assert result.status == "failed"
    assert not observed[0]["workspace"].exists()


@pytest.mark.parametrize("limit_kind", ["cues", "characters"])
def test_transcript_resource_limits_fail_safely(limit_kind):
    if limit_kind == "cues":
        blocks = [
            f"00:00:00.000 --> 00:00:01.000\ncue {index}"
            for index in range(MAX_SUBTITLE_CUES + 1)
        ]
        content = "WEBVTT\n\n" + "\n\n".join(blocks)
    else:
        content = vtt("x" * (MAX_TRANSCRIPT_CHARACTERS + 1))
    provider = YtDlpSubtitleEnrichmentProvider(
        ydl_factory=ydl_factory(content=content)
    )

    result = provider.enrich(candidate(), "en")

    assert result.status == "failed"


def test_concurrent_provider_calls_use_isolated_workspaces_and_cleanup():
    observed = []
    barrier = Barrier(2)
    provider = YtDlpSubtitleEnrichmentProvider(
        ydl_factory=ydl_factory(
            content=vtt(), observed=observed, barrier=barrier
        )
    )
    first, second = candidate(), candidate()
    second.provider_id = "second"
    second.url = "https://www.youtube.com/watch?v=second"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: provider.enrich(item, "en"), [first, second]
            )
        )

    workspaces = [call["workspace"] for call in observed]
    assert [result.status for result in results] == ["success", "success"]
    assert len(set(workspaces)) == 2
    assert all(not workspace.exists() for workspace in workspaces)


def test_noncanonical_url_is_rejected_without_network_access():
    observed = []
    provider = YtDlpSubtitleEnrichmentProvider(
        ydl_factory=ydl_factory(content=vtt(), observed=observed)
    )
    item = candidate()
    item.url = "https://example.com/internal"

    result = provider.enrich(item, "en")

    assert result.status == "failed"
    assert observed == []


def test_legacy_api_request_keeps_previous_fields_and_adds_null_enrichment(
    monkeypatch,
):
    service = MediaDiscoveryService(
        FakeSearchProvider([search_result("legacy")]),
        FakeEnrichmentProvider({"legacy": success_result()}),
    )
    monkeypatch.setattr(media_discovery_api, "discovery_service", service)

    response = client.post(
        "/api/media/discover", json={"query": "Language lesson"}
    )

    assert response.status_code == 200
    assert response.json()[0]["transcript_enrichment"] is None
    assert response.json()[0]["thumbnail"] is None
    assert service._enrichment_provider.calls == []
