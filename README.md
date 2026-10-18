# Language AI Platform

Language AI Platform turns YouTube and other media into structured English or
German language lessons with a guided seven-stage training flow.

## Architecture

### Backend

The FastAPI backend provides:

- media extraction from URLs and uploaded content
- subtitle extraction with local Whisper fallback
- a validated Lesson schema
- mock-first or configured AI lesson analysis
- local semantic chunk storage and retrieval
- a persistent local Learning Library with durable Lesson snapshots and audio
- an explicit Words & Chunks Study Bank with source provenance
- a persistent Listening Workspace over saved Library media
- a server-side endpoint for temporary OpenAI Realtime credentials

### Frontend

The frontend uses React, TypeScript, and Vite. It guides learners through:

1. Active Recall
2. Blind Listening
3. Comprehension
4. Mini Dictation
5. Shadowing
6. Retell
7. Live Dialogue

Live Dialogue supports Local Text Practice and optional Realtime Voice Practice.

### Personal session persistence

The browser stores the current validated lesson and canonical training progress
under the versioned local key `language_ai_personal_session_v1`. Refreshing the
page restores the lesson, stage, and sentence position. The saved lesson keeps
only its backend-relative media URL; it never stores audio blobs, recordings,
credentials, or Realtime secrets.

The browser session is the active lesson, while My Library is the durable
collection. Saving a Lesson copies a safe local training-audio asset into
Library-owned storage when it is available; otherwise the Lesson and transcript
are still saved with audio marked unavailable. See
[docs/LEARNING-LIBRARY.md](docs/LEARNING-LIBRARY.md) for storage, recovery, and
manual smoke-test details.

Lesson keywords and chunks remain candidates until the learner explicitly saves
them. Curated items live in the local Study Bank; only active curated chunks are
eligible for normal Active Recall. See
[docs/STUDY-BANK.md](docs/STUDY-BANK.md) for curation, provenance, legacy Chunk
Memory compatibility, and the M4.2 smoke plan.

Saved media can also be reopened in the standalone Listening Workspace without
regenerating a Lesson. It uses the existing timestamped Lesson transcript,
remote YouTube display when available, persistent audio fallback, deterministic
dictation comparison, and Study Bank candidates. See
[docs/LISTENING-WORKSPACE.md](docs/LISTENING-WORKSPACE.md).

## Development safety

- `AI_MODE=mock` is the normal local-development baseline.
- Local Text Practice is the default Live Dialogue mode.
- Realtime Voice Practice never starts automatically.
- `OPENAI_API_KEY` is backend-only.
- Never place server keys in `VITE_*` variables or frontend files.

## macOS setup

### Backend

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
python -m uvicorn app:app --port 8000
```

The health endpoint is available at `http://127.0.0.1:8000/api/health`.

### Frontend

In a separate terminal:

```bash
cd frontend
npm ci
npm test
npm run lint
npm run build
npm run dev
```

The development server is available at `http://127.0.0.1:5173` by default.

## macOS personal desktop mode

After the normal backend and frontend dependencies are installed, macOS users
can build the frontend, register a local-only background service, and create
`~/Applications/Language AI.app` with:

```bash
./scripts/install_personal_app_macos.sh
```

Daily use then needs only the generated application; FastAPI serves both the
React build and existing APIs at `http://127.0.0.1:8000`. See
[docs/PERSONAL-DESKTOP-MODE.md](docs/PERSONAL-DESKTOP-MODE.md) for update,
status, uninstall, localStorage migration, and post-merge smoke-test details.

## Migration status

See [docs/HANDOFF-2026-10-06.md](docs/HANDOFF-2026-10-06.md) for the verified
Windows-to-macOS migration baseline and the remaining Realtime smoke-test task.
