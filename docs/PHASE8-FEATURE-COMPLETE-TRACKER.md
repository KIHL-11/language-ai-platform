# Phase 8 feature-complete integration tracker

Master: Issue #75. Integration base: 3b6c5dc0299d33c6eb4e947c14f0651590c887de.
This is a development tracker, not owner acceptance or production approval.

## Dependency order and evidence

| Workstream | Delivery | Status | Evidence / gate |
| --- | --- | --- | --- |
| F foundation | strict session JSONB checks; dependency inventory | In progress | synthetic direct-SQL negative tests; fresh audit pending |
| A | email challenges, verified subject mapping, sandbox mail, simulated WeChat flow, account UX | Pending | no real provider registration or mail delivery authorized |
| B | Library/media reliability, upload safeguards, owner Guided/Listening/curation | Pending | Issue #58 synthetic media; no personal media migration |
| C | bounded model/voice adapters, budgets, idempotency, mock interruption | Pending | paid/live calls remain disabled |
| D | export/preview/trial import, rollback/restore, deletion/session controls | Pending | synthetic account data only |
| E | default-off registry, consent, RBAC aggregates and synthetic dashboard | Pending | legal/data-region/retention approval separate |
| F integration | demo seeding, English/German E2E, desktop/mobile evidence, final CI/review | Pending | consolidated UAT only after package complete |

Each modular implementation receives focused tests and a reviewable commit.
Hosted CI runs on this integration branch; final integration SHA must have its
own successful run and independent security/privacy review.

## Safety and external dependencies

- Work only in the cloud project workspace and disposable synthetic stores.
- No claim of access to the owner's Mac. Cloud browser tests do not establish
  real-Mac verification.
- Never open production Email/WeChat/live AI without separately approved
  credentials, callback product/domain, spending and data-processing decisions.
- No public test-login, arbitrary principal minting, hidden personal import or
  automatic merge/deployment.
- DQ-02 #49, real dictionaries/releases, learner JSON/SQLite/audio and unrelated
  repositories remain untouched.

## Final package (all pending until evidenced)

- Integrated SHA and actual hosted CI; full backend/frontend and synthetic E2E.
- Isolated staging bootstrap/teardown, seed accounts and bilingual scenarios.
- Desktop/mobile screenshots, keyboard/accessibility checks and walkthroughs.
- Security/privacy/migration/recovery report with independent findings resolved.
- External blocker list and operator prerequisites.
- Consolidated UAT checklist and prioritized correction backlog.

## Release state dimensions

Track separately: IMPLEMENTED, HOSTED CI, INDEPENDENT REVIEW, REAL-MAC TESTED,
REAL-PROVIDER VERIFIED, PRODUCTION READY. The last two are not authorized by
this task; no substitute mock evidence may mark them complete.
