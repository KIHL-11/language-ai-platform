# CH-01/02 design revision (after Codex DESIGN_CHANGES_REQUIRED)

- **Date:** 2026-11-06.
- **Branch:** `claude/ch-01-02-design-revision`, stacked on CH-01 Step 1
  (`694e811`), which is stacked on #83.
- **Status:** design plus an isolated pure-algorithm slice. Full CH-01/CH-02
  is **not** implemented and **not** production ready.
- **Supersedes:** `docs/AI-CHUNK-2.0-DESIGN.md`. That document is kept for
  history and marked superseded.

## Round 3 (Codex CHANGES_REQUIRED on `ddda942`)

| ID | Fix | Where |
|---|---|---|
| M1 | Duplicate evidence keys are merged by the maximum verified reliability; one vote per distinct source (two-level lower median) | `reduce.py`, `scoring.merge_scores`; ranking spec §5 and §6.2 |
| M2 | Grounding stays inside the window's citable character ranges; new reason `outside_citable_range` | `provenance.py`; ranking spec §4 |
| M3 | Strict shape checks with no repair; a per-observation exception boundary (`verification_error`) | `provenance.shape_error`, `reduce.py`; ranking spec §4 |
| M4 | Phase A persistence projection and whitelist; an A→B gate; replays never re-rendered; executable tests | `compat.py`, `test_chunk_curation_compat.py`; contract §4 |
| M5 | Window status, attempts and checkpoints separated; immutable batch revisions with a current pointer; superseded batches; informational decision inheritance | state machine §1, §3 and §5 |
| M6 | Attempt ledger with an `uncertain` state; conservative accounting; re-approval; provider idempotency keys only when documented; "no duplicate billing" withdrawn | state machine §6; F1–F10 |

The CH-01 Step 1 Mediums stay **OPEN** (R-M10, R-M11). R-H3 is a launch gate.

## Documents

| # | Document | Owner item |
|---|---|---|
| 1 | this file | overview, provenance contract (3), Personal/Cloud boundary (6), security (7) |
| 2 | `CANDIDATE-COMPATIBILITY-CONTRACT.md` | 2 |
| 3 | `CURATION-JOB-STATE-MACHINE.md` | 5 |
| 4 | `FULL-TRANSCRIPT-RANKING-SPEC.md` | 1, 4 |
| 5 | `SYNTHETIC-VALIDATION-REPORT.md` | 8, 9 |
| 6 | `RISK-REGISTER-UPDATED.md` | risks |
| 7 | `CODEX-REREVIEW-HANDOFF.md` | re-review |

## 1. What changed from the first design

| Owner item | First design | Revision |
|---|---|---|
| 1. Quality | Five weights; "tie by `(score, normalized_text)`"; hash shuffle as a fairness device | Eight defined dimensions (two computed by the server), integer weights with rationale, lower-median aggregation, `unscored` when core signals are missing, gates, a floor, and quality-only rank keys. Exact ties are taken or excluded as a whole group, so the alphabet or a hash never decides inclusion |
| 2. Contract | Not defined | Field-by-field contract, defaults, old/new compatibility, a two-phase rollout for the `extra="forbid"` replay hazard, and migration steps (not run) |
| 3. Provenance | "Grounding" described | Server-only evidence from a snapshot; provider claims ignored and counted; variations can never be evidence; re-validation states that mirror the existing Cloud accept check; the Personal gap identified |
| 4. Coverage and reduce | Window and overlap sketched | Precise rules for segment-level coverage, oversized and unsplittable sentences, context that cannot be cited, and partial semantics. Order independence is argued and tested; merging multiple sources never uses first position |
| 5. Job | State diagram | Job key, lease token with fence, compare-and-swap writes, immutable done windows, budget reservation, cancellation, crash recovery, owner isolation, and a 14-test spec (NOT EXECUTED) |
| 6. Persistence | One sentence | Separate Personal and Cloud designs (§4 below) |
| 7. Security | Boundary fencing | Full threat handling (§5 below), with output validation implemented in the slice |
| 8. Slice | — | `backend/chunk_curation_lab/`, 35 tests, 19 of 19 mutants killed |

## 2. Trusted provenance contract (Owner item 3)

### Types

These are the slice types; production would use pydantic equivalents.

