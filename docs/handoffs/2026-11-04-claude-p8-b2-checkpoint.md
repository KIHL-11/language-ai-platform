# Checkpoint: P8-B2 Cloud Guided/Listening + progress OCC (Claude Code, 2026-11-04, final)

Manual checkpoint (no `handoff.py` lease; that tool lives only in Draft PR #77).
Status of record is Issue #75; this file is a pointer, not a status source.

## Stack (all Draft, nothing merged or deployed)

| PR | Branch | Head | Base |
| --- | --- | --- | --- |
| #81 P8-B1 media integrity + cleanup worker | `claude/p8-b1-library-reliability` | `dfd243b` (accepted) | main `eb7e902` |
| #82 P8-B2 progress OCC + Cloud Guided/Listening | `claude/p8-b2-guided-listening` | `a963ea1` (code; this file on top) | #81 |

Worktree: `~/Downloads/language-ai-claude-p8-b2`. Merge order: #81 then #82, only
after owner acceptance and retargeting.

## Owner decisions in force (2026-11-04)

1. B-1 accepted at `dfd243b`. PRs stay Draft; no merge or deploy.
2. GitHub comments are not posted by the agent; drafts are kept in
   `~/Downloads/language-ai-review-drafts/p8-b1/` and `p8-b2/`.
3. **Starlette:** no private `FileResponse` hooks. Production uses a tested,
   locked version. The Workstream F dependency audit keeps evidence for 1.7.0
   and 1.10.0.
4. **Listening:** the resume point is the position from the most recent
   playback action, forward or back. Another tab's player is never moved.
   Guided progress is monotonic.
5. **Before B-3:** Cloud must not fall back to Personal stores; recall and
   chunk memory reopen only on owner-scoped cloud stores.
6. No real data, mail, WeChat or paid AI.

## What B-2 delivered (since the first checkpoint)

- **`80105ef`:** the CI failure on `d2c981b` (run 41676494879) was test timing
  (proven by a file probe: 1 run in 89). The test now waits for the request and
  checks stronger owner claims; 100 consecutive runs pass.
- **`7ce79e6`:** two fixes.
  - **Listening ownership:** an idle tab no longer moves another tab's resume
    point. This was proven failing first.
  - **Cloud → Personal gate:** six routes now fail closed: chunk memory
    list/recall/practice/archive, integration target languages, and the
    speaking-suggestion save.
- **`010829e` / `a963ea1`:** position claims follow the media clock and need
  sustained playback (two advancing ticks). This fixes review M1/L1 and the
  follow-up Low.

## Evidence

- **Local** (macOS, Python 3.11, disposable PostgreSQL 17):
  - Backend 821 passed / 9 skipped; frontend 429 / 56 files.
  - Lint and typecheck clean; build OK.
- **Hosted CI on the B-2 code head `a963ea1`:** run 41707396206, SUCCESS
  (821/9, 429/56).
- **Hosted CI on earlier B-2 heads:**
  - `80105ef`: 41683432303
  - `7ce79e6`: 41684782778
  - `2462c10`: 41675359231
  - `d2c981b`: 41676494879 FAILED (fixed in `80105ef`)
- **Mutation checks:** 36 mutants. All fail the tests except one equivalent
  (the UPDATE `revision =` condition).
- **Independent review:** separate reviewer agent; no human or
  different-model review.
  - Seven rounds in all.
  - Every High, Medium and Low was fixed or explicitly accepted.
  - The last two rounds (`cfbebc4`, `a963ea1`) ended with one Low (fixed),
    then no findings.
  - It also listed every API route on the app: no other cloud route falls
    back to a Personal store.

## Open items and risks

- **Accepted (L3):** the same-account focus re-check sends requests during
  `/me`. CSRF is bound to the session and private GETs are owner-checked.
- **Missed claims only, never a wrong overwrite:** a native seek larger than
  2 s, and a hidden tab that Chrome throttles to about one tick a minute.
- **Pre-existing:** the Play/Pause label can be stale after native or OS
  pause or after the media ends.
- **Not done:**
  - cloud dictionary, recall and Study Bank saves (B-3/B-4);
  - Shadowing in cloud;
  - lesson creation and import (D);
  - voice (C);
  - browser E2E (B-7).
- **Issue #58:** items 1–4 are implemented across #81 and #82. Keep it open
  until the owner accepts #82.

## B-3 start conditions (met once this file's commit has a green CI)

- B-2 HEAD CI green and the review has no open findings.
- B-3 branches from that HEAD in its own worktree.
- **Scope:** a synthetic English/German dictionary adapter and Cloud AI Chunk.
  - Any recall or chunk-memory reopening in cloud must use owner-scoped
    PostgreSQL stores; the six gated routes stay closed until then.

## Resume

```bash
cd ~/Downloads/language-ai-claude-p8-b2 && git fetch origin && git status
gh pr view 82 && gh issue view 58 && gh issue view 75 --comments
AI_MODE=mock TEST_POSTGRES_DSN=postgresql://<user>@127.0.0.1:<port>/<scratch_db> \
  PYTHONPATH=backend <venv>/bin/python -m pytest -q backend/tests
(cd frontend && npm ci && npm test && npm run lint && npm run build)
```
