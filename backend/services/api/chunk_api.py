from fastapi import APIRouter

from services.retrieval.chunk_store import ChunkStore
from services.retrieval.retriever import ChunkRetriever, add_lesson_chunks
from services.schemas.lesson import Lesson
from services.schemas.retrieval import (
    ChunkSearchRequest,
    ChunkSearchResponse,
    StoreLessonChunksResponse,
)


router = APIRouter()
chunk_store = ChunkStore()
chunk_retriever = ChunkRetriever(chunk_store)


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
    )
    return ChunkSearchResponse(results=results)