- **`LibrarySnapshot`**: `owner`, `library_item_id`, `lesson_id`,
  `language`, `sentences[(id, text, start, end)]`, and
  `transcript_revision`, a content hash. It is built only by the server from
  the owner's authorized Library row.
- **`ProviderObservation`** (untrusted): the text, cited sentence ID, window
  index, claimed origin, scores and variations, plus any **claimed** item,
  quote or timestamps.
- **`VerifiedEvidence`** (trusted): item, lesson, revision, sentence ID,
  text, fingerprint, start and end, the grounded span, and reliability.
  Every field comes from the snapshot.
- **Origin.** A candidate is always `source_extracted`. Variations are
  `{text, origin: "ai_variation"}` and are never evidence.

### Flow

1. **At generation.**
   - The server builds the snapshot from the owner's Library row; in Cloud
     this happens under RLS.
   - Windows are built from the snapshot.
   - `verify_observation` rejects anything not grounded in a sentence that
     was **citable in the proposing window**. Rejection reasons:
     `variation_not_source`, `invalid_scores`, `invalid_text`,
     `unknown_window`, `unknown_sentence`, `not_citable_in_window`,
     `ungrounded`.
   - Accepted evidence is copied from the snapshot. Claim mismatches are
     counted and never applied.
2. **At read** (optional, cheap). `revalidate(evidence, current_snapshot)`
   returns one of:

   | Result | Meaning |
   |---|---|
   | `valid` | Unchanged |
   | `revalidated` | Other sentences changed; this one is identical |
   | `sentence_changed` | Text or timing differs |
   | `sentence_missing` | The sentence is gone |
   | `lesson_replaced` | The lesson was replaced |
   | `source_deleted` | The Library item is gone |

   The UI marks everything except the first two as invalid and disables
   Accept.
3. **At accept** (authoritative).
   - **Cloud: the existing `_validate_library_evidence` is reused unchanged.**
     It runs in the decision transaction with a `FOR KEY SHARE` lock on the
     Library row and compares the lesson id, title, URL, languages, sentence
     text, translation and timestamps. A mismatch is a 409, which gains the
     code `candidate_source_changed`.
   - Additional sources are re-validated the same way. Invalid ones are
     dropped from the provenance written to the Study Bank, and the
     response warns about it.
   - **Personal: a gap.** `_apply_decision` re-grounds the text against the
     stored quote only. It must gain the same Library re-validation before
     Personal curation ships (R-H3).
4. **Library deletion or transcript replacement.** The old curated batch
   stays readable, but its evidence re-validates as invalid and Accept is
   refused with a 409. Nothing is deleted silently. A new curation needs a
   new job, because the revision is part of the job key.

### Errors

| Case | Result |
|---|---|
| Provider output invalid | Observation rejected and counted; the job continues |
| Every observation in a window invalid | The window is done, with zero candidates and its counts |
| Source changed at accept | 409 `candidate_source_changed` |
| Source deleted at accept | 409 `candidate_source_changed` ("no longer available") |

## 3. Implemented vs designed

- **Implemented, isolated:** windowing, the coverage manifest,
  verification and re-validation, identity (the production
  `prepare_chunk_text`), de-duplication, scoring and aggregation, stable
  ranking and tie groups, and source reconciliation, plus synthetic fixtures
  and tests.
- **Designed only:**
  - the provider adapter;
  - prompts;
  - the CurationJob tables, worker and routes;
  - the migrations;
  - the Personal store;
  - the UI panel;
  - the CH-03 hooks.
- **Not touched:** Learn's seven stages, Live Dialogue selection, Speaking,
  Topic Mock, SRS, the Study Bank, Library writes, `main` and existing PRs.

## 4. Personal and Cloud persistence (Owner item 6)

They are **separate implementations** that share only the pure algorithm and
the pydantic contract.

### Personal Desktop

- **Today.** `InMemoryAiChunkStore`: batches and decision replays live in
  process memory (at most 100 per owner and 500 in total) and are lost on
  restart. Decisions themselves reach the JSON Study Bank.
- **Proposed.** `PersonalAiChunkStore`, a JSON file beside the Personal
  Library and Study Bank stores.
  - Writes are atomic (temp file, then rename) under the existing
    process-wide lock pattern.
  - The file carries a `schema_version`.
  - It holds batches, candidates, decision replays (key, fingerprint,
    response), curation jobs and window rows.
