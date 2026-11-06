# Codex second independent review: CH-01/02 design revision

- **Verdict being addressed:** DESIGN_CHANGES_REQUIRED on the first AI Chunk
  2.0 design (`docs/AI-CHUNK-2.0-DESIGN.md`, now marked superseded).
- **Branch:** `claude/ch-01-02-design-revision`. The commit that adds this
  file is the HEAD under review (`git log -1`).
- **Base:** CH-01 Step 1 `694e811`, which is on #83 `c89b94b`.
- **Status:** no PR opened, nothing merged, deployed or migrated.
- **Scope:** `git diff 694e811..HEAD`. Read-only, one pass. Full CH-01/CH-02
  is **not** implemented, and nothing here claims production readiness.

## What to review

| Area | Files |
|---|---|
| Revised design | `docs/ai-chunk-2/CH-01-02-DESIGN-REVISION.md` (provenance contract §2, Personal/Cloud §4, security §5) |
| Ranking and quality | `docs/ai-chunk-2/FULL-TRANSCRIPT-RANKING-SPEC.md` |
| Compatibility | `docs/ai-chunk-2/CANDIDATE-COMPATIBILITY-CONTRACT.md` |
| Job (design only) | `docs/ai-chunk-2/CURATION-JOB-STATE-MACHINE.md` |
| Evidence | `docs/ai-chunk-2/SYNTHETIC-VALIDATION-REPORT.md`, `docs/ai-chunk-2/RISK-REGISTER-UPDATED.md` |
| Pure slice | `backend/chunk_curation_lab/*.py` (no I/O; imported by nothing in production) |
| Tests | `backend/tests/test_chunk_curation_lab.py`, `backend/tests/chunk_curation_synthetic.py` |
| CI trigger | `.github/workflows/phase8-cloud-foundation.yml` (one line) |

**Not changed:** any production module, schema, migration or route; the
frontend; Learn, Live Dialogue, Speaking and Topic Mock; SRS; Study Bank;
Library.

## Questions for the re-review

1. **Quality.** Are the eight dimensions, the weights, the lower-median
   aggregation, the `unscored` rule, the gates and the 1.8 floor a
   sufficient and honest model? Is any path left where position, the
   alphabet, a hash or first occurrence affects **selection**? (In-group
   display order and the source-order fingerprint are deliberate; check
   that they cannot change inclusion.)
2. **Order independence.** Is the argument in the ranking spec §8 complete?
   Are the tests (all 720 orders, and 200 shuffles in EN and DE) adequate?
3. **Provenance.** Can any provider-controlled field reach `VerifiedEvidence`?
   Is reusing the Cloud `_validate_library_evidence` at accept, plus the
   Personal gap (R-H3), described correctly?
4. **Compatibility.** Is the two-phase rollout (needed because of
   `extra="forbid"` together with stored `response_json` replays) correct
   and sufficient? Are the defaults truthful for existing rows?
5. **Job.** Does the fence and compare-and-swap design stop stale workers,
   duplicate submits, double billing and budget-partial-as-complete? Is the
   NOT EXECUTED spec J1–J14 complete?
6. **New findings.** Do you agree with the German case-sensitive identity
   finding (R-M4) and the Personal accept re-validation gap (R-H3)?

## Evidence

All runs used macOS, Python 3.11 and `AI_MODE=mock`, with synthetic data
only.

- `test_chunk_curation_lab.py`: 35 passed in each of 3 runs.
- Reused existing protection tests: 8 passed.
- Backend full: 902 passed, 9 skipped, 1 xfailed.
- Frontend full: 441 passed; lint clean.
- Lab mutation check: 19 of 19 killed.
- Hosted CI on this HEAD: see the Owner report or the local drafts copy of
  this handoff.

## NOT EXECUTED

- Job and worker tests J1–J14.
- Provider and prompt tests.
- UI.
- The Personal store.
- Migrations.
