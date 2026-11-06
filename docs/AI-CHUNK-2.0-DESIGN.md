# AI Chunk 2.0: full-transcript curation and conversational transfer (design only)

> **Superseded (2026-11-06).** Codex returned DESIGN_CHANGES_REQUIRED on this
> design. See `docs/ai-chunk-2/CH-01-02-DESIGN-REVISION.md` and the documents
> beside it. This file is kept for history; its ranking and tie rules in
> particular are replaced.

**Status:** design for Owner review, 2026-11-06. Nothing in this document is
implemented. CurationJob and real providers need separate approval. The
evidence comes from the CH-00 audit; Step 1 is in
`CH-01-STEP1-CHUNK-HONESTY.md`.

## 0. Principles

1. **The lesson finishes first.** Curation never runs inside
   `/api/lesson/from-url`. It is a separate job started after the lesson is
   saved, and it can be resumed.
2. **Reuse what exists.** Candidates, decisions, Study Bank de-duplication,
   provenance append, SRS protection, Chunk Memory sync and the Speaking /
   Topic Mock infrastructure are reused. There is no second Study Bank and no
   new dialogue engine.
3. **Honesty over volume.**
   - Coverage is always reported, and partial results are labelled
     `partial`.
   - Fewer than 8 good chunks is a valid result, and nothing is padded.
   - A mock is never presented as a model.
4. **Position-blind ranking.** No step uses sentence position as a feature or
   as a tie-breaker, and the inputs to every comparison are ordered
   position-independently (§3.4).

## 1. CurationJob state machine

```
             create (idempotent)
                   │
                   ▼
   ┌────────── pending ──────────┐
   │               │ lease       │ cancel
   │               ▼             ▼
   │          processing ─────► cancelled
   │   window done │  ▲ lease expired → back to pending (attempt+1)
   │               ▼  │
   │        (all windows settled)
   │               │
   │      ┌────────┼─────────┐
   │      ▼        ▼         ▼
   │    ready   partial    failed
   │               │ retry   │ retry
   └───────────────┴─────────┘  (only unsettled windows run again)
```

- **Identity and idempotency.** One job per `(owner, library_item_id,
  transcript_hash, curation_config_hash, provider_id, provider_version)`.
  Creating the same job again returns the existing one:
  - while it is pending or processing, the same job;
  - once ready, the cached batch, with **no AI request**.
  - A new transcript, config or provider version means a new job.
- **States.**
  - `processing`: windows run under a lease with a heartbeat, the same
    pattern as the B-1 cleanup worker. An expired lease returns the job to
    `pending`, with `attempts + 1` and a cap of 3.
  - `ready`: every window succeeded.
  - `partial`: at least one window succeeded and at least one was skipped
    or failed. It always carries reasons.
  - `failed`: no window succeeded, or the budget was refused before the
    first window.
- **Retry.** Only `failed` and `skipped (budget)` windows run again. Done
  windows are never re-billed.
- **Window rows** (`pending | done | failed | skipped`) record:
  - the sentence range and token estimate;
  - attempts, an error code and a provider request id when available;
  - the candidates proposed.

  This makes recovery after a crash exact.

## 2. Full-transcript candidate discovery (Map)

### 2.1 Windowing adapted to sentence length, tokens and model context

Windows are built by **token budget, not sentence count**. "About 40
sentences" is only the starting test value.

```
input_budget = min(provider.context_tokens × 0.5, config.max_window_tokens)
               − prompt_overhead_tokens − provider.max_output_tokens
```

- **Estimating tokens.** The provider tokenizer is used when the adapter
  exposes one. Otherwise a conservative estimate applies: `ceil(chars / 3.2)`
  for EN and `ceil(chars / 2.8)` for DE, because German compounds tokenise
  longer. The estimate is checked by tests against fixed synthetic strings.
- **Building windows.** Whole sentences are added greedily until the next one
  would exceed `input_budget`, so a window never splits a sentence across two
  windows.
  - Short-sentence podcasts get more sentences per window and long-sentence
    lectures get fewer.
  - A floor of 8 sentences per window (when they fit) avoids paying for tiny
    windows.
- **Context overlap.** The last sentence of the previous window is repeated
  as **context only**, marked `context: true`. Candidates cannot cite it, so
  the overlap never produces duplicate evidence.
