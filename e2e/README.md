# Cloud browser E2E — Gate 1 (Draft / NOT RELEASE APPROVAL)

Pinned baseline: `8403a3b76b8cf630109acd2431ff0119929bf30f`.
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
npm install
npx playwright install chromium
E2E_BASE_URL=https://your-sandbox.example.test \
E2E_STORAGE_STATE=/secure/path/synthetic-account.storage-state.json \
E2E_ISOLATED_SANDBOX=yes npm test
```
Local sandbox targets also require `E2E_ALLOW_LOCAL=yes`. Nonstandard hostnames require `E2E_ALLOW_NONSTANDARD_HOST=yes` and independent confirmation that the target is isolated. These overrides do NOT authorize production use.

## Current coverage
- True Chromium navigation: authenticated My Library → Words & Chunks.
- Capture API HTTP status and `detail.code` in an attached JSON evidence file; fail on Study Bank 503.
- Logout confirmation, including reloading and checking that protected navigation is absent.

## Still pending
- Execute against approved sandbox; persist trace/screenshots/HTTP evidence.
- Verify two-account isolation, CSRF, stale/replayed cookies, fresh login, service restart.
- Independent security review; never merge or deploy based on Gate 1 alone.

Playwright outputs are local artifacts and must not be committed. Traces can contain private state; only synthetic credentials are allowed, and artifacts must be access-controlled.
