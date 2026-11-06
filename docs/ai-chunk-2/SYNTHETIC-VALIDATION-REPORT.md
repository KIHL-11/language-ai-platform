# Synthetic validation report (CH-01/02 design revision)

- **Environment:** macOS, Python 3.11 venv, disposable PostgreSQL 17
  (`postgresql://synthetic@127.0.0.1:55433/scratch_b1`, used by the existing
  suites only; the lab needs no database), `AI_MODE=mock`.
- **Data:** synthetic EN and DE transcripts only. No AI provider was called,
  and no real learning data was read or written.

Explicit **stub scores** (`tests/chunk_curation_synthetic.py::STUB_SCORES`)
stand in for a semantic scorer. They are test inputs, not AI judgements. The
slice does not claim to judge real quality; without scores it returns
`unscored`.

## Commands

```bash
cd backend
# Lab, run 3 times
AI_MODE=mock PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_chunk_curation_lab.py
# Reused existing protections (8 selected tests)
TEST_POSTGRES_DSN=… AI_MODE=mock python -m pytest -q -p no:cacheprovider \
  tests/test_ai_chunk_organizer.py::test_duplicate_preserves_review_and_archived_state \
  tests/test_ai_chunk_organizer.py::test_media_accept_does_not_overwrite_chunk_memory_meaning_or_statistics \
  tests/test_durable_ai_chunk_candidates.py::test_duplicate_accept_preserves_srs_fields_archive_meaning_and_note \
  tests/test_durable_ai_chunk_candidates.py::test_reject_has_no_study_or_review_side_effect \
  tests/test_durable_ai_chunk_candidates.py::test_library_source_is_owner_scoped_and_exact_snapshot_is_persisted \
  tests/test_durable_ai_chunk_candidates.py::test_cross_tenant_batch_and_candidate_ids_are_non_enumerating \
  tests/test_durable_ai_chunk_candidates.py::test_expired_generation_lease_can_be_reclaimed_after_restart \
  tests/test_durable_ai_chunk_candidates.py::test_parallel_duplicate_generation_invokes_mock_once
# Full regression
TEST_POSTGRES_DSN=… AI_MODE=mock PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
cd ../frontend && npx vitest run && npm run lint
```

## Results

| Run | Result |
|---|---|
| `test_chunk_curation_lab.py` | **35 passed**, 3 of 3 runs (about 1.5 s) |
| Reused existing protections (8) | **8 passed** |
| Backend full | **902 passed, 9 skipped, 1 xfailed**: Step 1 baseline 867 plus 35 new. The xfail is Step 1's pinned German `ß` mock bug |
| Frontend full | **441 passed** (57 files); the branch has no frontend change |
| Frontend lint | clean |
| Lab mutation check | **19 of 19 killed** (below) |
| Hosted CI | see `CODEX-REREVIEW-HANDOFF.md` |

## Owner test list (section 9) → evidence