- **Oversized sentences.** A single sentence larger than `input_budget` (a
  bad subtitle merge) is split at clause punctuation into sub-segments that
  keep the sentence ID. If that is still too large, the window is `skipped`
  with reason `sentence_exceeds_window`, and the job becomes `partial`.
- **Job budget.** If the whole transcript needs more windows than
  `config.max_windows_per_job`, the job does **not** truncate the tail. The
  remaining windows wait as `skipped (budget)` and the result is `partial`,
  for example "analysed 240/400 sentences; 160 skipped: job budget". The
  learner can retry them once the budget allows.

### 2.2 What each window returns

The output is strict JSON, validated against a schema with `extra=forbid`.
Each candidate has:

| Field | Rule |
|---|---|
| `text` | ≤ 120 chars; must be grounded in the cited sentence (`_ground_phrase`) |
| `evidence_sentence_id` | must be a non-context sentence of this window |
| `meaning_zh`, `usage_note`, `reason` | bounded strings |
| `conversational_uses[]` | ≤ 3 short scenario labels |
| `scores` | integers 0–3 per dimension (§3.2) |
| `variations[]` | ≤ 2 AI-written alternative uses, `origin = ai_variation` |

The map step only **discovers** candidates; window scores are provisional.

## 3. Global de-duplication, ranking and count (Reduce)

### 3.1 Validation and de-duplication

1. **Grounding.** A candidate whose text is not found in its cited sentence
   is dropped and counted (`dropped_ungrounded`).
2. **De-duplication.** Candidates are normalised with the existing
   `normalized_identity` and merged. All evidence is kept, up to 5 sources,
   and the scores of the merged copies are averaged.
3. **Already saved.** A match against the owner's active Study Bank chunks
   sets `existing_study_item_id`, using the existing hint logic, and lowers
   the novelty score.

### 3.2 Scoring

All scores are coarse (0–3), so there is no false precision.

| Dimension | Weight | Why |
|---|---|---|
| Everyday reusability | 0.30 | The goal is transfer to the learner's own conversations |
| Naturalness and pragmatic value | 0.25 | The learner should sound natural, not merely correct |
| Transfer potential to other scenarios | 0.20 | It feeds CH-03 dialogue practice |
| Level fit (B1/B2/C1 vs `lesson.level`) | 0.15 | Too easy wastes the learner's time; too hard won't stick |
| Novelty vs the existing Chunk Bank | 0.10 | Avoid re-teaching what is already saved; low weight because review of known items still has value |

- **Penalties** (subtract 1, floor 0):
  - the chunk depends on a proper noun from the source;
  - it is a single word;
  - it is a fragment with no standalone use;
  - its grammar is unnatural.
- **Configuration.** The weights live in the curation config and are
  included in `curation_config_hash`, so changing them never silently
  rewrites cached results.

### 3.3 Global re-rank (comparability across windows)

Window scores drift, because different calls are calibrated differently. So
the top candidates go into one compact **comparative re-rank** call:

- the input is at most 30 finalists, each as its text plus its source
  sentence;
- they are re-scored on the same 0–3 scale against each other;
- one call is enough because the finalists are short.

### 3.4 Position fairness

LLMs favour items listed first (primacy bias). To neutralise it:

- **Finalist order** in the re-rank prompt is a deterministic shuffle by
  `sha256(normalized_text + job_id)`. It never follows transcript order.
- **Tie-break order** is `(final_score desc, normalized_text)`, never sentence
  position.
- **Merge order** in the reduce step keeps evidence in sentence order for
  display only; display order is never used for ranking.

### 3.5 How many chunks to recommend

- `K = min(12, number of candidates with final_score ≥ quality_floor)`, with
  a default `quality_floor` of 1.8 out of 3.
- **Never padded.** If only 5 candidates pass, the result is 5.
- Below 8, the batch carries `reason: "few_high_quality_candidates"` so the
  UI can say so. 0 is a valid `ready` result.

## 4. Per-chunk source evidence

Every `source_extracted` candidate carries:

- the chunk text;
- the source sentence text and sentence ID;
- `start` / `end`, or null when there are no timestamps (the UI then shows
  no play button);
