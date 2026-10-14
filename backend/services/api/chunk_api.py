from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from services.retrieval.chunk_memory import (
    DEFAULT_ACTIVE_RECALL_LIMIT,
    MAX_ACTIVE_RECALL_LIMIT,
    ChunkMemoryService,
)
from services.retrieval.chunk_store import ChunkStore
from services.retrieval.retriever import ChunkRetriever, add_lesson_chunks
from services.schemas.lesson import Lesson, SourceLanguage
from services.schemas.retrieval import (
    ActiveRecallResponse,
    ChunkActiveRequest,
    ChunkMemoryCreateRequest,
    ChunkMemoryListResponse,
    ChunkMemoryUpsertResponse,
    ChunkPracticeRequest,
    ChunkSearchRequest,
    ChunkSearchResponse,
    StoreLessonMemoryRequest,
    StoreLessonChunksResponse,
    StoredChunk,
)


router = APIRouter()
chunk_store = ChunkStore()
chunk_retriever = ChunkRetriever(chunk_store)
chunk_memory_service = ChunkMemoryService(chunk_store)


@router.post("/chunks/from-lesson", response_model=StoreLessonChunksResponse)
def store_chunks_from_lesson(lesson: Lesson):
    added = add_lesson_chunks(lesson, chunk_store)
    return StoreLessonChunksResponse(added=len(added), total=chunk_store.count())


@router.post("/chunks/search", response_model=ChunkSearchResponse)
def search_chunks(request: ChunkSearchRequest):
    results = chunk_retriever.retrieve_chunks(
        request.query,
        source_language=request.source_language,
        top_k=request.top_k,
        user_id=request.user_id,
    )
    return ChunkSearchResponse(results=results)


@router.post("/chunks/memory", response_model=ChunkMemoryUpsertResponse)
def save_chunk_memory(request: ChunkMemoryCreateRequest):
    try:
        chunk, created = chunk_memory_service.save_chunk(
            **request.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ChunkMemoryUpsertResponse(chunk=chunk, created=created)


@router.post(
    "/chunks/memory/from-lesson", response_model=StoreLessonChunksResponse
)
def save_lesson_chunk_memory(request: StoreLessonMemoryRequest):
    added = 0
    for sentence in request.lesson.sentences:
        for chunk in sentence.chunks:
            if not chunk.text.strip():
                continue
            _, created = chunk_memory_service.save_chunk(
                user_id=request.user_id,
                text=chunk.text,
                meaning=chunk.meaning,
                language=request.lesson.source_language,
                source_lesson_id=request.lesson.id,
                source_media_id=request.source_media_id,
                source_sentence_id=sentence.id,
                source_stage="lesson",
            )
            added += int(created)
    total = len(chunk_memory_service.list_chunks(request.user_id))
    return StoreLessonChunksResponse(added=added, total=total)


@router.get("/chunks/memory", response_model=ChunkMemoryListResponse)
def list_chunk_memory(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    language: SourceLanguage | None = None,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    chunks = chunk_memory_service.list_chunks(
        user_id,
        language,
        include_archived=include_archived,
    )
    return ChunkMemoryListResponse(chunks=chunks[offset : offset + limit])


@router.get("/chunks/memory/active-recall", response_model=ActiveRecallResponse)
def select_active_recall_memory(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    language: SourceLanguage,
    current_lesson_id: Annotated[
        str | None, Query(min_length=1, max_length=128)
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=MAX_ACTIVE_RECALL_LIMIT)
    ] = DEFAULT_ACTIVE_RECALL_LIMIT,
):
    chunks = chunk_memory_service.select_for_active_recall(
        user_id,
        language,
        current_lesson_id=current_lesson_id,
        limit=limit,
    )
    return ActiveRecallResponse(chunks=chunks)


def _memory_or_404(operation: Callable[[], StoredChunk]) -> StoredChunk:
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chunk memory not found") from exc


@router.patch(
    "/chunks/memory/{chunk_id}/practice", response_model=StoredChunk
)
def record_chunk_practice(chunk_id: str, request: ChunkPracticeRequest):
    return _memory_or_404(
        lambda: chunk_memory_service.record_practice(
            request.user_id, chunk_id, request.outcome
        )
    )


@router.patch("/chunks/memory/{chunk_id}", response_model=StoredChunk)
def set_chunk_memory_active(chunk_id: str, request: ChunkActiveRequest):
    return _memory_or_404(
        lambda: chunk_memory_service.set_active(
            request.user_id, chunk_id, request.active
        )
    )

