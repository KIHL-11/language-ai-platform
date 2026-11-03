# Checkpoint: P8-05B2 account identity (Claude Code, 2026-11-03)

Manual checkpoint (no `handoff.py` lease; that tool lives only in Draft PR #77).
Status of record is Issue #75; this file is a pointer, not a status source.

## Where things are

- Branch `claude/p8-05b2-account-identity`, stacked on P8-05B1
  (`claude/p8-05b-email-auth` at `abf95ad`), which is based on main `eb7e902`.
- Code head `88ec86247936693cb15d29039619b666f62b3bf9` (this file is committed on top).
- Draft PR #79, base `claude/p8-05b-email-auth`. Merge #78 first; then retarget
  #79 to main. Nothing is merged or deployed.
- Design: `docs/P8-05B2-ACCOUNT-IDENTITY.md` (includes the Workstream F
  dependency). Email login: `docs/P8-05B-EMAIL-LOGIN.md`. Tracker row A: In progress.

## Evidence at code head

- Local (macOS, Python 3.11 venv, disposable PostgreSQL 17, Node 26): backend
  784 passed / 9 skipped; frontend 391 passed / 55 files; oxlint clean; build OK.
- Hosted CI: Phase 8 workflow run 41576214098 on `88ec862`: SUCCESS (backend 784/9, frontend 391/55).
- 14 security-guard mutation checks all fail the suite when applied.
- Independent review: one separate reviewer agent (fresh context, not the author
  session) reviewed `abf95ad..ef22710`. No Critical/High. Verified Medium M1
  (WeChat login racing unlink) and plausible M2 (re-check lost on focus) fixed
  in `88ec862` with tests; Lows fixed or documented. The fixes themselves have
  not had a second review. No human or different-model review has been done.

## Open, needing a decision or another workstream

- Real WeChat adapter: App ID, secret, callback domain, `unionid` decision and
  owner approval. Any `CLOUD_WECHAT_*` variable fails closed today.
- Real mail adapter (credentials, sending domain, data-processing decision).
- Workstream F: trusted proxy definition, per-IP/global limits (also for
  `/api/account/wechat/start`). Until then anyone can use an address's hourly
  code quota (up to 1 h delay).
- Product: WeChat never creates an account; email cannot be unlinked or changed yet.
- Browser session rows are not purged; purge job needs a scheduler and DELETE grants.

## Next steps in Workstream A

1. Second review pass over `ef22710..88ec862`.
2. Email add/change (then email unlink with the last-method guard).
3. Session-row purge; account deletion/export hooks if Workstream A owns them.

## Resume

```bash
git fetch origin
git switch claude/p8-05b2-account-identity && git pull --ff-only
gh pr view 79 && gh pr view 78 && gh issue view 75 --comments
python -m venv <venv> && <venv>/bin/pip install -r backend/requirements.txt
# disposable PostgreSQL only; never a real database
AI_MODE=mock TEST_POSTGRES_DSN=postgresql://<user>@127.0.0.1:<port>/<scratch_db> \
  PYTHONPATH=backend <venv>/bin/python -m pytest -q backend/tests
(cd frontend && npm ci && npm test && npm run lint && npm run build)
```