- the source language;
- `library_item_id`, `lesson_id` and the source URL;
- `meaning_zh`, `recommendation_reason` and `conversational_uses`;
- `verification`: `grounded`.

Most of this already exists in `CandidateEvidence`.

- **AI-written variations** live in `variations[]` under their source
  candidate and are labelled "AI example, not from the source". They cannot
  be accepted as a source chunk, and accepting a candidate never stores a
  variation as provenance.

## 5. Partial analysis and coverage

Every job and batch exposes:

`coverage = {total_sentences, processed_sentences, skipped_ranges[{from, to, reason}], failed_windows, dropped_ungrounded}`

- **Skip reasons:**
  - `budget`;
  - `sentence_exceeds_window`;
  - `provider_timeout`;
  - `provider_invalid_output`;
  - `no_timestamps` (informational only).
- **UI.** It always shows "Analysed N/M sentences". On `partial` it lists the
  skipped ranges and offers "Retry skipped parts".

## 6. Prompt injection and output validation

- **Separation.**
  - The system message holds the instructions.
  - The transcript goes in a data block fenced by a per-request random
    boundary, `<<TRANSCRIPT-{nonce}>> … <<END-{nonce}>>`.
  - The instructions state that the content inside the boundary is quoted
    material and never instructions.
- **Strict output.**
  - JSON schema, `extra=forbid`, bounded lengths and a bounded item count.
  - Every candidate must cite a sentence ID from the window and pass
    grounding. Anything else is dropped and counted, never repaired by
    guessing.
- **No escalation.** No tool calls and no URLs fetched. The provider only
  sees text, and its output is only stored as data.
- **Tests.** Transcripts containing "ignore previous instructions", fake
  JSON and boundary-lookalike strings must still give schema-valid,
  grounded output. A stub provider that "obeys" the injection must have its
  output dropped.

## 7. Provider abstraction, cost and timeouts

The existing `ChunkModelAdapter` gains:

- `mode`, `context_tokens` and `max_output_tokens`;
- `estimate_tokens(text)`;
- `propose_window(...)` and `rerank(...)`.

The `DeterministicMockChunkAdapter` stays the default.

- **`LlmChunkAdapter`** reuses `services/ai/llm_client.py`, the existing
  provider switch and key handling, which stays backend-only.
  - It is **off by default** and enabled only when `AI_CHUNK_PROVIDER=llm`
    and an explicit `AI_CHUNK_PAID_CALLS_AUTHORIZED=true` are set.
  - No test, CI run or default configuration sets either.
- **Cost limits:**
  - `max_windows_per_job` (default 12);
  - `max_tokens_per_job`;
  - a per-owner daily job cap (default 5);
  - a cost estimate shown before a paid job starts.
- **Timeouts and retries:**
  - 60 s per call;
  - at most 2 retries with exponential back-off, only for timeouts, 429 and
    5xx;
  - no retry on invalid output (it is counted, and the window fails).
- **Concurrency.** A global semaphore of 2 calls, one running job per owner,
  and a circuit breaker after 5 consecutive provider failures.

## 8. Persistence, idempotency, retry and recovery

- **Cloud.** A new migration (proposed as `0013_chunk_curation_jobs`), owner
  scoped with forced RLS:
  - `cloud_chunk_curation_jobs`
  - `cloud_chunk_curation_windows`
  - the final candidates go into the **existing** `0003` batch and candidate
    tables, with `source_type = 'library_curation'`, plus nullable columns
    `proposal_mode`, `origin`, `scores` and `coverage`.

  The down migration refuses while rows exist, the same pattern as 0011 and
  0012.
- **Personal.** Organizer batches are in memory today (CH-00 L-1). The
  proposal is a JSON store with atomic writes, the same as the existing
  Personal Library and Study Bank stores. This is an Owner decision.
- **Idempotency.** Job identity is defined in §1. Decisions keep their
  existing idempotency keys and version 409s.
- **Recovery.** Window rows survive a crash, and a lease that expires
  resumes at the first window that is not `done`.

## 9. Reused decision and Study Bank interfaces

No contract changes here:

- `POST /api/ai/chunk-suggestions/{batch}/decisions` with accept,
  edit_and_accept, edit (Cloud) and reject;
- `save_items` de-duplication;
- meaning, note, archive and SRS are never reset;
- provenance from other videos is appended.

