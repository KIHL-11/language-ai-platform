# Candidate compatibility contract (CH-01/02 design revision)

**Design only. No migration was run or written.** The baseline is the
schema on `claude/p8-b3-cloud-dictionary-chunks` (#83) plus CH-01 Step 1:

- `backend/services/schemas/ai_chunks.py`
- Cloud migrations `0003` and `0011`
- `ai_chunks/postgres.py`

## 1. Field contract

| Concept | Today | AI Chunk 2.0 | Default for existing data |
|---|---|---|---|
| Candidate identity | `candidate_id` (server UUID or hex) | unchanged | — |
| Batch identity | `batch_id` | unchanged; a curated batch also links `curation_job_id` | null |
| Normalized chunk identity | `normalized_identity` = Study Bank `normalized_text` (`prepare_chunk_text`; EN case-folded, DE case-kept) | unchanged, and the **only** de-duplication key | — |
| Generation version | `proposal_model`, `proposal_version` (Step 1 made the mock `2`) | plus `scorer_id`, `scorer_version` and `curation_config_hash` on the batch | null, which means legacy |
| Candidate revision | `version` (≥ 1; +1 per decision or saved edit; stale → 409) | unchanged | — |
| Idempotency | generation `idempotency_key`; decision `idempotency_key` with fingerprint | unchanged; plus the job identity tuple (see the state machine doc) | — |
| Source provenance | `evidence`: one `CandidateEvidence` (snapshot fields with the grounded span) | plus `transcript_revision`, `sentence_fingerprint`, and `additional_sources[]` (≤ 5, same shape, server-built) | null and `[]` |
| Origin | implicit: every candidate is grounded | `origin: "source_extracted"`, the only value a candidate can have; `variations[]` each `{text, origin: "ai_variation"}` | `"source_extracted"` (true for all existing rows, which were grounded at creation) |
| Verification status | implicit: grounded at creation; Cloud re-checks on accept | `verification`: `grounded` \| `manual_text` \| `revalidated` \| `invalid` with `invalid_reason` | `grounded` for library evidence, `manual_text` for manual |
| Recommendation score | none | `recommendation: {status, score: "2.71" (decimal string) \| null, dimensions{8}, weight_coverage, tied_with[]}` | null, which means **legacy and unscored**, never "recommended" |
| Rationale | `explanation_of_value`, `usage_note`, `quality_flags` | unchanged, plus `recommendation.reasons[]` (codes) | `[]` |
| Decision state | `state` ∈ proposed, accepted, edited_and_accepted, rejected, expired | unchanged | — |
| Expiry or invalidation | batch `expires_at` and `status`; expired is terminal; Cloud accept re-checks the Library (409) | unchanged, plus an explicit re-validation result on read (`verification`) and a 409 code `candidate_source_changed` on accept | — |
| Accepted Study Item | `accepted_study_item_id`, `existing_study_item_id/active` | unchanged | — |
| Batch extras | `proposal_mode` (Step 1, computed, not stored) | stored `proposal_mode`, `coverage{…}`, `status_reason`, and `source_type: "library_curation"` | computed as today |

## 2. Invariants that must not change

1. **Decision routes and actions.** Accept, edit_and_accept, edit (Cloud
   only) and reject keep their routes, request schema and states.
2. **Study Bank de-duplication.** `save_items` and
   `save_item_in_transaction` keep their semantics. An existing item keeps
   its meaning, note, archive state, ReviewState and SRS fields, and the new
   provenance is appended.
   - Covered by the existing tests
     `test_duplicate_accept_preserves_srs_fields_archive_meaning_and_note`
     and `test_duplicate_preserves_review_and_archived_state`.
3. **Reject has no side effects.** `test_reject_has_no_study_or_review_side_effect`.
4. **Immutable evidence.** A decision can never change the source evidence
   (the immutability check in `postgres.decide_atomic`).
5. **Accept re-validates the source on the server.**
   - Cloud already does this in `_validate_library_evidence`, under a
     `FOR KEY SHARE` lock on the Library row.
   - **Gap: Personal does not.** Personal `_apply_decision` only re-grounds
     the text against the stored quote. It must get the same re-validation
     before Personal curation ships (risk R-H3).
6. **Nothing is accepted automatically.** Curation only proposes.

## 3. Old client and new server

- **Responses gain optional fields only.**
  - The current frontend reads responses with plain casts
    (`api/aiChunks.ts`) and ignores unknown fields.
  - The older `AiChunkOrganizer` keeps working, including Step 1's
    `proposal_mode`.
- **Old clients never call the new routes** (`/ai/chunk-curations*`), and
  the existing request schemas are unchanged, so an old client cannot reach
  curation.
- **Batch size.** A curated batch may have up to 12 candidates. The Pydantic
  `AiChunkBatch.candidates` limit (`max_length=8`) must rise to 12. The
  database has no count constraint (only a unique `ordinal`). The UI already
  maps over any length.
- **New source type.** `source_type: "library_curation"` needs the Cloud
  CHECK `cloud_ai_batch_source_type` extended. An old client shows it with
  the default library rendering.

## 4. New server and old server (rollback hazard, two-phase rollout)

All models use `extra="forbid"`, and Cloud stores full decision responses
in `cloud_ai_chunk_decisions.response_json` for replay. Once a new server has
written a response containing new fields, an **older** server would fail to
replay it. So:

- **Phase A, read-tolerant.** Ship model changes that only add **optional**
  fields with the defaults above, and raise the candidate limit to 12. No
  writer emits the new fields yet. Rolling back from A is safe.
- **Phase B, writers.** Curation starts writing the new fields. Rolling
  back below A is then unsupported. Rolling back to A is safe.

Personal stores follow the same order through a `schema_version` in the
file.

## 5. Migration strategy (proposed, not executed)

- **Cloud: one migration after the #81–#85 stack** (`0013` or the next free
  number), adding only:
  - **Candidates.** `cloud_ai_chunk_candidates` gains `origin TEXT NOT NULL
    DEFAULT 'source_extracted' CHECK (origin = 'source_extracted')`,
    `verification TEXT NOT NULL DEFAULT 'grounded'`, `invalid_reason TEXT`,
    `recommendation JSONB`, and `variations JSONB NOT NULL DEFAULT '[]'`.
  - **Evidence.** `cloud_ai_chunk_evidence` gains `transcript_revision TEXT`
    and `sentence_fingerprint TEXT`.
  - **Additional sources.** A new table
    `cloud_ai_chunk_candidate_sources (owner, candidate_id, ordinal, …)`
    with forced RLS and an owner policy.
  - **Batches.** `cloud_ai_chunk_batches` gains `proposal_mode`, `coverage
    JSONB`, `curation_job_id UUID` and `status_reason`, and the source-type
    CHECK gains `library_curation`.
  - **Jobs.** The job tables, which are defined in
    CURATION-JOB-STATE-MACHINE.md.
- **Backfill.** None. The defaults describe existing rows truthfully.
- **Down migration.** It refuses while any curated batch, job, additional
  source or non-default value exists, the same pattern as 0011 and 0012. It
  never deletes data silently.
- **Personal.** No database. See the Personal section of
  CH-01-02-DESIGN-REVISION.md.

## 6. Error contract additions

| Situation | HTTP | `detail.code` |
|---|---|---|
| Source changed or deleted at accept | 409 | `candidate_source_changed` (today the message is "no longer matches"; keep the message and add the code) |
| Batch expired | 409 | existing behaviour ("has expired") |
| Unscored batch | 200 | `status: "unscored"`; no recommendations; never an error |
| Curation job for another owner | 404 | the same shape as missing, non-enumerating, as `test_cross_tenant_api_batch_ids_match_missing_response` already requires for batches |
