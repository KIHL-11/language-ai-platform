import uuid
from datetime import datetime, timezone

import numpy as np

from services.retrieval.chunk_store import ChunkStore
from services.retrieval.embedder import Embedder
from services.schemas.lesson import Lesson, SourceLanguage
from services.schemas.retrieval import RetrievedChunk, StoredChunk


class ChunkRetriever:
    def __init__(self, store: ChunkStore, embedder: Embedder | None = None):
        self.store = store
        self.embedder = embedder or Embedder()

    def retrieve_chunks(
        self,
        query: str,
        source_language: SourceLanguage,
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")

        candidates = [
            chunk
            for chunk in self.store.read_chunks()
            if chunk.source_language == source_language
        ]
        if not candidates:
            return []

        embeddings = self.embedder.embed([query, *[chunk.text for chunk in candidates]])
        query_vector = embeddings[0]
        chunk_vectors = embeddings[1:]
        query_norm = np.linalg.norm(query_vector)
        chunk_norms = np.linalg.norm(chunk_vectors, axis=1)
        denominators = chunk_norms * query_norm
        scores = np.divide(
            chunk_vectors @ query_vector,
            denominators,
            out=np.zeros(len(candidates), dtype=float),
            where=denominators != 0,
        )

        ranked_indexes = np.argsort(-scores, kind="stable")[:top_k]
        return [
            RetrievedChunk(
                **candidates[index].model_dump(),
                score=float(np.clip(scores[index], -1.0, 1.0)),
            )
            for index in ranked_indexes
        ]


def add_lesson_chunks(lesson: Lesson, store: ChunkStore) -> list[StoredChunk]:
    created_at = datetime.now(timezone.utc)
    chunks = []
    for sentence in lesson.sentences:
        for chunk in sentence.chunks:
            if not chunk.text.strip():
                continue
            chunks.append(
                StoredChunk(
                    id=uuid.uuid4().hex,
                    text=chunk.text,
                    meaning=chunk.meaning,
                    source_language=lesson.source_language,
                    source_lesson_id=lesson.id,
                    source_sentence_id=sentence.id,
                    created_at=created_at,
                )
            )
    return store.add_chunks(chunks)


_default_store = ChunkStore()
_default_retriever = ChunkRetriever(_default_store)


def retrieve_chunks(
    query: str,
    source_language: SourceLanguage,
    top_k: int = 5,
) -> list[RetrievedChunk]:
    return _default_retriever.retrieve_chunks(query, source_language, top_k)

