# F1–F3 implementation handoff for Owner Review

- Date: 2026-11-07.
- Base: `19a88301f108d2204576380ea2b8fcc111a9f51f`.
- Branch: `codex/ch-01-02-f1-f3`.
- Status: local isolated implementation, pending Owner Review. No PR merge,
  deployment, paid call or production data migration.

## F1 — repeated scores for one evidence key

Root cause: the per-source median gave duplicate reports multiplicity even
though the resulting source received one vote. A low/high pair could become
high by repeating the high report, crossing the recommendation floor.

`scoring.merge_scores` now uses the minimum of non-missing values within
one evidence key. Across distinct sources, `assess` still uses the lower
median. Source reliability still uses the best verified match. Duplicate
counts cannot change quality/status/provenance; diagnostic observation counts
may change. A conflicting low report can conservatively reduce quality; real
semantic scorer quality is not proven by this rule.

Tests: `test_chunk_curation_f1.py`, including 2/10/100 repetitions, conflict
at the floor, multiple sources, partial score maps, gates and permutations.
One existing R3 assertion is updated to require the now-conservative
below-floor result while still checking exact evidence reliability.

## F2 — frozen Legacy V1 persistence

Root cause: deriving the old whitelist/reader from live production models
would let both sides drift together, hiding a rollback regression.

`legacy_v1.py` is a static copy of the transitive validators at the base:
candidate/evidence/batch/response, StudyItem, ReviewState and all three
provenance variants. It imports no production models and must not be
regenerated when V2 grows. The existing projection now uses it recursively.
Unknown provenance kinds and batches above 8 fail closed in Phase A.

The committed historical fixture is entirely synthetic. Both frozen V1 and
V2 read it, and the V2 projection returns identical V1 JSON values, including
states, proposal version, archive, note, manual meaning and review counters.
Future-field tests expand live model bindings and nested records; projection
must not widen. Existing real-shaped synthetic decision tests now validate
against the independent frozen reader.

No production serializer, schema, stored decision or database was modified.
A real two-version rollout remains untested.

## F3 — one budget allocation per attempt id

Root cause: summing uncertain rows and orphan rows separately could debit
one physical call twice. Event ids and provider idempotency keys are not
accounting identities.

The state-machine design now binds each physical send to
`(owner, attempt_id)` before dispatch, with immutable request context.
Orphan and late-usage events reference this identity; they never add a charge.
The pure `billing.budget_totals` experiment joins canonical snapshots and
reports, counts spend/holds once, reconciles evidenced actual cost once and
fails closed on contradictory records. Distinct physical retries use new ids
and stay conservatively funded even with the same provider key. Only verified
billing/replay evidence may allocate zero incremental cost.

The tests exercise repeated orphan reports, late receipts, holds, actual
overruns, owner namespaces, request conflicts, unknown attempts, zero-cost
replay proof and invalid costs. Production fencing, transactions, concurrency,
receipt authentication and worker event storage are NOT IMPLEMENTED.
Conflicting evidence blocks admission pending reconciliation.

## Validation and remaining gates

- Baseline targeted tests: **88 passed**.
- Final targeted tests: **126 passed**.
- Original F1 function against new counterexamples, injected in memory:
  **5 failed / 1 passed**, confirming detection of the baseline bug.
- Full results/command: `SYNTHETIC-VALIDATION-REPORT.md` follow-up section.
- No full-suite or frontend lint/build run: no production/frontend changes.
- Review scope is lab modules, explicit test files/fixture and these design
  documents only. Test runtime files are isolated and ignored by Git.
- R-H3 Personal accept re-validation remains a launch gate. Step 1
  R-M10/R-M11 remain open. German identity stays unchanged; no old cards merge.
- This handoff is an implementation/self-review record, not independent
  approval or permission to integrate the full CH-01/CH-02 feature.

Stop after the local commit and await Owner Review.
