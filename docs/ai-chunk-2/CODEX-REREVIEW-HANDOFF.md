# Codex third independent review: CH-01/02 Round 3 remediation

> Historical handoff at `19a8830`. The F1–F3 follow-up is documented in
> [CODEX-F1-F3-OWNER-REVIEW.md](CODEX-F1-F3-OWNER-REVIEW.md). Its within-source
> minima, frozen V1 reader and attempt-id accounting supersede the relevant
> assumptions/questions below. This implementation handoff is not an
> independent approval.

- **Verdict being addressed:** CHANGES_REQUIRED on `ddda942`: M1–M3 (with
  counterexamples) and M4–M6 (design gaps).
- **Branch:** `claude/ch-01-02-remediation-r3`. The commit that adds this
  file is the HEAD under review (`git log -1`).
- **Base:** `ddda9429c9521d55a39da68f74347618f4ed954b`.
- **Status:** no PR opened, nothing merged, deployed or migrated.
- **Scope:** `git diff ddda942..HEAD`. Read-only, one pass. Full CH-01/CH-02
  is **not** implemented or authorised for production.

## Changed files

| Kind | Files |
|---|---|
| Slice fixes (M1–M3) | `backend/chunk_curation_lab/provenance.py`, `reduce.py`, `scoring.py` |
| M4 executable model | `backend/chunk_curation_lab/compat.py` (new; isolated) |
| Tests | `backend/tests/test_chunk_curation_lab_r3.py` (new, 39), `backend/tests/test_chunk_curation_compat.py` (new, 14), `backend/tests/test_chunk_curation_lab.py` (2 tests tightened to the no-repair rule) |
| Design | `docs/ai-chunk-2/CANDIDATE-COMPATIBILITY-CONTRACT.md` (§1, §4, §5), `CURATION-JOB-STATE-MACHINE.md` (rewritten), `FULL-TRANSCRIPT-RANKING-SPEC.md` (§4, §5, §6.2), `RISK-REGISTER-UPDATED.md`, `SYNTHETIC-VALIDATION-REPORT.md` (Round 3), `CH-01-02-DESIGN-REVISION.md` (Round 3 table), this file |
| CI trigger | `.github/workflows/phase8-cloud-foundation.yml` (one line) |

**Unchanged:** every production module, schema, serialiser, migration and
route; the frontend; Learn, Listening, Shadowing, Speaking and Topic Mock;
Study Bank; SRS; the CH-01 Step 1 code.

## Questions

1. **M1.** Is merging one evidence key by the maximum verified reliability
   sound? Reliability is a server observation and changes neither the span
   nor any semantic score. Is the two-level lower median order-independent
   and resistant to vote stuffing? Is any order dependence left in
   `reduce.py`?
2. **M2.** Do `citable_ranges` and `_ground_in` guarantee `[start, end)` lies
   inside characters the window could cite, in every case, including
   repeated phrases and consecutive parts? Is rejecting chunks that span two
   windows (R-L5) acceptable?
3. **M3.** Is there any untrusted field still read before `shape_error`, or
   any remaining coercion or repair? Is the `verification_error` boundary
   scoped correctly (per observation only)?
4. **M4.** Is the projection plus the A→B gate sufficient for rollback,
   given that Pydantic serialises None fields? Is it right that decision
   responses in Phase A equal the projected record, and that replays are
   never re-rendered?
5. **M5.** Do separate window status, attempts and checkpoints remove the
   `ON CONFLICT DO NOTHING` recovery conflict? Do immutable revisions with
   `UNIQUE(owner, job_id, batch_revision)` and a current pointer keep every
   old decision and Study Item reference intact? Is the inheritance table
   safe?
6. **M6.** Is the attempt ledger (`reserved → in_flight → succeeded |
   failed_definitive | failed_billed | uncertain`), with conservative
   accounting and the re-approval thresholds, honest and sufficient? Is
   F1–F10 complete?

## Evidence

All runs used macOS, Python 3.11 and `AI_MODE=mock`, with synthetic data
only.

| Run | Result |
|---|---|
| New counterexamples | 39 passed (30 of them fail on unfixed `ddda942`) |
| Compatibility | 14 passed |
| All slice tests | 88 passed, 3 of 3 runs |
| Backend full | 955 passed, 9 skipped, 1 xfailed |
| Slice mutation | 34 of 34 killed (15 new) |
| Hosted CI | see the Owner report or the local drafts copy |

## OPEN by instruction

- The CH-01 Step 1 Mediums R-M10 and R-M11.
- R-H3, the Personal re-validation launch gate.
- R-M4, German case: a hint only, never an automatic merge.

## NOT EXECUTED

- J1–J14 and F1–F10.
- Production wiring of the projection, and a two-version deploy test.
- The R-H3 gate tests.
- Provider, prompt and UI tests.
