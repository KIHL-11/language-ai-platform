# Checkpoint: P8-05B1 email login (Claude Code, 2026-11-03)

Manual checkpoint. The `handoff.py` tool lives only in Draft PR #77, so no
lease or machine-verified record exists for this branch. Status of record is
Issue #75; this file is a pointer, not a status source.

## Where things are

- Branch `claude/p8-05b-email-auth`, based on main `eb7e902`.
- Code head `dcd502442b8d8f79c2e0bbce8ee3e9ec717b8013` (this file is committed on top).
- Draft PR #78 against main. Not merged, not deployed.
- Design and limits: `docs/P8-05B-EMAIL-LOGIN.md`. Tracker row A: In progress.

## Evidence at code head

- Local (macOS, Python 3.11 venv, disposable PostgreSQL 17, Node 26): backend
  760 passed / 9 skipped; frontend 381 passed / 54 files; oxlint clean; build OK.
- Hosted CI: see the Phase 8 workflow run for this branch head on PR #78.
- Independent review: two separate reviewer agents (fresh context, not the
  author session) reviewed eff48f0 and a10f579. No Critical/High. Verified
  Medium findings fixed in a10f579 and dcd5024. A human or different-model
  review has not been done.

## Known risks (accepted or open)

- No per-IP/global limits until the Workstream F trusted-proxy decision:
  cross-address code spraying; hourly quota exhaustion; **owner decision
  pending:** anyone can block new sign-ins for an address for up to 24 h by
  spending its 10 daily failed attempts.
- A 429 reveals a recent code request for an address.
- Pre-login session revocation failure leaves that session valid until expiry.
- Other tabs are not notified after sign-in.

## Next steps in Workstream A

1. Owner decision on the 24 h lockout trade-off (keep, or per-browser cap with
   a higher per-address ceiling).
2. WeChat OAuth with simulated provider responses.
3. Account link/unlink with fresh proof for both identities.
4. Purge of old challenges and staging outbox files.

## Resume

```bash
git fetch origin
git switch claude/p8-05b-email-auth && git pull --ff-only
gh pr view 78 && gh issue view 75 --comments
python -m venv <venv> && <venv>/bin/pip install -r backend/requirements.txt
# disposable PostgreSQL only; never a real database
AI_MODE=mock TEST_POSTGRES_DSN=postgresql://<user>@127.0.0.1:<port>/<scratch_db> \
  PYTHONPATH=backend <venv>/bin/python -m pytest -q backend/tests
(cd frontend && npm ci && npm test && npm run lint && npm run build)
```
