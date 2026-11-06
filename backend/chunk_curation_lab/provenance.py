"""Server-side provenance verification and re-validation.

Provider output is untrusted. A source-extracted candidate is accepted only
when its text is grounded in a sentence that was citable in the window that
proposed it; every evidence field is then copied from the trusted snapshot.
Claimed media ids, quotes and timestamps are ignored (mismatches are only
counted). An AI variation can never become source evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from chunk_curation_lab.scoring import SEMANTIC
from chunk_curation_lab.snapshot import LibrarySnapshot, sentence_fingerprint
from chunk_curation_lab.windowing import WindowPlan
from services.ai_chunks.service import AiChunkOrganizerService
from services.retrieval.chunk_quality import ChunkTextRejected, prepare_chunk_text

MAX_CHUNK_CHARS = 120
MAX_VARIATIONS = 2
MAX_VARIATION_CHARS = 200


@dataclass(frozen=True)
class ProviderObservation:
    """One candidate as a provider (or test stub) returned it. Untrusted."""
    window_index: int
    text: str
    evidence_sentence_id: int | None
    claimed_origin: str = "source_extracted"
    scores: Mapping[str, int | None] = field(default_factory=dict)
    variations: tuple[str, ...] = ()
    claimed_library_item_id: str | None = None
    claimed_quote: str | None = None
    claimed_start: float | None = None
    claimed_end: float | None = None


@dataclass(frozen=True)
class VerifiedEvidence:
    library_item_id: str
    lesson_id: str
    transcript_revision: str
    sentence_id: int
    sentence_text: str
    sentence_fingerprint: str
    start: float | None
    end: float | None
    evidence_start: int
    evidence_end: int
    # 3: same characters as the source, ignoring case (so a sentence-initial
    # capital is not penalised); 2: matched only after punctuation/space
    # normalisation.
    reliability: int

    @property
    def key(self) -> tuple:
        return (self.library_item_id, self.sentence_id, self.evidence_start, self.evidence_end)


@dataclass(frozen=True)
class VerifiedObservation:
    identity: str
    display_text: str  # the exact source substring, from the snapshot
    evidence: VerifiedEvidence
    scores: Mapping[str, int | None]
    variations: tuple[str, ...]
    claim_mismatches: tuple[str, ...]


@dataclass(frozen=True)
class RejectedObservation:
    reason: str
    text: str


def verify_observation(
    observation: ProviderObservation, snapshot: LibrarySnapshot, plan: WindowPlan
) -> VerifiedObservation | RejectedObservation:
    if observation.claimed_origin != "source_extracted":
        # A variation proposed as a standalone chunk is never a source chunk.
        return RejectedObservation("variation_not_source", observation.text)
    scores = observation.scores
    if not isinstance(scores, Mapping) or set(scores) - set(SEMANTIC) or any(
        value is not None and (type(value) is not int or not 0 <= value <= 3)
        for value in scores.values()
    ):
        return RejectedObservation("invalid_scores", observation.text)
    text = observation.text.strip() if isinstance(observation.text, str) else ""
    if not text or len(text) > MAX_CHUNK_CHARS or len(text.split()) < 2:
        return RejectedObservation("invalid_text", observation.text)
    window = next((value for value in plan.windows if value.index == observation.window_index), None)
    if window is None:
        return RejectedObservation("unknown_window", observation.text)
    sentence = snapshot.sentence(observation.evidence_sentence_id) if observation.evidence_sentence_id else None
    if sentence is None:
        return RejectedObservation("unknown_sentence", observation.text)
    if sentence.sentence_id not in window.citable_sentence_ids:
        return RejectedObservation("not_citable_in_window", observation.text)
    try:
        start, end = AiChunkOrganizerService._ground_phrase(sentence.text, text, provider_output=True)
        prepared = prepare_chunk_text(sentence.text[start:end], snapshot.language)
    except (ValueError, RuntimeError, ChunkTextRejected):
        return RejectedObservation("ungrounded", observation.text)
    mismatches = tuple(name for name, claimed, actual in (
        ("library_item_id", observation.claimed_library_item_id, snapshot.library_item_id),
        ("quote", observation.claimed_quote, sentence.text),
        ("start", observation.claimed_start, sentence.start),
        ("end", observation.claimed_end, sentence.end),
    ) if claimed is not None and claimed != actual)
    variations = tuple(
        value.strip() for value in tuple(observation.variations)[:MAX_VARIATIONS]
        if isinstance(value, str) and value.strip() and len(value.strip()) <= MAX_VARIATION_CHARS
    )
    return VerifiedObservation(
        identity=prepared.normalized_text,
        display_text=sentence.text[start:end],
        evidence=VerifiedEvidence(
            library_item_id=snapshot.library_item_id,
            lesson_id=snapshot.lesson_id,
            transcript_revision=snapshot.transcript_revision,
            sentence_id=sentence.sentence_id,
            sentence_text=sentence.text,
            sentence_fingerprint=sentence_fingerprint(sentence.text),
            start=sentence.start,
            end=sentence.end,
            evidence_start=start,
            evidence_end=end,
            reliability=3 if text.casefold() == sentence.text[start:end].casefold() else 2,
        ),
        scores=dict(observation.scores),
        variations=variations,
        claim_mismatches=mismatches,
    )


def revalidate(evidence: VerifiedEvidence, current: LibrarySnapshot | None) -> str:
    """Status of stored evidence against the Library as it is now. Mirrors the
    accept-time check in ai_chunks/postgres.py (`_validate_library_evidence`):
    anything but `valid` or `revalidated` must block acceptance."""
    if current is None or current.library_item_id != evidence.library_item_id:
        return "source_deleted"
    if current.lesson_id != evidence.lesson_id:
        return "lesson_replaced"
    sentence = current.sentence(evidence.sentence_id)
    if sentence is None:
        return "sentence_missing"
    if (sentence.text, sentence.start, sentence.end) != (
        evidence.sentence_text, evidence.start, evidence.end
    ):
        return "sentence_changed"
    if current.transcript_revision != evidence.transcript_revision:
        # Other sentences changed; this one is identical. Allowed, but the
        # caller must re-ground before accepting and record the new revision.
        return "revalidated"
    return "valid"
