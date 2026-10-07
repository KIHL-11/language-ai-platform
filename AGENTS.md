# Language AI Platform — Codex Instructions

## Project Baseline

Verified cloud baseline:

- Backend: 47 pytest tests passed
- Frontend: 94 tests passed
- Frontend lint passed
- Frontend production build passed

Current cloud environment:

- Python 3.12
- Node 24

Local macOS development historically uses Python 3.11.

Both environments are currently verified.

## Architecture

Backend:

- FastAPI
- media extraction
- subtitles / Whisper fallback
- Lesson schema
- mock/AI analysis
- semantic retrieval
- Realtime temporary credential endpoint

Frontend:

- React
- TypeScript
- Vite

Training stages:

1. active_recall
2. blind_listening
3. comprehension
4. mini_dictation
5. shadowing
6. retell
7. live_dialogue

## Default Development Mode

Use:

```text
AI_MODE=mock
```

Local Text Practice is the default mode.

Realtime Voice must never start automatically.

Normal Realtime product limits:

- 5 learner turns
- 3 minutes

## Required Development Workflow

For every engineering task:

1. Inspect repository state first.
2. Read relevant code and tests before changing anything.
3. Reproduce the issue where possible.
4. Identify root cause.
5. Implement the smallest correct change.
6. Add or update tests for behavior changes.
7. Run relevant regression tests.
8. Inspect git diff.
9. Perform self-review for regressions, security, portability, and dead code.
10. Fix issues found during self-review.
11. Re-run tests.
12. Report exact results.

Do not stop merely to recommend a fix when it can be safely implemented and tested.

## Security Rules

Never:

- print API keys or credentials
- commit `.env` files
- commit `.env.local`
- commit cookies
- commit `.venv`
- commit `node_modules`
- commit `dist`
- commit generated runtime media
- put server secrets in `VITE_*` variables
- make paid OpenAI API requests without explicit user authorization
- start a real Realtime voice session without explicit user authorization
- automatically run destructive dependency upgrades
- run `npm audit fix --force` automatically
- weaken tests merely to obtain green results

`OPENAI_API_KEY` must remain backend-only.

## Realtime Rules

Realtime development must use mocks unless explicitly authorized.

Do not:

- request a real client secret
- request microphone permission
- create WebRTC sessions
- make paid realtime calls

unless the user explicitly authorizes that exact task.

## Git Rules

Before work:

```bash
git status
```

After work:

```bash
git status
git diff
```

Do not rewrite main history.

Keep commits focused.

Do not push secrets.

Cloud tasks may work on task branches.

Important work must ultimately be committed and pushed before another independent cloud task relies on it.

## Portability

Avoid:

- hardcoded macOS paths
- hardcoded Windows paths
- environment-specific absolute paths

Keep backend compatible with verified Python 3.11 and 3.12 unless intentionally changed.

## Completion Report

At the end of every task report:

- problems found
- root cause
- files changed
- tests run
- exact test counts
- lint/build status
- self-review findings
- security impact
- remaining risks
- recommended next task

Do not make any other project changes.
