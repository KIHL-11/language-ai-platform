# Local semantic chunk retrieval

This module provides a small, independent retrieval service for historical
spoken-language chunks. It is intentionally not connected to lesson generation
or any lesson prompt yet.

## Architecture

- `chunk_store.py` persists validated `StoredChunk` records in
  `backend/storage/chunk_bank.json`. Writes replace the file atomically and
  repeated imports of the same lesson sentence are deduplicated.
- `embedder.py` lazily loads the CPU-compatible
  `sentence-transformers/all-MiniLM-L6-v2` model. No OpenAI API is used.
- `retriever.py` filters chunks by source language, embeds the query and source
  chunk text, computes cosine similarity with NumPy, and returns the top scores.
- `services/api/chunk_api.py` exposes the module through the existing FastAPI
  `/api` namespace.

The first real search downloads the model from Hugging Face if it is not
already cached. Later searches use the local cache and consume no LLM tokens.

## Storage format

The JSON file contains a version and a `chunks` array. Each record stores:

- `id`
- `text`
- `meaning`
- `source_language`
- `source_lesson_id`
- `source_sentence_id`
- `created_at`

Embeddings are generated at retrieval time. This keeps the file readable and
avoids stale vectors when the local model changes.

## Retrieval flow

1. Read and validate the chunk bank.
2. Filter records to the requested `source_language`.
3. Embed the query and candidate source text locally.
4. Compute cosine similarity.
5. Return at most `top_k` records in descending score order.

## API usage

Store chunks from an existing lesson payload:

```http
POST /api/chunks/from-lesson
Content-Type: application/json

{ ...a valid Lesson payload... }
```

Search stored English chunks:

```http
POST /api/chunks/search
Content-Type: application/json

{
  "query": "making plans with a friend",
  "source_language": "en",
  "top_k": 5
}
```

Each result includes the stored provenance fields plus a cosine similarity
`score`. Empty queries, unsupported languages, and non-positive `top_k` values
are rejected with HTTP 422.
