# Risk register (CH-01/02 design revision, 2026-11-06)

The status column reflects this round only. "Open" means it is not solved in
production code.

## High

| ID | Risk | Mitigation (design) | Status |
|---|---|---|---|
| R-H1 | A paid provider's cost runs away (retries, loops, large transcripts) | Two explicit flags; per-window budget reservation; per-job, per-owner and per-day caps; concurrency 2; no retry on invalid output; cached ready jobs | Open: designed; no provider code |
| R-H2 | Prompt injection through transcripts | Nonce-fenced data block; JSON-per-sentence; schema-only output; grounding against citable sentences; per-observation rejection (tested in the slice) | Partly mitigated: output side tested in the slice; the prompt side is not built |
| R-H3 | **Personal accept does not re-validate the Library source** (the Cloud path does). A curated Personal candidate could be accepted after its sentence changed or was deleted | Add a Personal equivalent of `_validate_library_evidence` before any Personal curation ships | Open: newly identified, pre-existing gap. The Personal API already accepts `library_passage` sources, although the `main` UI only offers manual text |
| R-H4 | A real semantic scorer may score badly or inconsistently; the slice cannot prove real quality | Never fake quality: `unscored` when signals are missing; a lower-median conservative aggregate; gates and floor; a human accept or reject is required; quality evaluation with a labelled synthetic set before any rollout | Open by nature |

## Medium

| ID | Risk | Mitigation | Status |
|---|---|---|---|
| R-M1 | Rollback after new fields are written fails replay (`extra="forbid"` with `response_json`) | Two-phase rollout: read-tolerant models first | Designed |
| R-M2 | Score drift between windows and model primacy bias | Lower-median aggregation; quality-only ranking; tie groups; finalist re-rank (design) presented in an order that does not depend on position; permutation tests | The slice proves order independence of the reduce; model-side bias is untested (no model) |
| R-M3 | The token estimate is wrong for a given provider | Conservative ratios, 50% context headroom, the provider tokenizer when available, `sentence_exceeds_window` instead of truncation | Designed; ratios tested for arithmetic only |
| R-M4 | **German case-sensitive identity** splits a sentence-initial chunk from the same mid-sentence chunk | Owner decision: keep Study Bank parity (now), or add a curation-level merge for sentence-initial capitals (which would differ from Study Bank de-duplication) | Open: newly identified |
| R-M5 | The stacked Drafts #81–#85 are unmerged; the curation migration depends on `0012` | Number it after `0012`; land only after the stack merges | Open |
| R-M6 | Personal batches are memory-only; decisions are not recoverable after a restart | The proposed `PersonalAiChunkStore` with a schema version | Designed |
| R-M7 | `AiChunkBatch.candidates max_length=8` against the 12 target | Raise it to 12 in phase A of the rollout | Designed |
| R-M8 | Job worker races and stale leases | Lease token with fence; compare-and-swap writes; immutable done windows | Designed; J3–J5 NOT EXECUTED |

## Low

| ID | Risk | Mitigation | Status |
|---|---|---|---|
| R-L1 | Fewer than 8 recommendations surprises learners | The `few_high_quality_candidates` note shown in the UI | Designed, tested in the slice |
| R-L2 | The German mock phrase "im großen und ganzen" never matches (`ß` casefold) | Pinned by a strict xfail (Step 1) | Open, mock only |
| R-L3 | Lesson generation over 5 minutes is not timed | A separate instrumentation task | Open, out of scope |
| R-L4 | Live Dialogue position bias (CH-00 R3) | Unchanged by Owner decision; it could later use curated ranks | Open by decision |

## Risks closed or changed since the first design

- **Ranking by `(score, normalized_text)` could let the alphabet decide
  inclusion.** Replaced by whole tie groups.
- **A finalist hash shuffle as the fairness device.** It is now only a
  presentation measure for a model re-rank; selection fairness rests on
  quality-only keys and order-free aggregation, which are tested.
- **The reliability rule's sentence-initial bias.** Found and fixed in the
  slice.
