from __future__ import annotations

import re
import threading
import unicodedata
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import ValidationError

from services.ai_chunks.adapter import (
    ChunkModelAdapter,
    DeterministicMockChunkAdapter,
    ProposedChunk,
    SourceSegment,
    normalize_phrase,
)
from services.ai_chunks.store import InMemoryAiChunkStore
from services.ai_chunks.postgres import (
    CloudAiChunkUnavailableError,
    PostgresOwnerScopedAiChunkRepository,
    PreparedDecision,
)
from services.library.store import LearningLibraryStore, LibraryStoreError
from services.retrieval.chunk_store import ChunkStoreError
from services.schemas.ai_chunks import (
    AiChunkBatch,
    AiChunkCandidate,
    CandidateDecisionRequest,
    CandidateDecisionResponse,
    CandidateEvidence,
    ChunkSuggestionRequest,
    LibraryPassageSource,
    ManualTextSource,
)
from services.schemas.study_bank import (
    AiTextStudyProvenance,
    MediaStudyProvenance,
    StudyItemInput,
)
from services.study_bank.store import StudyBankStore, normalized_identity, utc_now


class AiChunkValidationError(ValueError):
    pass


class AiChunkProviderError(RuntimeError):
    pass


class AiChunkOrganizerService:
    def __init__(
        self,
        library: LearningLibraryStore,
        study_bank: StudyBankStore,
        *,
        adapter: ChunkModelAdapter | None = None,
        store: InMemoryAiChunkStore | None = None,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        chunk_memory=None,
    ):
        self.library = library
        self.study_bank = study_bank
        self.adapter = adapter or DeterministicMockChunkAdapter()
        self.store = store or InMemoryAiChunkStore()
        self.clock = clock
        self.id_factory = id_factory
        self.chunk_memory = chunk_memory
        self._decision_lock = threading.RLock()

    def suggest(self, owner: str, request: ChunkSuggestionRequest) -> AiChunkBatch:
        fingerprint = self.store.fingerprint(request.model_dump(mode="json", exclude={"idempotency_key"}))
        existing = self.store.get_request(owner, request.idempotency_key, fingerprint)
        if existing is not None:
            return existing
        segments, evidence = self._source(owner, request)
        try:
            raw = self.adapter.propose(
                language=request.source_language,
                segments=segments,
                max_candidates=request.max_candidates,
            )
            proposals = []
            for index, value in enumerate(raw):
                if index >= request.max_candidates:
                    raise AiChunkProviderError("Chunk provider exceeded the candidate limit")
                proposals.append(
                    value if isinstance(value, ProposedChunk) else ProposedChunk.model_validate(value)
                )
        except AiChunkProviderError:
            raise
        except (ValidationError, ValueError, TypeError) as exc:
            raise AiChunkProviderError("Chunk provider returned invalid output") from exc
        except Exception as exc:
            raise AiChunkProviderError("Chunk provider is unavailable") from exc
        saved = self.study_bank.list_items(owner, kind="chunk", language=request.source_language, active=None)
        saved_by_identity = {item.normalized_text: item for item in saved}
        candidates: list[AiChunkCandidate] = []
        seen: set[str] = set()
        for proposal in proposals:
            source = evidence.get(proposal.evidence_segment_id)
            if source is None:
                raise AiChunkProviderError("Chunk provider referenced unknown evidence")
            phrase_range = self._ground_phrase(source.quote, proposal.text, provider_output=True)
            candidate_input = StudyItemInput(
                user_id=owner,
                kind="chunk",
                source_language=request.source_language,
                target_language="zh-CN",
                text=proposal.text,
                meaning=proposal.suggested_meaning_zh,
                example=proposal.example or source.quote,
                usage=proposal.usage_note,
                provenance=[self._provenance(source, phrase_range)],
            )
            clean_text, identity = normalized_identity(candidate_input)
            if identity in seen:
                continue
            seen.add(identity)
            prior = saved_by_identity.get(identity)
            candidates.append(AiChunkCandidate(
                candidate_id=self.id_factory(),
                source_language=request.source_language,
                text=clean_text,
                normalized_identity=identity,
                suggested_meaning_zh=proposal.suggested_meaning_zh,
                usage_note=proposal.usage_note,
                example=proposal.example or source.quote,
                explanation_of_value=proposal.explanation_of_value,
                quality_flags=proposal.quality_flags,
                evidence=source.model_copy(update={
                    "evidence_start": phrase_range[0], "evidence_end": phrase_range[1]
                }),
                proposal_model=self.adapter.model_id,
                proposal_version=self.adapter.proposal_version,
                existing_study_item_id=prior.id if prior else None,
                existing_study_item_active=prior.active if prior else None,
            ))
        batch = AiChunkBatch(
            batch_id=self.id_factory(),
            user_id=owner,
            source_language=request.source_language,
            source_type=request.source.source_type,
            source_label=next(iter(evidence.values())).source_label,
            created_at=self._now(),
            candidates=candidates,
        )
        return self.store.put_batch(owner, request.idempotency_key, fingerprint, batch)

    def get_batch(self, owner: str, batch_id: str) -> AiChunkBatch:
        return self.store.get_batch(owner, batch_id)

    def decide(
        self, owner: str, batch_id: str, request: CandidateDecisionRequest
    ) -> CandidateDecisionResponse:
        fingerprint = self.store.fingerprint({
            "batch_id": batch_id,
            "request": request.model_dump(mode="json", exclude={"idempotency_key"}),
        })
        with self._decision_lock:
            return self.store.update_candidate(
                owner,
                batch_id,
                request.candidate_id,
                request.expected_version,
                mutate=lambda candidate: self._apply_decision(owner, candidate, request),
                decision_key=request.idempotency_key,
                decision_fingerprint=fingerprint,
                response_factory=lambda candidate, item, created, warnings: CandidateDecisionResponse(
                    candidate=candidate, study_item=item, created=created, warnings=warnings
                ),
            )

    def _apply_decision(self, owner, candidate, request):
        if request.action == "reject":
            return candidate.model_copy(update={"state": "rejected", "version": candidate.version + 1}), None, False, []
        edit = request.edit
        text = edit.text if edit else candidate.text
        meaning = edit.suggested_meaning_zh if edit else candidate.suggested_meaning_zh
        usage = edit.usage_note if edit else candidate.usage_note
        example = edit.example if edit else candidate.example
        phrase_range = self._ground_phrase(candidate.evidence.quote, text, provider_output=False)
        provenance = self._provenance(candidate.evidence, phrase_range)
        value = StudyItemInput(
            user_id=owner,
            kind="chunk",
            source_language=candidate.source_language,
            target_language="zh-CN",
            text=text,
            meaning=meaning,
            example=example,
            usage=usage,
            provenance=[provenance],
        )
        items, created_count = self.study_bank.save_items([value])
        item = items[0]
        warnings: list[str] = []
        if self.chunk_memory is not None and isinstance(provenance, MediaStudyProvenance):
            try:
                existing = next(
                    (
                        chunk for chunk in self.chunk_memory.list_chunks(
                            owner, candidate.source_language, include_archived=True
                        )
                        if chunk.normalized_text == item.normalized_text
                    ),
                    None,
                )
                chunk, _ = self.chunk_memory.save_chunk(
                    user_id=owner,
                    text=item.text,
                    # Never overwrite an existing manually curated meaning.
                    meaning="" if existing is not None else item.meaning,
                    language=candidate.source_language,
                    source_lesson_id=provenance.lesson_id,
                    source_media_id=provenance.library_item_id,
                    source_sentence_id=provenance.sentence_id,
                    source_stage="study_bank",
                )
                if not chunk.active:
                    self.chunk_memory.set_active(owner, chunk.id, True)
            except (ChunkStoreError, KeyError, ValueError):
                warnings.append("Chunk Memory compatibility sync failed; Study Bank save succeeded.")
        state = "edited_and_accepted" if request.action == "edit_and_accept" else "accepted"
        updated = candidate.model_copy(update={
            "state": state,
            "version": candidate.version + 1,
            "text": item.text,
            "normalized_identity": item.normalized_text,
            "suggested_meaning_zh": meaning,
            "usage_note": usage,
            "example": example,
            "accepted_study_item_id": item.id,
            "existing_study_item_id": item.id,
            "existing_study_item_active": item.active,
            "evidence": candidate.evidence.model_copy(update={
                "evidence_start": phrase_range[0], "evidence_end": phrase_range[1]
            }),
        })
        return updated, item, bool(created_count), warnings

    def _source(self, owner: str, request: ChunkSuggestionRequest):
        if isinstance(request.source, ManualTextSource):
            segment = SourceSegment(segment_id="manual:0", text=request.source.text)
            evidence = CandidateEvidence(
                source_type="manual_text",
                quote=request.source.text,
                source_label=request.source.label,
                evidence_start=0,
                evidence_end=len(request.source.text),
            )
            return [segment], {segment.segment_id: evidence}
        if not isinstance(request.source, LibraryPassageSource):
            raise AiChunkValidationError("Unsupported candidate source")
        item = self.library.get_item(request.source.library_item_id)
        if item.lesson.source_language != request.source_language:
            raise AiChunkValidationError("Source language does not match the Library item")
        requested = set(request.source.sentence_ids)
        selected = [sentence for sentence in item.lesson.sentences if sentence.id in requested]
        if len(selected) != len(requested):
            raise AiChunkValidationError("One or more source sentences do not exist")
        if any(len(sentence.text) > 2_000 for sentence in selected):
            raise AiChunkValidationError("A source sentence exceeds the candidate input limit")
        if sum(len(sentence.text) for sentence in selected) > 6_000:
            raise AiChunkValidationError("The selected passage exceeds the candidate input limit")
        segments, evidence = [], {}
        for sentence in selected:
            segment_id = f"sentence:{sentence.id}"
            segments.append(SourceSegment(segment_id=segment_id, text=sentence.text))
            evidence[segment_id] = CandidateEvidence(
                source_type="library_sentence",
                quote=sentence.text,
                source_label=item.lesson.title,
                library_item_id=item.id,
                lesson_id=item.lesson.id,
                sentence_id=sentence.id,
                source_url=item.lesson.source_url,
                sentence_translation=sentence.translation,
                start=sentence.start,
                end=sentence.end,
                evidence_start=0,
                evidence_end=len(sentence.text),
            )
        return segments, evidence

    @staticmethod
    def _ground_phrase(
        source: str, phrase: str, *, provider_output: bool
    ) -> tuple[int, int]:
        normalized_phrase = normalize_phrase(phrase)
        if not normalized_phrase:
            raise AiChunkValidationError("Candidate has no lexical content")
        normalized_source, offsets = AiChunkOrganizerService._normalized_with_offsets(source)
        padded = f" {normalized_source} "
        match = re.search(rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)", padded)
        if match is None or not offsets:
            error = AiChunkProviderError if provider_output else AiChunkValidationError
            raise error("Candidate text is not grounded in the authorized source")
        start = match.start() - 1
        end = match.end() - 1
        return offsets[start], offsets[end - 1] + 1

    @staticmethod
    def _normalized_with_offsets(value: str) -> tuple[str, list[int]]:
        characters: list[str] = []
        offsets: list[int] = []
        for original_index, original_character in enumerate(value):
            for character in unicodedata.normalize("NFKC", original_character).casefold():
                normalized = (
                    character
                    if (character.isalnum() or character == "_" or character in "'’")
                    else " "
                )
                if normalized == " " and (not characters or characters[-1] == " "):
                    continue
                characters.append(normalized)
                offsets.append(original_index)
        if characters and characters[-1] == " ":
            characters.pop()
            offsets.pop()
        return "".join(characters), offsets

    @staticmethod
    def _provenance(evidence: CandidateEvidence, phrase_range: tuple[int, int]):
        if evidence.source_type == "manual_text":
            return AiTextStudyProvenance(
                source_label=evidence.source_label,
                source_text=evidence.quote,
                evidence_start=phrase_range[0],
                evidence_end=phrase_range[1],
            )
        return MediaStudyProvenance(
            library_item_id=evidence.library_item_id,
            lesson_id=evidence.lesson_id,
            source_url=evidence.source_url,
            media_title=evidence.source_label,
            sentence_id=evidence.sentence_id,
            sentence_text=evidence.quote,
            sentence_translation=evidence.sentence_translation,
            start=evidence.start,
            end=evidence.end,
        )

    def _now(self):
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AiChunkValidationError("Chunk organizer clock must be timezone-aware")
        return value.astimezone(timezone.utc)


