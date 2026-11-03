# Checkpoint: P8-B2 Cloud Guided/Listening + progress OCC (Claude Code, 2026-11-04)

Manual checkpoint (no `handoff.py` lease; that tool lives only in Draft PR #77).
Status of record is Issue #75; this file is a pointer, not a status source.

## Stack (all Draft, nothing merged or deployed)

| PR | Branch | Head | Base |
| --- | --- | --- | --- |
| #81 P8-B1 media integrity + cleanup worker | `claude/p8-b1-library-reliability` | `dfd243b` | main `eb7e902` |
| #82 P8-B2 progress OCC + Cloud Guided/Listening | `claude/p8-b2-guided-listening` | `2462c10` (code; this file on top) | #81 |

Worktree: `~/Downloads/language-ai-claude-p8-b2`. Workstream A (#78–#80) is a separate stack.
Merge order: #81 then #82, only after owner acceptance and retargeting.

## Owner decisions in force (2026-11-03/04)

1. B-1 accepted at `dfd243b` (CI 41660011509). PRs stay Draft; no merge or deploy.
2. GitHub comments are not posted by the agent (an auto-mode denial must not be bypassed).
   - B-1 drafts: `~/Downloads/language-ai-review-drafts/p8-b1/`.
   - B-2 drafts: `~/Downloads/language-ai-review-drafts/p8-b2/`.
3. **Starlette:** never depend on private `FileResponse` methods. Production must use a tested, locked version. The dependency audit (Workstream F) must check FastAPI/Starlette/AnyIO compatibility and keep regression evidence for Starlette 1.7.0 and 1.10.0. B-1 has that evidence; B-2 adds no media serving.
4. No real data, mail, WeChat or paid AI.

## Evidence

- **Local** (macOS, Python 3.11, disposable PostgreSQL 17):
  - Backend 815 passed / 9 skipped; frontend 417 / 56 files.
  - Lint clean, build OK.
- **Hosted CI:**
  - `cf3771e`: run 41667750209, SUCCESS (814/9, 410/56).
  - `aa90b82`: run 41669956153, SUCCESS.
  - `2462c10`: run 41675359231, SUCCESS (815/9, 417/56).
- **Mutation checks:** 21 mutants; all caught except one equivalent (the UPDATE `revision =` condition, unreachable behind the row lock and pre-check).
- **Repeat runs:** concurrency test 30 runs with 0 failures; new frontend tests 3–5 runs each, all passing.
- **Independent review** (separate reviewer agent, fresh context; no human or different-model review):
  1. `cf3771e`: 1 High, 1 Medium, 4 Low. All resolved; L3 accepted.
  2. `aa90b82`: 1 Medium (a step queued during the reload was dropped) and 1 Low (half-measure on listening position). Both resolved.
  3. `2462c10`: no findings.

## Open items

- **Owner decision:** Listening position follows the tab in use (last tab used sets the resume point). Guided progress is monotonic on conflict.
- **Pre-existing gap:** in cloud, `/api/chunks/memory/active-recall` reads the Personal local stores. The cloud UI no longer calls it; it still needs to move behind an owner-scoped store (Workstream B/F).
- **Accepted (L3):** the same-user focus re-check sends requests during `/me`. CSRF is bound to the session and private GETs are owner-checked.
- **Not done:**
  - cloud dictionary, recall and Study Bank saves (B-3/B-4);
  - Shadowing in cloud;
  - lesson creation and import (Workstream D);
  - realtime voice (Workstream C);
  - browser E2E (B-7).
- **Issue #58:** items 1–4 are implemented across #81 and #82. Keep it open until the owner accepts #82.

## Next

- Owner: review #82 and the comment drafts, and decide on the Listening-position item.
- Next increment per `docs/PHASE8-WORKSTREAM-B-PLAN.md`: **B-3**, a cloud dictionary adapter with a synthetic fixture lexicon plus the AI Chunk organizer on the cloud Library. Branch it from #82's head.

## Resume

```bash
cd ~/Downloads/language-ai-claude-p8-b2 && git fetch origin && git status
gh pr view 82 && gh pr view 81 && gh issue view 58 && gh issue view 75 --comments
# disposable PostgreSQL only; never a real database
AI_MODE=mock TEST_POSTGRES_DSN=postgresql://<user>@127.0.0.1:<port>/<scratch_db> \
  PYTHONPATH=backend <venv>/bin/python -m pytest -q backend/tests
(cd frontend && npm ci && npm test && npm run lint && npm run build)
```
