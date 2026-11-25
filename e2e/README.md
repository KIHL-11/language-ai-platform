# Cloud browser E2E — Gate 1 (Draft / NOT RELEASE APPROVAL)

Original reproduction baseline: `8403a3b76b8cf630109acd2431ff0119929bf30f`.
New main baseline after #91: `a965ed06278ae2b4c73412d647d05d553f19e7d3` (must be revalidated at execution).
This suite is a **new, initially unexecuted** browser regression harness, not proof that the historical 61/61 suite is reproduced.

## Safety preconditions
- One-time isolated PostgreSQL 16 instance, synthetic test account, sandbox email provider.
- Never point `E2E_BASE_URL` at production, reuse production secrets, or use real user records.
- Database migration `0001–0009` requires **separate explicit authorization** and is not performed by this harness.
- Provide a Playwright `storageState` JSON captured for a dedicated synthetic **authenticated** account outside the repository. Never commit state or secrets.
- The sandbox domain heuristic in config is a guardrail, NOT a guarantee of isolation. Operator must independently verify routing and credentials.

## Install / run (once an approved sandbox exists)
```sh
cd e2e
npm install # provisional: a committed npm lockfile and npm ci are still required before reproducible CI
npx playwright install chromium
E2E_BASE_URL=https://your-sandbox.example.test \
E2E_EXPECTED_HOST=your-sandbox.example.test \
E2E_STORAGE_STATE=/secure/path/synthetic-account.storage-state.json \
E2E_ISOLATED_SANDBOX=yes \
E2E_STORAGE_STATE_B=/secure/path/synthetic-account-b.storage-state.json \
E2E_STORAGE_STATE_LOGOUT=/secure/path/synthetic-logout.storage-state.json \
E2E_STORAGE_STATE_REPLAY=/secure/path/synthetic-replay.storage-state.json \
E2E_ACCOUNT_A_LIBRARY_ITEM_ID=seeded-synthetic-a-item-id npm test
```
Local sandbox targets also require `E2E_ALLOW_LOCAL=yes`. Nonstandard hostnames require `E2E_ALLOW_NONSTANDARD_HOST=yes` and independent confirmation that the target is isolated. These overrides do NOT authorize production use.

## Current coverage
- True Chromium navigation: authenticated My Library → Words & Chunks.
- Capture API HTTP status and `detail.code` in an attached JSON evidence file; fail on Study Bank 503.
- Logout confirmation, including reloading and checking that protected navigation is absent.

## Verification blockers
- No dependency lockfile has been committed. An attempt to resolve the dependency in the current execution environment failed because npm registry DNS resolution was unavailable (EAI_AGAIN). A verified lockfile must be generated with npm and used via npm ci before Gate 1/2 can be called reproducible.
- The browser harness has not been compiled, collected, or run; there is no E2E PASS yet.
- Confirm the target is a designated sandbox by independent infrastructure inspection; matching E2E_EXPECTED_HOST alone is not a security authorization.

## Gate 2 synthetic security fixtures
- Provide synthetic authenticated account storageState files for A and B, plus separate valid sessions for the destructive logout and replay tests (E2E_STORAGE_STATE_LOGOUT, E2E_STORAGE_STATE_REPLAY). All four session cookies must be distinct; reuse of one session across tests invalidates subsequent tests.
- Seed and provide a Library item owned by A in E2E_ACCOUNT_A_LIBRARY_ITEM_ID. The test expects B to receive 404 when accessing that item.
- Tests verify B cannot impersonate A through user_id, reject POST without CSRF, and reject a revoked session cookie after logout.
- Gate 2 tests fail closed when fixture variables are absent. A seeded synthetic sandbox is required; no tests have been executed.
- A fresh email login, application restart persistence, and an independent security review are still not implemented or verified.

## Still pending
- Execute against approved sandbox; persist trace/screenshots/HTTP evidence.
- Verify two-account isolation, CSRF, stale/replayed cookies, fresh login, service restart.
- Independent security review; never merge or deploy based on Gate 1 alone.

Playwright outputs are local artifacts and must not be committed. Traces can contain private state; only synthetic credentials are allowed, and artifacts must be access-controlled.
