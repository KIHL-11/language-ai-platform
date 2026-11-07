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
| Batch revision (Round 3) | none | `curation_job_id`, `batch_revision` (unique per job), status `superseded` (in addition to ready and expired); decisions on a superseded batch get 409 `batch_superseded`; old revisions stay readable | null; existing batches are never superseded |
| Inherited decision (Round 3) | none | `inherited_decision: {batch_id, candidate_id, state}` on a candidate in a later revision (informational); recommendation status `previously_rejected` | null |

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

## 4. Rollback safety: persistence projection and two-phase rollout (Round 3, M4)

### 4.1 Why "optional" is not enough

All candidate models use `extra="forbid"`. Pydantic's
`model_dump(mode="json")` **includes fields whose value is None**, so a new
model with only optional additions still writes keys an old server rejects.
Cloud stores whole decision responses in
`cloud_ai_chunk_decisions.response_json` and replays them, so one such record
breaks replay after a rollback. This is demonstrated by
`test_the_hazard_is_real_a_plain_dump_of_new_fields_breaks_the_old_server`.

### 4.2 Two kinds of output

| Output | Who reads it | Phase A rule |
|---|---|---|
| **API response**: `GET` batch, curation status | browsers only; never stored | may include new fields (computed); old clients ignore them |
| **Persisted record**: decision `response_json`, Personal JSON store, any JSON column | servers of every version still in the rollback window | **old-schema keys only**, via the persistence projection |
| **Decision API response** (first call and replay) | browsers; Cloud replays the stored record | in Phase A it **is** the projected record, so the first call and a replay are identical |

### 4.3 The projection (executable model: `backend/chunk_curation_lab/compat.py`)

- **`persisted_decision(response)`** keeps exactly the keys of the old models:
  - `CandidateDecisionResponse`;
  - the nested `AiChunkCandidate`;
  - the nested `CandidateEvidence`.

  It also projects nested StudyItem, ReviewState and every provenance
  record. It validates the result with the **frozen Legacy V1** model, so it
  fails closed instead of writing a record an old server cannot load.
- **`persisted_batch(batch)`** does the same for batches. It also:
  - drops the computed `proposal_mode`, which is never persisted (CH-01
    Step 1);
  - **refuses** to persist more than 8 candidates, because the old
    `AiChunkBatch` limit is 8. A 12-candidate curated batch therefore cannot
    exist in storage until Phase B.
- **Frozen Legacy V1 (Codex F2).** The old reader is
  `backend/chunk_curation_lab/legacy_v1.py`, a static transitive copy of the
  contract and validators at `19a88301f108d2204576380ea2b8fcc111a9f51f`.
  It imports no production model. The whitelist is derived only from these
  **frozen** models, never from the current production classes. V2 may evolve
  without silently widening the rollback contract. Introduce a new explicit
  version instead of refreshing V1.
  - Projection covers the envelope, candidate, evidence, StudyItem,
    ReviewState and media/speaking/ai_text provenance records.
  - Unknown provenance discriminators fail closed; they are not relabelled.
  - The committed synthetic fixture
    `backend/tests/fixtures/ai_chunk_legacy_v1.json` fixes historical V1
    accept/edit_and_accept/reject/edit records and a batch. Tests read these
    through V1 and V2, then project back to exactly the same JSON values.
  - Future-field tests simulate live models gaining optional keys and nested
    StudyItem/review/provenance additions. None may leak into V1 output.
    The 8-candidate limit and old source-type enum remain enforced.
  - This is an isolated compatibility experiment, not a two-server rollout
    or a change to production serializers.

### 4.4 Phases, deploy order and rollback

| Step | Code | Persisted format | Rollback to the previous step |
|---|---|---|---|
| A0 (today) | current models | old | — |
| **A** | read-tolerant V2 models (optional fields with truthful defaults; batch limit 12 on **read**); every write goes through the projection | **old** (projected) | **Safe**: A never persisted a new key |
| A→B gate | A deployed on **every** server, and the rollback floor declared as A (no older build may be redeployed) | — | — |
| **B** | the projection is lifted for new records; curation may persist new fields and up to 12 candidates | new keys allowed | **Safe to A** (A reads them); **unsafe below A**, which the gate forbids |

- The Cloud migration that adds columns (§5) lands **with A**, nullable or
  with defaults.
- Writes to those columns start only in B.

### 4.5 Historical replay never rewrites decisions

- A replay returns the **stored** record as stored. It is never re-rendered
  through newer models and never upgraded in place.
- A new server reading an old record fills the defaults for its own use
  only. It does not write the defaults back.
- Proven, for accept, edit_and_accept and reject, by
  `test_replay_round_trips_without_rewriting_the_decision`. The record
  passes old → new read → Phase A persist and comes back byte-identical,
  with the same state, version and accepted Study Item.
- The edit action is covered by `test_edit_only_responses_round_trip_too`.

### 4.6 Executable compatibility tests (Round 3)

These are in `backend/tests/test_chunk_curation_compat.py`, all executed. They
use real Personal organizer responses on temporary synthetic stores.

| Requirement | Test |
|---|---|
| New writes in the compatible format and the old server reads it | `test_new_server_phase_a_records_load_on_the_old_server` (×3 actions) |
| The old server writes and the new server reads | `test_old_server_records_load_on_the_new_server_with_truthful_defaults` (×3) |
| A new field that is None does not leak into the old format | `test_the_hazard_is_real_…` (plain dump fails) and the Phase A test (no extra keys at any level) |
| Accept, Edit and Reject replays do not change the original decision | `test_replay_round_trips_without_rewriting_the_decision` (×3) and `test_edit_only_responses_round_trip_too` |
| Saved candidate, Study Bank, archive and SRS are unchanged | `test_projection_preserves_archive_note_meaning_and_srs_exactly` (a duplicate accept onto an archived card with a note, a manual meaning and a rated ReviewState: the record is byte-equal and the store is unchanged) |
| Batch projection | `test_phase_a_batches_drop_new_and_computed_fields_and_refuse_oversize` |

**NOT EXECUTED** (needs production code, which is not authorised):

- the projection wired into `ai_chunks/postgres.py` and
  `InMemoryAiChunkStore`;
- a two-version deploy test that runs the old and new servers against one
  database.

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
    JSONB`, `curation_job_id UUID`, `batch_revision INT` and
    `status_reason`, plus `UNIQUE (owner_user_id, curation_job_id,
    batch_revision)`. The source-type CHECK gains `library_curation` and the
    status CHECK gains `superseded`.
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
