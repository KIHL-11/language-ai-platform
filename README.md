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

## Migration status

See [docs/HANDOFF-2026-10-06.md](docs/HANDOFF-2026-10-06.md) for the verified
Windows-to-macOS migration baseline and the remaining Realtime smoke-test task.
