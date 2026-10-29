from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass

from services.schemas.ai_chunks import AiChunkBatch, CandidateDecisionResponse


class CandidateNotFoundError(KeyError):
    pass


class CandidateConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IdempotentDecision:
    batch_id: str
    fingerprint: str
    response: CandidateDecisionResponse


class InMemoryAiChunkStore:
    """Bounded Personal Desktop proposal store; never a cloud persistence fallback."""

    def __init__(self, *, max_batches: int = 100, max_total_batches: int = 500):
        if max_batches < 1 or max_total_batches < 1:
            raise ValueError("Candidate batch limits must be positive")
        self.max_batches = max_batches
        self.max_total_batches = max_total_batches
        self._lock = threading.RLock()
        self._batches: dict[tuple[str, str], AiChunkBatch] = {}
        self._request_keys: dict[tuple[str, str], tuple[str, str]] = {}
        self._decision_keys: dict[tuple[str, str], IdempotentDecision] = {}

    @staticmethod
    def fingerprint(value: object) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get_request(self, owner: str, key: str, fingerprint: str) -> AiChunkBatch | None:
        with self._lock:
            stored = self._request_keys.get((owner, key))
            if stored is None:
                return None
            stored_fingerprint, batch_id = stored
            if stored_fingerprint != fingerprint:
                raise CandidateConflictError("Idempotency key was already used for another request")
            return self._batches[(owner, batch_id)].model_copy(deep=True)

    def put_batch(self, owner: str, key: str, fingerprint: str, batch: AiChunkBatch) -> AiChunkBatch:
        with self._lock:
            existing = self._request_keys.get((owner, key))
            if existing is not None:
                if existing[0] != fingerprint:
                    raise CandidateConflictError("Idempotency key was already used for another request")
                return self._batches[(owner, existing[1])].model_copy(deep=True)
            owner_batches = [entry for entry in self._batches if entry[0] == owner]
            if len(owner_batches) >= self.max_batches:
                oldest = min(owner_batches, key=lambda entry: self._batches[entry].created_at)
                self._evict(oldest)
            if len(self._batches) >= self.max_total_batches:
                oldest = min(self._batches, key=lambda entry: self._batches[entry].created_at)
                self._evict(oldest)
            self._batches[(owner, batch.batch_id)] = batch.model_copy(deep=True)
            self._request_keys[(owner, key)] = (fingerprint, batch.batch_id)
            return batch.model_copy(deep=True)

    def get_batch(self, owner: str, batch_id: str) -> AiChunkBatch:
        with self._lock:
            try:
                return self._batches[(owner, batch_id)].model_copy(deep=True)
            except KeyError as exc:
                raise CandidateNotFoundError(batch_id) from exc

    def get_decision(
        self, owner: str, key: str, fingerprint: str
    ) -> CandidateDecisionResponse | None:
        with self._lock:
            stored = self._decision_keys.get((owner, key))
            if stored is None:
                return None
            if stored.fingerprint != fingerprint:
                raise CandidateConflictError("Idempotency key was already used for another decision")
            return stored.response.model_copy(deep=True)

    def update_candidate(
        self,
        owner: str,
        batch_id: str,
        candidate_id: str,
        expected_version: int,
        *,
        mutate,
        decision_key: str,
        decision_fingerprint: str,
        response_factory,
    ) -> CandidateDecisionResponse:
        with self._lock:
            existing = self.get_decision(owner, decision_key, decision_fingerprint)
            if existing is not None:
                return existing
            key = (owner, batch_id)
            batch = self._batches.get(key)
            if batch is None:
                raise CandidateNotFoundError(batch_id)
            index = next(
                (i for i, value in enumerate(batch.candidates) if value.candidate_id == candidate_id),
                None,
            )
            if index is None:
                raise CandidateNotFoundError(candidate_id)
            candidate = batch.candidates[index]
            if candidate.state != "proposed" or candidate.version != expected_version:
                raise CandidateConflictError("Candidate decision is stale")
            updated, study_item, created, warnings = mutate(candidate)
            candidates = list(batch.candidates)
            candidates[index] = updated
            self._batches[key] = batch.model_copy(update={"candidates": candidates})
            response = response_factory(updated, study_item, created, warnings)
            self._decision_keys[(owner, decision_key)] = IdempotentDecision(
                batch_id=batch_id,
                fingerprint=decision_fingerprint,
                response=response.model_copy(deep=True),
            )
            return response

    def _evict(self, batch_key: tuple[str, str]) -> None:
        owner, batch_id = batch_key
        del self._batches[batch_key]
        for request_key, value in list(self._request_keys.items()):
            if request_key[0] == owner and value[1] == batch_id:
                del self._request_keys[request_key]
        for decision_key, value in list(self._decision_keys.items()):
            if decision_key[0] == owner and value.batch_id == batch_id:
                del self._decision_keys[decision_key]
