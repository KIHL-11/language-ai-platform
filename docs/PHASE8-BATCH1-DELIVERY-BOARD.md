# Phase 8 — Batch 1 Cloud Foundation Delivery Board

Status: PROPOSED / DESIGN COMMITTED; no Phase 8 feature has been implemented or deployed.
Baseline: main 234f0587b0f1321a7b8af636e259ebf2f017d3ad. DQ-02 Phase 7A remains a separate, unmerged Draft PR #49. C-01 CI is delivered via a separate stacked Draft PR; it is not part of this Phase 8 branch.

## Delivery stages and gates

| ID | Deliverable | Precondition | Cloud-only acceptance |
| --- | --- | --- | --- |
| C-01 | GitHub Actions + evidence | DQ-02 Phase 7A branch | Actual Ubuntu/macOS-hosted CI jobs with linked logs, reviewed security boundaries |
| P8-01 | Architecture, domain model, API, threat/privacy contracts | Existing code inventory | Reviewed decisions and migration/rollback specifications |
| P8-02 | Identity + server-owned authorization + tenant isolation | P8-01 | Cross-user API and media authorization tests fail closed; old personal mode unchanged |
| P8-03 | AI Chunk Organizer | Existing Study Bank / SRS; P8-01 contracts | Candidate provenance, explicit user confirmation, idempotent saves, SRS preservation and AI failure tests |
| P8-04 | Topic Speaking Curriculum | Existing Speaking Agent; P8-01 contracts | Versioned scenario fixtures, target evidence, interruption/consent and no-auto-SRS tests |
| P8-05 | Email + WeChat integration | P8-02 auth service | Verified Email login and replay-resistant flow; simulated WeChat OAuth, production credentials/approval separately gated |
| P8-06 | Event ingestion + Admin aggregates | P8-01 privacy specification and P8-02 auth | Fake/synthetic data only, registered event schemas, consent handling and admin role tests |
| P8-07 | Integrated cloud acceptance | P8-02 through P8-06 | Full tests, safe migration simulation, tenant/privacy checks and explicit deployment decision |

## Now: Batch 1

1. C-01: put synthetic dictionary release/migration regression under GitHub Actions, run the actual workflow and fix failures. Hosted macOS test does not discharge the Owner Mac gate.
2. P8-01: review cloud data/identity/learning/event contracts; list unresolved technical, security and privacy decisions. No database or account provider selection asserted by default.
3. P8-03/P8-04 design: agree on candidate/Topic schema and behavior before production code. Preserve Study Bank item state and existing target matching/agent boundaries.
4. P8-06 design: review allow-list and privacy purposes before adding any client tracking.

## Per-PR requirements

- Single objective and isolated branch; create Draft PR, no auto-merge.
- Scope, test plan, negative/security cases, and recovery/rollback method included.
- Cloud code/test evidence and GitHub CI results distinguished from human review and physical-device verification.
- No production keys, real learner data, full Kaikki download, dictionary cutover, or silent uploads.
- Fail closed when permission check, SRS preservation, provenance, or privacy gate cannot be proved.
- A scoped, explicit Owner decision is necessary for real integrations, production deployment, personal-data import, or Live Mac operations.

## Open decisions (record before implementation)

1. Deployment region, infrastructure and account/email provider; do not assume any particular SaaS is selected.
2. Local-only mode coexistence and user-driven import, account deletion/export obligations and backup restoration drills.
3. WeChat integration prerequisites for website/mobile surfaces, provider approvals and verified identity mapping.
4. Analytics purpose/legal basis, consent scope, retention period, and minimal admin role policy.
5. AI model/provider selection, request budgets, transcript transmission policy, moderation and supported scenarios.
6. Whether to stage PostgreSQL schema and tests before AI Chunk/topic UI work, or use in-memory fake repositories for parallel development. Either way, public cloud release waits for P8-02 authorization gate.

## Evidence of completion

A task is IMPLEMENTED only when code exists; CLOUD VERIFIED only with actual runner/test evidence; REVIEWED only with recorded review; PRODUCTION READY only with security, privacy, migration and rollback acceptance. This design PR by itself satisfies none of the implementation or deployment gates.
