from datetime import datetime, timezone

import pytest

from services.retrieval.chunk_memory import ChunkMemoryService
from services.retrieval.chunk_quality import (
    ChunkTextRejected,
    plan_chunk_cleanup,
    prepare_chunk_text,
    sanitize_chunk_text,
)
from services.retrieval.chunk_store import ChunkStore
from services.schemas.retrieval import StoredChunk


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("&gt;&gt; Hello", "Hello"),
        (">> Hello", "Hello"),
        ("[music] Hello", "Hello"),
        ("Hello [applause]", "Hello"),
        ("[LAUGHTER] Hello", "Hello"),
        ("(audience cheering) Hello", "Hello"),
        ("♪ Hello ♫", "Hello"),
        ("&quot;Hello&quot; &amp; goodbye", '"Hello" & goodbye'),
        ("  Hello\n\tworld  ", "Hello world"),
        ("[Chapter 1] Hello", "[Chapter 1] Hello"),
        ("(important) Hello", "(important) Hello"),
        ("The value 2 > 1 stays.", "The value 2 > 1 stays."),
    ],
)
def test_sanitize_chunk_text_is_boundary_aware(raw, expected):
    assert sanitize_chunk_text(raw) == expected


def test_html_entities_are_decoded_once_only():
    assert sanitize_chunk_text("&amp;gt;&amp;gt; Hello") == "&gt;&gt; Hello"


@pytest.mark.parametrize(
    "label",
    [
        "music",
        "applause",
        "laughter",
        "laughs",
        "cheering",
        "audience cheering",
        "inaudible",
    ],
)
def test_recognized_caption_noise_labels_are_case_insensitive(label):
    assert sanitize_chunk_text(f"[{label.upper()}] Hello [{label}]") == "Hello"


@pytest.mark.parametrize(
    "raw",
    [
        "[music]",
        ">>",
        "♪ ♫",
        "...",
        "Oh. Oh.",
        "Ah! Ah!",
        "Uh, um.",
        "Whoa, whoa, whoa.",
        "GIVENS, OH. OH.",
    ],
)
def test_prepare_chunk_text_rejects_low_value_noise(raw):
    with pytest.raises(ChunkTextRejected):
        prepare_chunk_text(raw, "en")


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Blocked it.", "en"),
        ("Exactly.", "en"),
        ("Thank you.", "en"),
        ("Not yet.", "en"),
        ("Come on.", "en"),
        ("Sounds good.", "en"),
        ("Genau.", "de"),
        ("Danke schön.", "de"),
        ("Noch nicht.", "de"),
    ],
)
def test_prepare_chunk_text_preserves_useful_short_phrases(text, language):
    prepared = prepare_chunk_text(text, language)

    assert prepared.text == text


def test_service_stores_sanitized_text_and_deduplicates_on_it(tmp_path):
    service = ChunkMemoryService(
        ChunkStore(tmp_path / "chunk_bank.json"),
        id_factory=iter(("first", "second")).__next__,
    )

    first, first_created = service.save_chunk(
        user_id="learner",
        text="&gt;&gt;  Hello there.",
        language="en",
    )
    duplicate, duplicate_created = service.save_chunk(
        user_id="learner",
        text="Hello there.",
        language="en",
    )

    assert first_created is True
    assert duplicate_created is False
    assert first.text == "Hello there."
    assert first.normalized_text == "hello there"
    assert duplicate.id == first.id
    assert len(service.list_chunks("learner")) == 1


def stored_chunk(identifier, text, normalized_text, **overrides):
    values = {
        "id": identifier,
        "user_id": "learner",
        "text": text,
        "normalized_text": normalized_text,
        "meaning": "meaning",
        "source_language": "en",
        "source_lesson_id": "lesson-old",
        "source_media_id": "youtube:old",
        "source_sentence_id": 1,
        "source_stage": "lesson",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
        "last_practiced_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
        "practice_count": 5,
        "success_count": 3,
        "failure_count": 1,
        "mastery_score": 0.6,
        "active": True,
    }
    values.update(overrides)
    return StoredChunk(**values)


def test_cleanup_plan_preserves_history_and_is_deterministic():
    original = stored_chunk("clean-me", ">> Hello there.", ">> hello there")

    first = plan_chunk_cleanup([original])
    second = plan_chunk_cleanup(first.chunks)

    cleaned = first.chunks[0]
    assert cleaned.text == "Hello there."
    assert cleaned.normalized_text == "hello there"
    assert cleaned.practice_count == original.practice_count
    assert cleaned.success_count == original.success_count
    assert cleaned.failure_count == original.failure_count
    assert cleaned.mastery_score == original.mastery_score
    assert cleaned.last_practiced_at == original.last_practiced_at
    assert cleaned.created_at == original.created_at
    assert cleaned.updated_at == original.updated_at
    assert first.cleaned_ids == ("clean-me",)
    assert second.chunks == first.chunks
    assert second.cleaned_ids == ()


def test_cleanup_plan_removes_noise_but_skips_sanitized_collisions():
    dirty = stored_chunk("dirty", ">> Hello.", ">> hello")
    already_clean = stored_chunk("clean", "Hello.", "hello")
    noise = stored_chunk("noise", "[music]", "[music]")

    plan = plan_chunk_cleanup([dirty, already_clean, noise])

    assert plan.chunks == (dirty, already_clean)
    assert plan.removed_ids == ("noise",)
    assert plan.collision_ids == ("dirty", "clean")
    assert plan.cleaned_ids == ()
