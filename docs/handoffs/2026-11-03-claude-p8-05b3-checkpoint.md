# Checkpoint: P8-05B3 email management + auth maintenance (Claude Code, 2026-11-03)

Manual checkpoint (no `handoff.py` lease; that tool lives only in Draft PR #77).
Status of record is Issue #75; this file is a pointer, not a status source.

## Stack (all Draft, nothing merged or deployed)

| PR | Branch | Head | Base |
| --- | --- | --- | --- |
| #78 P8-05B1 email login | `claude/p8-05b-email-auth` | `abf95ad` | main `eb7e902` |
| #79 P8-05B2 WeChat (simulated), link/unlink | `claude/p8-05b2-account-identity` | `584283e` | #78 |
| #80 P8-05B3 add/change email, maintenance | `claude/p8-05b3-email-management` | `11fc2cd` (code; this file on top) | #79 |

Merge order: #78 → #79 → #80, each only after final review and CI, and after
retargeting to main. #80 contains merge commits of #79 (no force pushes).

## Owner decisions in force (2026-11-03)

1. #78/#79 (and #80) stay Draft; no automatic merge.
2. WeChat never creates an account.
3. No email removal until add/change/recovery is complete.
4. No real mail, WeChat or paid service.
5. The email throttles are interim and test-stage only, not a production approval.
6. Trusted proxy / IP limits belong to Workstream F; never trust an unconfirmed `X-Forwarded-For`.

## Evidence

- Local (macOS, Python 3.11, disposable PostgreSQL 17, Node 26):
  - backend 814 passed / 9 skipped on #80; 789 / 9 on #79
  - frontend 395 / 56 files on #80; 392 / 55 on #79
  - lint clean, build OK, `pip check` clean
- Hosted CI:
  - #79 at `584283e`: runs 41599867426 / 41599859518, SUCCESS (789/9)
  - #80 at `f49fe35`: runs 41600768523 / 41600760652, SUCCESS (810/9)
  - #80 at `11fc2cd`: runs 41602189207 / 41602182406, SUCCESS (814/9)
- Mutation checks: 17 (B2) + 15 (B3) security-guard mutants all fail the suite.
- Independent reviews: separate reviewer agents, fresh context, not the author
  session. No human or different-model review.
  1. B2 post-fix (`a4469c8`): 2 Medium. The rotation race was fixed; the
     hourly-quota lockout was accepted as interim (decision 5).
  2. B3 (`7e6d9fd`): 2 Medium (deadlocks). Both fixed (`584283e`, `f49fe35`).
  3. Fix re-review (`f49fe35`): no Critical/High/Medium. Two Lows: one fixed
     in `11fc2cd`, one accepted (crossing changes).
  - `11fc2cd` itself (live-only mass sign-out plus tests) has not been reviewed again.

## Open risks

- **Medium (interim, decision 5):** anyone who knows an address can spend its
  hourly send quota and block new email sign-ins (about 1 h, renewable). Fix:
  Workstream F per-client limits, plus a remembered-device quota.
- **Low:**
  - no global guess budget across addresses
  - crossing address changes deadlock (both roll back)
  - a purge lock timeout ends the run
  - adding an address does not sign other browsers out
  - a change does not remove a second address or WeChat that an intruder added earlier (the UI prompts the learner to review)
- **Not done:**
  - real mail and WeChat adapters
  - email removal and recovery
  - choosing the primary address
  - a durable notice outbox
  - choosing the re-check address in the UI

## Next

- Workstream A remains In progress (not Feature Complete).
- Next feature: Workstream B, increment B-1 Cloud Library reliability (Issue
  #58), on a fresh branch from main. Plan: `docs/PHASE8-WORKSTREAM-B-PLAN.md`.

## Resume

```bash
git fetch origin
git switch claude/p8-05b3-email-management && git pull --ff-only
gh pr view 80 && gh pr view 79 && gh issue view 75 --comments
python -m venv <venv> && <venv>/bin/pip install -r backend/requirements.txt
# disposable PostgreSQL only; never a real database
AI_MODE=mock TEST_POSTGRES_DSN=postgresql://<user>@127.0.0.1:<port>/<scratch_db> \
  PYTHONPATH=backend <venv>/bin/python -m pytest -q backend/tests
(cd frontend && npm ci && npm test && npm run lint && npm run build)
```
