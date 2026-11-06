"""The trusted, server-side view of one authorized Library transcript.

Everything a recommendation may claim about its source (media id, sentence
text, timestamps) is read from here, never from provider output.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class SnapshotSentence:
    sentence_id: int
    text: str
    start: float | None = None
    end: float | None = None


@dataclass(frozen=True)
class LibrarySnapshot:
    owner: str
    library_item_id: str
    lesson_id: str
    language: str
    sentences: tuple[SnapshotSentence, ...]

    def __post_init__(self):
        ids = [value.sentence_id for value in self.sentences]
        if len(set(ids)) != len(ids) or any(value <= 0 for value in ids):
            raise ValueError("snapshot sentence ids must be unique positive integers")

    @property
    def transcript_revision(self) -> str:
        """Content hash of the transcript. The current Lesson schema has no
        revision field, so any change to text or timing is a new revision."""
        payload = [[s.sentence_id, s.text, s.start, s.end] for s in self.sentences]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

    def sentence(self, sentence_id: int) -> SnapshotSentence | None:
        return next((value for value in self.sentences if value.sentence_id == sentence_id), None)


def sentence_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