class CloudAiChunkOrganizerService(AiChunkOrganizerService):
    """Cloud organizer using durable owner-scoped candidate persistence."""

    def __init__(
        self,
        library,
        study_bank,
        candidates: PostgresOwnerScopedAiChunkRepository,
        *,
        adapter: ChunkModelAdapter | None = None,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ):
        # Cloud intentionally has no Chunk Memory compatibility writer.
        super().__init__(
            library,
            study_bank,
            adapter=adapter or DeterministicMockChunkAdapter(),
            store=candidates,
            clock=clock,
            id_factory=id_factory,
            chunk_memory=None,
        )
        self.candidates = candidates

    def suggest(self, owner: str, request: ChunkSuggestionRequest) -> AiChunkBatch:
        fingerprint = self.candidates.fingerprint(
            request.model_dump(mode="json", exclude={"idempotency_key", "user_id"})
        )
        reservation = self.candidates.reserve_generation(
            owner, request.idempotency_key, fingerprint
        )
        if reservation.existing_batch is not None:
            return reservation.existing_batch
        lease_token = reservation.lease_token
        if lease_token is None:  # defensive: repository returns exactly one outcome
            raise AiChunkValidationError("Candidate reservation is incomplete")
        try:
            segments, evidence = self._cloud_source(owner, request)
            proposals = self._propose(segments, request)
            saved = self.study_bank.list_items(
                owner, kind="chunk", language=request.source_language, active=None
            )
            saved_by_identity = {item.normalized_text: item for item in saved}
            candidates: list[AiChunkCandidate] = []
            seen: set[str] = set()
            for proposal in proposals:
                source = evidence.get(proposal.evidence_segment_id)
                if source is None:
                    raise AiChunkProviderError("Chunk provider referenced unknown evidence")
                phrase_range = self._ground_phrase(
                    source.quote, proposal.text, provider_output=True
                )
                value = StudyItemInput(
                    user_id=owner,
                    kind="chunk",
                    source_language=request.source_language,
                    target_language="zh-CN",
                    text=proposal.text,
                    meaning=proposal.suggested_meaning_zh,
                    example=proposal.example or source.quote,
                    usage=proposal.usage_note,
                    provenance=[self._provenance(source, phrase_range)],
                )
                clean_text, identity = normalized_identity(value)
                if identity in seen:
                    continue
                seen.add(identity)
                prior = saved_by_identity.get(identity)
                candidates.append(
                    AiChunkCandidate(
                        candidate_id=self.id_factory(),
                        source_language=request.source_language,
                        text=clean_text,
                        normalized_identity=identity,
                        suggested_meaning_zh=proposal.suggested_meaning_zh,
                        usage_note=proposal.usage_note,
                        example=proposal.example or source.quote,
                        explanation_of_value=proposal.explanation_of_value,
                        quality_flags=proposal.quality_flags,
                        evidence=source.model_copy(
                            update={
                                "evidence_start": phrase_range[0],
                                "evidence_end": phrase_range[1],
                            }
                        ),
                        proposal_model=self.adapter.model_id,
                        proposal_version=self.adapter.proposal_version,
                        existing_study_item_id=prior.id if prior else None,
                        existing_study_item_active=prior.active if prior else None,
                    )
                )
            batch = AiChunkBatch(
                batch_id=self.id_factory(),
                user_id=owner,
                source_language=request.source_language,
                source_type=request.source.source_type,
                source_label=next(iter(evidence.values())).source_label,
                created_at=self._now(),
                candidates=candidates,
            )
            return self.candidates.complete_generation(
                owner,
                request.idempotency_key,
                fingerprint,
                lease_token,
                batch,
            )
        except Exception:
            # A process crash is recovered by lease expiry; ordinary failures
            # release this exact lease so the same request can retry immediately.
            try:
                self.candidates.abandon_generation(
                    owner, request.idempotency_key, fingerprint, lease_token
                )
            except Exception:
                pass
            raise

    def get_batch(self, owner: str, batch_id: str) -> AiChunkBatch:
        return self.candidates.get_batch(owner, batch_id)

    def decide(
        self, owner: str, batch_id: str, request: CandidateDecisionRequest
    ) -> CandidateDecisionResponse:
        fingerprint = self.candidates.fingerprint(
            {
                "batch_id": batch_id,
                "request": request.model_dump(
                    mode="json", exclude={"idempotency_key", "user_id"}
                ),
            }
        )
        return self.candidates.decide_atomic(
            owner,
            batch_id,
            request.candidate_id,
            request.expected_version,
            action=request.action,
            decision_key=request.idempotency_key,
            decision_fingerprint=fingerprint,
            prepare=lambda candidate: self._prepare_decision(owner, candidate, request),
        )

    def _cloud_source(self, owner: str, request: ChunkSuggestionRequest):
        if isinstance(request.source, ManualTextSource):
            segment = SourceSegment(segment_id="manual:0", text=request.source.text)
            evidence = CandidateEvidence(
                source_type="manual_text",
                quote=request.source.text,
                source_label=request.source.label,
                evidence_start=0,
                evidence_end=len(request.source.text),
            )
            return [segment], {segment.segment_id: evidence}
        if not isinstance(request.source, LibraryPassageSource):
            raise AiChunkValidationError("Unsupported candidate source")
        try:
            item = self.library.get_for_owner(owner, request.source.library_item_id)
        except KeyError:
            raise
        except LibraryStoreError as exc:
            raise CloudAiChunkUnavailableError(
                "Cloud candidate source is unavailable"
            ) from exc
        if item.lesson.source_language != request.source_language:
            raise AiChunkValidationError("Source language does not match the Library item")
        if item.lesson.target_language != "zh-CN":
            raise AiChunkValidationError(
                "AI Chunk candidates require a Chinese-target Library lesson"
            )
        requested = set(request.source.sentence_ids)
        selected = [sentence for sentence in item.lesson.sentences if sentence.id in requested]
        if len(selected) != len(requested):
            raise AiChunkValidationError("One or more source sentences do not exist")
        if any(len(sentence.text) > 2_000 for sentence in selected):
            raise AiChunkValidationError("A source sentence exceeds the candidate input limit")
        if sum(len(sentence.text) for sentence in selected) > 6_000:
            raise AiChunkValidationError("The selected passage exceeds the candidate input limit")
        segments: list[SourceSegment] = []
        evidence: dict[str, CandidateEvidence] = {}
        for sentence in selected:
            segment_id = f"sentence:{sentence.id}"
            segments.append(SourceSegment(segment_id=segment_id, text=sentence.text))
            evidence[segment_id] = CandidateEvidence(
                source_type="library_sentence",
                quote=sentence.text,
                source_label=item.lesson.title,
                library_item_id=item.id,
                lesson_id=item.lesson.id,
                sentence_id=sentence.id,
                source_url=item.lesson.source_url,
                sentence_translation=sentence.translation,
                start=sentence.start,
                end=sentence.end,
                evidence_start=0,
                evidence_end=len(sentence.text),
            )
        return segments, evidence

    def _propose(self, segments, request):
        try:
            raw = self.adapter.propose(
                language=request.source_language,
                segments=segments,
                max_candidates=request.max_candidates,
            )
            proposals: list[ProposedChunk] = []
            for index, value in enumerate(raw):
                if index >= request.max_candidates:
                    raise AiChunkProviderError("Chunk provider exceeded the candidate limit")
                proposals.append(
                    value
                    if isinstance(value, ProposedChunk)
                    else ProposedChunk.model_validate(value)
                )
            return proposals
        except AiChunkProviderError:
            raise
        except (ValidationError, ValueError, TypeError) as exc:
            raise AiChunkProviderError("Chunk provider returned invalid output") from exc
        except Exception as exc:
            raise AiChunkProviderError("Chunk provider is unavailable") from exc

    def _prepare_decision(self, owner, candidate, request) -> PreparedDecision:
        if request.action == "reject":
            return PreparedDecision(
                candidate=candidate.model_copy(
                    update={"state": "rejected", "version": candidate.version + 1}
                ),
                study_input=None,
            )
        edit = request.edit
        text = edit.text if edit else candidate.text
        meaning = edit.suggested_meaning_zh if edit else candidate.suggested_meaning_zh
        usage = edit.usage_note if edit else candidate.usage_note
        example = edit.example if edit else candidate.example
        phrase_range = self._ground_phrase(candidate.evidence.quote, text, provider_output=False)
        provenance = self._provenance(candidate.evidence, phrase_range)
        value = StudyItemInput(
            user_id=owner,
            kind="chunk",
            source_language=candidate.source_language,
            target_language="zh-CN",
            text=text,
            meaning=meaning,
            example=example,
            usage=usage,
            provenance=[provenance],
        )
        state = "edited_and_accepted" if request.action == "edit_and_accept" else "accepted"
        updated = candidate.model_copy(
            update={
                "state": state,
                "version": candidate.version + 1,
                "text": text,
                "suggested_meaning_zh": meaning,
                "usage_note": usage,
                "example": example,
                "evidence": candidate.evidence.model_copy(
                    update={
                        "evidence_start": phrase_range[0],
                        "evidence_end": phrase_range[1],
                    }
                ),
            }
        )
        return PreparedDecision(candidate=updated, study_input=value)