- **Old data.** There is none to migrate, because the in-memory batches never
  persisted. The first start creates an empty file. If the file is corrupt
  it is moved aside as `*.corrupt-<ts>` and an empty store is started. The
  Study Bank is never touched by this recovery.
- **Versioning, expiry and clean-up.**
  - A batch has `expires_at` (proposed: 14 days, the same as Cloud
    retention).
  - On start and on a daily timer, expired batches move to `expired`.
    Batches expired for more than 30 days are deleted, together with their
    replays.
  - Accepted Study Bank items are never touched by clean-up.
- **Crash recovery.**
  - Atomic rename means the file is either the old or the new version.
  - A job in `processing` with an expired lease resumes at the first window
    that is not done, with the same rules as Cloud but a process-local lock
    instead of the database.
- **Learning data.** Personal decisions keep using
  `StudyBankStore.save_items`, so de-duplication, notes, meanings, archive
  state and SRS are preserved. The curation store never writes Study Bank
  or Chunk Memory files itself.

### Cloud

- **Batches.** The existing owner-scoped `0003`/`0011` tables are extended
  by one later migration (see the contract §5). RLS stays forced.
- **Jobs.** New owner-scoped tables, with leases and fences as in the
  state-machine document.
- **Account switch and expiry.**
  - The server binds every row to `app.current_owner`.
  - The browser's `CloudAuthClient` generation checks drop stale responses
    after a switch.
  - Expired batches are terminal (`test_expired_batch_is_terminal_and_cannot_be_accepted`).
- **Library version.**
  - The `transcript_revision` is stored with the evidence and is part of the
    job key.
  - Accept re-validates under `FOR KEY SHARE`.
- **Concurrent edits.** Candidate `version` with 409, unchanged. Two tabs:
  the existing reload or reconcile UI (B-3).
- **Draft dependencies and migration order.**
  - #81 → #82 → #83 (candidates `0011`) → #84 → #85 (`0012` quota) → CH-01
    Step 1 → this revision.
  - The curation migration must be numbered after `0012`, and is proposed as
    `0013`.
  - It cannot land before the stack merges. No migration is written in this
    round.

## 5. Security (Owner item 7)

- **Strict isolation of the transcript.**
  - The system or developer message holds the instructions and the output
    schema.
  - The transcript is data inside a block fenced by a per-request random
    nonce. Any line that contains the nonce is rejected before sending.
  - Each sentence is serialised as a JSON object `{id, text}`, so it cannot
    break out of the structure.
  - The instructions state that the block is quoted material.
- **Prompt injection.**
  - The provider can only return JSON matching the schema, with no tools
    and no URLs.
  - Every candidate must be grounded in a sentence that was citable in its
    window. Injected text such as "ignore previous instructions…" can
    therefore become, at most, a grounded quote of the transcript itself.
    It can never become an instruction, a fabricated source, or a field the
    server trusts.
  - This is tested: malformed scores, extra score keys (for example
    `system_override`), non-integer types, ungrounded instructions and
    over-long text are all rejected per observation, and the reduce
    continues (`test_malformed_provider_output_is_rejected_not_fatal`).
- **Structured output validation.**
  - Scores must be integers 0–3 on known dimensions only.
  - Text must be 2 words or more and at most 120 characters.
  - At most 2 variations, each at most 200 characters and strings only.
  - Rejected observations are counted by reason.
- **Length controls.**
  - Windows are capped by the token budget.
  - The per-job window and token caps limit the total input.
  - The provider's `max_output_tokens` is enforced.
- **Errors and secrets.**
  - Provider errors are mapped to codes (`provider_timeout`,
    `provider_invalid_output`, `provider_rate_limited`).
  - Raw provider bodies, keys and prompts are never returned to clients or
    written to logs, consistent with the existing `llm_client` error
    wrapping.
  - The API key stays backend-only.
- **Cost.**
  - Paid calls need two explicit flags.
  - There are per-job, per-owner and per-day caps, and a concurrency limit
    of 2.
  - Invalid output is not retried.
  - Tests and CI use stubs only.
- **Never.** No command from transcript text is executed, no secret is
  disclosed, and the system rules cannot be changed by provider output. The
  output is only data after validation.

## 6. Impact on P01–P09

There is **none**. The slice is imported by no production module, which is
enforced by `test_no_production_module_imports_the_lab`. The full backend
and frontend regressions are unchanged except for the new lab tests (see the
validation report).
