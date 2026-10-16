# Persistent chunk memory and local semantic retrieval

This module provides persistent, user-scoped memory for historical
spoken-language chunks plus the existing optional semantic search.

## Architecture

- `chunk_store.py` persists validated `StoredChunk` records in
  `backend/storage/chunk_bank.json`. Writes replace the file atomically and
  records are deduplicated per user, language, and normalized text.
- `chunk_memory.py` owns save/upsert, list, practice updates, archive state,
  and deterministic Active Recall selection.
- `embedder.py` lazily loads the CPU-compatible
  `sentence-transformers/all-MiniLM-L6-v2` model. No OpenAI API is used.
- `retriever.py` filters chunks by source language, embeds the query and source
  chunk text, computes cosine similarity with NumPy, and returns the top scores.
- `services/api/chunk_api.py` exposes the module through the existing FastAPI
  `/api` namespace.

The first real search downloads the model from Hugging Face if it is not
already cached. Later searches use the local cache and consume no LLM tokens.

## Storage format

The JSON file contains a version and a `chunks` array. Version 1 records remain
readable and the next write upgrades the file to version 2. Each record stores:

- `id`
- `user_id`
- `text`
- `normalized_text`
- `meaning`
- `source_language`
- `source_lesson_id`
- optional source media, sentence, and stage context
- creation, update, and last-practiced timestamps
- practice, success, and failure counters
- a bounded `mastery_score` from 0 to 1
- active/archive state

## Save and deduplication

Before persistence, chunk text receives one standard-library HTML entity decode,
Unicode NFKC normalization, collapsed whitespace, and conservative removal of
cue-boundary `>>`/music-note markers and recognized caption annotations such as
`[music]`, `[applause]`, and `[laughter]`. Empty, symbol-only, and repeated pure
interjection fragments are rejected. Direct memory saves return HTTP 422 for
rejected text; lesson imports skip rejected chunks and continue with valid ones.

The cleaned text is then normalized with ignored terminal sentence punctuation.
English is case-folded. German case is preserved because capitalization can be
meaningful. The deduplication key is
`user_id + source_language + normalized_text`. An upsert keeps the original ID
and creation provenance, while allowing a non-empty meaning to be refreshed.

Existing banks are never rewritten automatically. To prepare a reviewed cleaned
copy, run the explicit utility from `backend` with different source and output
paths:

```bash
.venv/bin/python -m scripts.cleanup_chunk_memory storage/chunk_bank.json /tmp/chunk_bank.cleaned.json
```

The source is never overwritten, practice/mastery/timestamp fields are preserved,
and records that would collide after sanitation remain unchanged in the output
and are reported under `collision_ids` for manual resolution.

## Active Recall selection

`ChunkMemoryService.select_for_active_recall` filters by user and language,
excludes archived records and (when provided) the current lesson, then orders
deterministically by lower mastery, lower success rate, previous failures, and
older or missing practice timestamps. The default is five records and the API
limit is capped at 20. This is a lightweight priority rule, not a spaced-
repetition scheduler.

Practice outcomes update the record as follows:

- `success`: increment practice and success counts; add 0.10 mastery.
- `partial`: increment practice count; add 0.02 mastery.
- `failure`: increment practice and failure counts; subtract 0.10 mastery.

Mastery is always clamped to 0–1.

## Chunk memory API

- `POST /api/chunks/memory` saves or upserts one chunk.
- `POST /api/chunks/memory/from-lesson` saves the chunks in a Lesson payload.
- `GET /api/chunks/memory` lists a user's memory with bounded pagination.
- `GET /api/chunks/memory/active-recall` selects prior chunks for warm-up.
- `PATCH /api/chunks/memory/{id}/practice` records a practice outcome.
- `PATCH /api/chunks/memory/{id}` changes active/archive state.

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

## Known limitations

- The application has no authentication or profile layer yet, so callers must
  supply an opaque `user_id`; this is logical isolation, not authorization.
- JSON writes are atomic and protected within one process, but this store is not
  intended for multiple backend processes writing concurrently.
- Frontend cross-lesson presentation is deferred until a user identity/session
  contract exists. Current training stages are unchanged.
- Advanced spaced repetition (SM-2, FSRS, due dates, and intervals) is outside
  M3.5 scope.
