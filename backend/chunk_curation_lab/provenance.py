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
from chunk_curation_lab.snapshot import LibrarySnapshot, SnapshotSentence, sentence_fingerprint
from chunk_curation_lab.windowing import Window, WindowPlan
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


def _is_int(value) -> bool:
    return type(value) is int  # excludes bool


def shape_error(observation: ProviderObservation) -> str | None:
    """Reason a provider observation is malformed, or None. Checks container
    and element types before anything reads them; nothing is repaired,
    truncated or coerced (Round 3, M3)."""
    if not _is_int(observation.window_index):
        return "invalid_window_index"
    if not isinstance(observation.text, str):
        return "invalid_text"
    if observation.evidence_sentence_id is not None and not _is_int(observation.evidence_sentence_id):
        return "invalid_sentence_id"
    if not isinstance(observation.claimed_origin, str):
        return "invalid_origin"
    scores = observation.scores
    if not isinstance(scores, Mapping) or any(not isinstance(key, str) for key in scores) or (
        set(scores) - set(SEMANTIC)
    ) or any(value is not None and (not _is_int(value) or not 0 <= value <= 3) for value in scores.values()):
        return "invalid_scores"
    variations = observation.variations
    if not isinstance(variations, (tuple, list)) or len(variations) > MAX_VARIATIONS or any(
        not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_VARIATION_CHARS
        for value in variations
    ):
        return "invalid_variations"
    if any(value is not None and not isinstance(value, str)
           for value in (observation.claimed_library_item_id, observation.claimed_quote)) or any(
        value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)))
        for value in (observation.claimed_start, observation.claimed_end)
    ):
        return "invalid_claims"
    return None


def citable_ranges(window: Window, sentence: SnapshotSentence) -> list[tuple[int, int]]:
    """Character ranges of `sentence` that `window` may cite. Consecutive
    parts of one sentence in the same window are joined across the
    whitespace between them, because the provider saw both."""
    parts = sorted((value for value in window.citable if value.sentence_id == sentence.sentence_id),
                   key=lambda value: value.part)
    ranges: list[tuple[int, int, int]] = []  # (start, end, last part)
    for part in parts:
        if ranges and ranges[-1][2] + 1 == part.part and not sentence.text[ranges[-1][1]:part.char_start].strip():
            ranges[-1] = (ranges[-1][0], part.char_end, part.part)
        else:
            ranges.append((part.char_start, part.char_end, part.part))
    return [(start, end) for start, end, _ in ranges]


def _ground_in(sentence_text: str, text: str, ranges: list[tuple[int, int]]) -> tuple[int, int] | None:
    for range_start, range_end in ranges:  # char order inside one sentence
        try:
            start, end = AiChunkOrganizerService._ground_phrase(
                sentence_text[range_start:range_end], text, provider_output=True)
        except (ValueError, RuntimeError):
            continue
        return range_start + start, range_start + end
    return None


def verify_observation(
    observation: ProviderObservation, snapshot: LibrarySnapshot, plan: WindowPlan
) -> VerifiedObservation | RejectedObservation:
    malformed = shape_error(observation)
    if malformed:
        return RejectedObservation(malformed, observation.text if isinstance(observation.text, str) else "")
    if observation.claimed_origin != "source_extracted":
        # A variation proposed as a standalone chunk is never a source chunk.
        return RejectedObservation("variation_not_source", observation.text)
    text = observation.text.strip()
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
    # Ground only inside the characters this window could cite (Round 3,
    # M2): a matching sentence id is not enough for a split sentence.
    span = _ground_in(sentence.text, text, citable_ranges(window, sentence))
    if span is None:
        anywhere = _ground_in(sentence.text, text, [(0, len(sentence.text))])
        return RejectedObservation("outside_citable_range" if anywhere else "ungrounded", observation.text)
    start, end = span
    try:
        prepared = prepare_chunk_text(sentence.text[start:end], snapshot.language)
    except (ValueError, ChunkTextRejected):
        return RejectedObservation("ungrounded", observation.text)
    mismatches = tuple(name for name, claimed, actual in (
        ("library_item_id", observation.claimed_library_item_id, snapshot.library_item_id),
        ("quote", observation.claimed_quote, sentence.text),
        ("start", observation.claimed_start, sentence.start),
        ("end", observation.claimed_end, sentence.end),
    ) if claimed is not None and claimed != actual)
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
        variations=tuple(value.strip() for value in observation.variations),
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
