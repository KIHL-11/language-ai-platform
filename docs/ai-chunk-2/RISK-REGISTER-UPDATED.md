# Risk register (CH-01/02, Round 3, 2026-11-06)

"Open" means not solved in production code. Full CH-01/CH-02 is **not**
authorised for production, and nothing here is production ready.

## High

| ID | Risk | Mitigation (design) | Status |
|---|---|---|---|
| R-H1 | A paid provider's cost runs away; **duplicate billing is possible** after crashes or timeouts (Round 3, M6) | Attempt ledger with reserve before send; `uncertain` counted at its ceiling; re-approval thresholds; provider idempotency keys only when documented; two explicit enable flags; caps | Open: designed (state machine §6); fault tests F1–F10 NOT EXECUTED; no provider code |
| R-H2 | Prompt injection through transcripts | Nonce-fenced data; JSON per sentence; schema-only output; strict shape checks; grounding inside citable ranges; per-observation rejection and exception boundary | Partly mitigated: output side tested in the slice; prompt side not built |
| R-H3 | **Personal accept does not re-validate the Library source** (the Cloud path does) | **Launch gate**, required before any Personal curation ships; see §Gates | **Open, blocking** for launch |
| R-H4 | Real semantic scoring may be poor or inconsistent; synthetic tests cannot prove real quality | `unscored` without core signals; conservative aggregation; gates and floor; human accept or reject; a labelled evaluation set before rollout | Open by nature |

## Medium

| ID | Risk | Mitigation | Status |
|---|---|---|---|
| R-M1 | Rollback breaks replay (`extra="forbid"`; None fields are serialised) | Phase A persistence projection and whitelist; A→B gate; replays never re-rendered (contract §4) | Designed; projection **tested in isolation** (14 tests); production wiring NOT EXECUTED |
| R-M2 | Score drift between windows and model primacy bias | Two-level lower median; quality-only ranking; whole tie groups; permutation tests | Reduce order independence proven in the slice; model-side bias untested |
| R-M3 | Token estimate wrong for a provider | Conservative ratios; 50% headroom; tokenizer when available; skip, never truncate | Designed |
| R-M4 | **German case-sensitive identity** splits a sentence-initial chunk from the same chunk mid-sentence | **Keep Study Bank identity** (Owner decision). Design: a **suspected-duplicate hint** only: when two identities differ only by the case of the first letter and one span starts at character 0, the UI shows "possibly the same as …". Nothing is merged automatically: no merge of cards, notes or SRS | Open (hint designed, not built) |
| R-M5 | Stacked Drafts #81–#85 are unmerged; the curation migration follows `0012` | Number after `0012`; land after the stack merges | Open |
| R-M6 | Personal batches are memory-only | `PersonalAiChunkStore` with a schema version and the Phase A projection | Designed |
| R-M7 | `AiChunkBatch.candidates max_length=8` against the 12 target | Raise it on read in Phase A; persist more than 8 only in Phase B (the projection refuses it, and this is tested) | Designed and tested in isolation |
| R-M8 | Worker races, stale leases, and failed-window recovery against checkpoints | Fence plus token compare-and-swap; checkpoint rows only for success; window status separate; orphan attempts recorded | Designed; J3–J6 NOT EXECUTED |
| R-M9 | Re-curation silently undoes or duplicates decisions (Round 3, M5) | Immutable batch revisions, a superseded status, a current pointer, inheritance by identity that is informational only, and a 409 on superseded batches | Designed; J6–J9 NOT EXECUTED |
| R-M10 | **CH-01 Step 1, Medium A: the planned analysis range is described as analysis already done** (the coverage line is shown before generation) | Not changed this round, by Owner instruction | **OPEN** |
| R-M11 | **CH-01 Step 1, Medium B: `proposal_mode` labels historical batches from the current adapter** | Not changed this round. The Phase B design persists the mode per batch (contract §1) | **OPEN** |

## Low

| ID | Risk | Status |
|---|---|---|
| R-L1 | Fewer than 8 recommendations surprises learners (note shown) | Designed and tested in the slice |
| R-L2 | German mock phrase with `ß` never matches (strict xfail) | Open, mock only |
| R-L3 | Lesson generation over 5 minutes is untimed | Open, out of scope |
| R-L4 | Live Dialogue position bias (CH-00 R3) | Open by decision |
| R-L5 | A chunk split across two windows is rejected in both (`outside_citable_range`) and can be missed | Accepted trade-off: correctness over recall. The window-boundary overlap is context only. If recall matters, a later design may re-ask the provider with a window centred on the boundary |

## Gates

### R-H3: Personal source re-validation (launch blocker)

Before any curated or Library-sourced candidate can be accepted in Personal,
`_apply_decision` must, **inside the same Study Bank write lock** as the
save:

1. **Re-read the Library item.** If it is missing, the accept is refused with
   409 `candidate_source_changed` and nothing is written.
2. **Compare the sentence text with the evidence.** A different text gets a
   409 and nothing is written.
3. **Compare the timestamps and translation.** A difference gets a 409.
   Optionally the client may offer "re-verify", which re-grounds the text
   against the new sentence and produces a **new** candidate version for the
   learner to accept; it is never automatic.
4. **Concurrency.**
   - The Library read and the Study Bank write must sit under one lock, or
     the Library item version must be checked again immediately before the
     write, using compare-and-swap on the Library revision.
   - A change between the check and the save must raise a conflict, never
     save stale provenance.
5. **Tests required before launch:** deleted source, changed sentence,
   changed timestamp, and a concurrent Library edit during accept (with an
   injected pause). Each must leave the Study Bank unchanged.

## Changes this round

- **M1, fixed in the slice.** Duplicate evidence keys are merged by maximum
  verified reliability, with one vote per source.
- **M2, fixed in the slice.** Grounding stays inside the window's citable
  ranges, with a new rejection reason `outside_citable_range`.
- **M3, fixed in the slice.** Strict shape checks, no repair, and a
  per-observation exception boundary.
- **M4, M5 and M6.** The contract and the state machine are revised. The
  claim of no duplicate billing is withdrawn (R-H1 reworded).
- **New:**
  - R-M9 (decision inheritance);
  - R-M10 and R-M11 (the Step 1 Mediums, recorded and OPEN);
  - R-L5 (chunks split across windows).
