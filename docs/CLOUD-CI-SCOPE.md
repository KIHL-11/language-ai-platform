# Cloud foundation CI — Batch 1

## Purpose

This workflow is the first GitHub-hosted validation layer for Language AI Platform.
It was added on a **separate** branch based on DQ-02 Phase 7A commit
`76bd3dab97c59180fb25673cbea844af2ef2de87` and does not alter PR #49.
GitHub-hosted green checks must **never** be called real-Mac verification or
production cutover approval.

## Jobs and test boundaries

| Job | Runner | Work | What it does NOT prove |
| --- | --- | --- | --- |
| `dq02-fixtures` | Ubuntu and macOS 15 | Synthetic DQ-02 importer, manifest, release, cutover, failure injection, LaunchAgent mocking and Bash syntax | No live launchctl, no real SQLite data, no local user records, no live provider |
| `backend-full` | Ubuntu | Install existing backend dependencies, pip check, run complete Python pytest suite | Does not opt into installed-dictionary smoke or validate a real Mac |
| `frontend` | Ubuntu | npm ci, lint, Vitest, production build | No browser/hardware/manual UI verification |

The targeted fixture job intentionally does **not** run
`test_dictionary_runtime_status.py` because that test imports the complete
application and is covered by `backend-full`. It uses a small, dedicated
`backend/requirements-dq02-ci.txt` to avoid downloading ML runtime stacks on
both platform runners. The optional `test_dictionary_real_data.py` is skipped
unless `RUN_REAL_DICTIONARY_SMOKE=1`; do not change that setting in CI.

## Security constraints

- Event triggers: `pull_request` and manual `workflow_dispatch`; no release/deploy trigger.
- Workflow token: `contents: read` only. No production GitHub environment or secrets.
- No `--execute` migration commands; tests create isolated synthetic fixtures.
- No complete Kaikki download or network credentials in tests.
- Neither `dictionary_current` nor the installed flat database is touched.
- Tests on hosted macOS are not a substitute for Runbook real-machine checks.
- Dependency install and npm CI use upstream public package registries. Review
  dependency pinning and action-SHA pinning before opening CI to untrusted code.

## Acceptance / follow-up

1. Obtain GitHub Actions run links and actual per-job conclusions; before that,
   status is **workflow committed / CI not yet confirmed**.
2. Fix CI setup or test failures with new commits; do not re-label agent-reported
   local test counts as CI evidence.
3. Review failures in the safety-critical DQ-02 cutover and rollback tests.
4. Add explicit reviewed quality/coverage thresholds in a separate follow-up;
   do not silently approve the draft Gold Set.
5. DQ-02 PR #49 remains Draft; retain the real-Mac approval gates.