| # | Requirement | Test(s) | Status |
|---|---|---|---|
| 1 | Long English transcript | `test_long_transcript_is_fully_covered_and_every_good_chunk_found[en]` (400 sentences, more than 10 windows, 400 of 400 covered) | EXECUTED, pass |
| 2 | Long German transcript | `…[de]` | EXECUTED, pass |
| 3 | High quality at start, middle or end | `test_start_middle_and_end_placements_give_the_same_recommendations[en/de]`, `test_every_permutation_of_three_regions_selects_the_same_set[en/de]`, `test_low_quality_early_never_beats_high_quality_late` | EXECUTED, pass |
| 4 | Oversized single sentence | `test_an_oversized_sentence_is_split_and_counted_once`, `test_a_sentence_that_cannot_fit_is_skipped_with_a_reason_not_truncated` | EXECUTED, pass |
| 5 | Window boundaries | `test_window_boundaries_keep_sentences_whole_and_context_uncitable`, `test_windows_respect_the_token_budget_for_both_languages` | EXECUTED, pass |
| 6 | Unique-sentence coverage with overlap | `test_overlap_never_inflates_coverage_and_failed_windows_are_not_covered` | EXECUTED, pass |
| 7 | Candidate order permutation | `test_reduce_is_identical_for_every_order_of_a_fixed_candidate_set` (all 720 orders), `test_reduce_is_identical_under_random_shuffles_of_a_long_transcript[en/de]` (200 shuffles each) | EXECUTED, pass |
| 8 | Aggregation independent of first occurrence | `test_aggregation_does_not_depend_on_which_occurrence_came_first`, `test_multiple_sources_merge_and_the_primary_is_not_the_first_occurrence` | EXECUTED, pass |
| 9 | Stable ordering for equal scores | `test_equal_scores_form_one_stable_tie_group`, `test_a_tie_straddling_the_limit_is_excluded_whole_not_alphabetically` | EXECUTED, pass |
| 10 | Missing or invalid provenance | `test_missing_or_invalid_provenance_is_rejected_with_a_reason`, `test_provider_claims_are_ignored_and_evidence_comes_from_the_snapshot`, `test_malformed_provider_output_is_rejected_not_fatal` | EXECUTED, pass |
| 11 | AI variation never becomes media source | `test_an_ai_variation_never_becomes_source_evidence` | EXECUTED, pass |
| 12 | Library deletion or transcript update invalidates evidence | `test_library_deletion_and_transcript_changes_invalidate_old_evidence` (pure re-validation); accept-time 409 is already covered by the existing `test_library_source_is_owner_scoped_and_exact_snapshot_is_persisted` | EXECUTED, pass (pure); curated-accept integration **NOT EXECUTED** (J13) |
| 13 | Lease timeout and worker races | spec J3, J4, J5 | **NOT EXECUTED** (no job service exists) |
| 14 | Timeout retry and budget exhaustion | `test_budget_exhaustion_is_partial_never_ready` (coverage semantics); worker retry spec J6–J8 | Partly EXECUTED; worker part **NOT EXECUTED** |
| 15 | Cross-account job access denied | spec J11, J12; existing batch isolation: `test_cross_tenant_batch_and_candidate_ids_are_non_enumerating` | Jobs **NOT EXECUTED**; batches EXECUTED (existing) |
| 16 | Archived card note, manual meaning, ReviewState and SRS protected | existing `test_duplicate_accept_preserves_srs_fields_archive_meaning_and_note`, `test_duplicate_preserves_review_and_archived_state`, `test_media_accept_does_not_overwrite_chunk_memory_meaning_or_statistics`, `test_reject_has_no_study_or_review_side_effect`; curated variant spec J14 | Existing EXECUTED, pass; curated **NOT EXECUTED** |

**Additional slice tests:**

- unscored output;
- fewer than 8 allowed and never padded;
- at most 12;
- the floor;
- a missing core dimension;
- gates;
- novelty and Study Bank identity parity;
- punctuation and case merging;
- isolation (no production import; no I/O imports).

## Mutation check (lab)

Each mutant was applied with `PYTHONDONTWRITEBYTECODE=1` and the bytecode
was purged after every restore. **19 of 19 were killed**:

1. median replaced by the first occurrence
2. aggregation uses the first observation
3. sources kept in arrival order
4. a tie cut alphabetically
5. no limit at all
6. the quality floor removed
7. the gate removed
8. a missing core dimension treated as scored
9. partial reported as ready
10. a budget gap counted as complete
11. context counted as coverage
12. a context sentence accepted as citable
13. a variation accepted as source
14. provider timestamps trusted
15. re-validation ignores timing
16. a deleted source still valid
17. case-sensitive reliability
18. malformed scores not rejected
19. an oversized sentence truncated

Two mutants first survived and exposed missing tests, which were added: the
quality floor and the core-dimension rule.

## Design findings made while validating

1. **The reliability rule penalised a sentence-initial capital.** The first
   draft scored "to be honest" against "To be honest, …" as a weaker match,
   which is an indirect position bias. Fixed: the comparison ignores case.
2. **German identity keeps case** in the existing Study Bank
   (`normalize_chunk_text`). A sentence-initial "Auf jeden Fall" and a
   mid-sentence "auf jeden Fall" are separate identities. The slice keeps
   Study Bank parity, and this is recorded as R-M4 for the Owner.
3. **Malformed scores must not abort the reduce.** The first draft raised on
   out-of-range scores. Fixed: each such observation is rejected as
   `invalid_scores`.

## NOT EXECUTED

- Every test in `CURATION-JOB-STATE-MACHINE.md` §8, J1–J14.
- Any real provider, prompt or token-count test.
- UI tests for the Recommended Chunks panel.
- Personal store tests: atomic write, corrupt-file recovery, clean-up.
- Migration tests for the proposed Cloud migration.