**New endpoints:**

- `POST /api/ai/chunk-curations {user_id, library_item_id}`: idempotent;
  returns the job.
- `GET /api/ai/chunk-curations/{job_id}`: returns the state, coverage and,
  once the job is ready or partial, the `batch_id`.
- `POST /api/ai/chunk-curations/{job_id}/retry`: re-runs the unsettled
  windows only.

**UI: a "Recommended Chunks" panel** on the lesson and My Library detail
page. It reuses `AiChunkOrganizer`'s decision code and shows:

- the state chip;
- "Analysed N/M";
- for each chunk: meaning, source sentence, a timestamp with play, the
  reason, uses, the saved state, Accept / Edit / Reject, and "Practice in
  Speaking".

## 10. Training interface (CH-03, design only)

`SpeakingContextRequest` already has `target_ids` (≤ 8) and `target_limit`,
and `_select_chunks` already prioritises again / hard / due items. The
proposal is:

- 3–5 newly accepted chunks are passed as `target_ids`.
- A new `recall_limit` (0–5) adds old chunks through the existing priority.
  The total is bounded at 8, the current `target_limit` cap. Raising it to 10
  is an Owner decision.
- The practice order stays the existing one: Active Recall → Listening →
  Shadowing → Retell → Speaking or Topic Mock.
- The existing real and mock Speaking providers and the learner-turn usage
  evidence (`target_usage`) are reused.
- Speaking evidence **never** becomes an SRS rating.
- New expressions found in conversation become candidates that need the
  learner's review, never direct Study Bank writes.
- **R3 (Live Dialogue).** Once curation exists, Live Dialogue could take the
  lesson's curated ranking instead of transcript order. This is a separate
  Owner decision, because it changes accepted behaviour.

## 11. Proof of position fairness (synthetic EN/DE)

The tests use stub providers. The mock is not used because it is a fixed list.

1. **Permutation invariance.** One set of high-quality chunks is placed at
   the start, the middle and the end of a 400-sentence EN and DE transcript,
   across all 6 orderings. The selected set is identical every time, and so
   is the ranking up to the hash shuffle.
2. **A late chunk beats early noise.** Low-quality candidates sit in sentences
   1–20 and the strong ones in 300–400. The strong ones rank first.
3. **A primacy-biased stub.** A stub re-ranker that inflates the scores of
   whatever is listed first still yields a selection that does not depend on
   transcript position, which proves the shuffle in §3.4 is effective.
4. **Coverage.** `processed_sentences == total` for every ready job. A forced
   budget gives `partial` with exact skipped ranges.
5. **No fixed quota per section.** No test asserts "N per window". Fairness
   is proven only by invariance and quality ordering.

## 12. Risks

| Level | Risk | Mitigation |
|---|---|---|
| High | Paid provider cost or runaway calls | Off by default; explicit authorisation flag; per-job and per-day caps; cached results; no retry on invalid output |
| High | Injection through transcripts | Boundary fencing; strict schema; grounding; drop-and-count |
| Medium | Window score drift and primacy bias | Global re-rank with hash-shuffled finalists; permutation tests |
| Medium | Token estimate wrong for a provider | Conservative ratios; provider tokenizer when available; 50% context headroom |
| Medium | Mode not persisted (Step 1 labels at response time) | Step 2 adds a persisted `proposal_mode` column before any real provider ships |
| Medium | Stacked Drafts #81–#85 are not merged | Cloud work stacks on them; the Personal store can start from `main` |
| Low | Recommendation counts below 8 surprise learners | Explicit "few high-quality candidates" reason |
| Low | Lesson generation over 5 minutes is not yet timed | Out of scope; propose timing instrumentation as a separate task |

## 13. Owner decisions needed before implementing Step 2

1. Approve the CurationJob, window rows and migration `0013` for Cloud.
2. Decide whether Personal Organizer batches move to a persistent JSON store.
3. Decide on real provider work: is `LlmChunkAdapter` behind both flags
   approved, and what are the budget defaults?
4. Approve the scoring weights, a `quality_floor` of 1.8 and K ≤ 12.
5. CH-03: add `recall_limit`, and keep the cap of 8 or raise it to 10?
6. R3: should Live Dialogue later use the curated ranking?
