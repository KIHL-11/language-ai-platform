# Phase 8 Workstream B plan: the cloud learning loop

Planning only, 2026-11-03. The gap column comes from a code survey (routes, repositories and cloud UI views), not from running each flow; entries marked "to verify" need confirming at the start of each increment. Status of record: Issue #75 and
`docs/PHASE8-FEATURE-COMPLETE-TRACKER.md`. Everything below runs on synthetic
accounts, a disposable PostgreSQL, the mock AI mode and synthetic media; real
mail, WeChat, paid AI and personal media stay off. Each increment is its own
stacked Draft PR with hosted CI and an independent review.

Order (owner): Cloud Library → Guided / Listening → Dictionary / AI Chunk →
Study Bank → Daily Review → Topic Speaking.

## Where cloud mode stands (main `eb7e902` + Draft #78–#80)

| Area | Backend in cloud | Cloud UI | Gap |
| --- | --- | --- | --- |
| Library | owner-scoped repository (P8-02B1), audio route | read-only preview list | media integrity, cleanup worker, revisions (Issue #58: branch exists, **no commits**); no create/import path in cloud UI |
| Guided / Listening | progress PATCH routes are owner-scoped | not mounted in cloud | `expected_revision` concurrency (Issue #58 item 4); training UI wired to `apiFetch` |
| Dictionary | `dictionary_api` has no cloud repository | none | cloud-safe lookup source (no Kaikki / real dictionary release); fail-closed in cloud today |
| AI Chunk | durable owner-scoped candidates (P8-03B2), mock generator | via Study Bank | budget/idempotency adapter belongs to Workstream C |
| Study Bank | owner-scoped (P8-03B1) | mounted | Library → Study Bank provenance in cloud UI (to verify) |
| Daily Review | owner-scoped review routes | mounted | concurrent-tab rating safety not yet verified |
| Topic Speaking | owner-scoped (P8-04B1) | mounted | text practice only; voice stays off (Workstream C) |

## Increments

**B-1 Cloud Library reliability (Issue #58).** Verify media bytes against the stored SHA-256
and size before serving them, using bounded streaming; corrupt bytes fail closed. Add a
leased, idempotent cleanup-outbox worker with retries and backoff, and route replacement
failures through the outbox. Tests use synthetic audio, tamper and object-swap cases,
two concurrent workers, and an injected crash. Personal Desktop does not change.

**B-2 Guided and Listening in cloud.** Make `expected_revision` mandatory on cloud progress
writes, with an atomic check-and-write in SQL and 409 on a stale revision; keep higher
learning/SRS states. Mount the guided and listening stages for Library items in the cloud
workspace through `apiFetch`, with owner-change and generation guards. Realtime voice stays
disabled. Tests cover two writers, two tabs, retries, an owner mismatch, and Personal
contract compatibility.

**B-3 Dictionary and AI Chunk.** Add a cloud dictionary adapter interface backed by a small
synthetic fixture lexicon (English/German) and fail closed when it is not configured. No real
dictionary release and no Kaikki. The chunk organizer reads the cloud Library and writes
owner-scoped candidates (mock generator only); per-owner request caps come from Workstream C
when it lands.

**B-4 Study Bank from cloud sources.** Save from Library, Dictionary and AI candidates with
provenance, and enforce owner checks across item, media and candidate. De-duplicate by a
normalized key under concurrency. Delete keeps the review history policy as documented.

**B-5 Daily Review.** Make due-queue reads and ratings revision-safe, so two tabs never
double-advance an item. Make day boundaries timezone-correct for each learner, with a
synthetic clock in tests.

**B-6 Topic Speaking.** Generate topics from cloud Study Bank targets and run text-only
practice attempts with owner-scoped storage. The interruption and resume contract with the
mock adapter is shared with Workstream C. Voice and paid models stay disabled.

**B-7 Integrated cloud loop E2E.** One synthetic learner per language: import a synthetic
lesson, work through guided and listening, look words up, save chunks, review, then practice
a topic. Run it in CI (Playwright or the existing test harness). Desktop and mobile
screenshots go to Workstream F integration.

## Cross-cutting rules

- Every cloud route resolves the owner from the session only; it never accepts a
  client `user_id`. Every store has negative cross-owner tests.
- Personal Desktop routes and data stay byte-compatible, with tests on both sides.
- Migrations come in up/down pairs. A rollback refuses rather than dropping learner data.
- Each PR is stacked, Draft, with hosted CI and an independent review before the next
  increment builds on it. No merge or deployment.

## Dependencies and blockers

- **Workstream C:** budgets and idempotency for AI and voice. B-3 and B-6 use the mock
  until then.
- **Workstream D:** export, import and deletion. Library import in the cloud UI is
  limited to synthetic or explicitly approved sources.
- **Workstream F:** trusted proxy and rate limits; E2E infrastructure.
- **Owner decisions:** whether any real dictionary data may be hosted, and the storage
  backend for production media. Neither blocks B-1 to B-7 on synthetic data.

Next: start B-1 on a branch from main (independent of the account PRs), since
it touches only Library storage.
