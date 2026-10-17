# Personal desktop mode (macOS)

Personal desktop mode builds the React application once and serves it from the
local FastAPI process. The installed per-user LaunchAgent listens only on
`127.0.0.1:8000`; no Vite server or open Terminal window is needed for daily
use.

Development mode remains unchanged: Vite runs on `http://localhost:5173` and
uses `http://127.0.0.1:8000` for API requests. A production build served by
FastAPI uses same-origin `/api` and `/media` URLs. An explicit
`VITE_API_BASE_URL` still overrides either default at build time.

## Prerequisites

- macOS with the repository checked out locally
- the existing `backend/.venv` with backend requirements installed
- frontend dependencies installed with `npm ci`
- macOS built-in `launchctl` and `osacompile`
- `curl`; expected media tools such as `ffmpeg` may be installed through
  Homebrew in `/opt/homebrew/bin` or `/usr/local/bin`

No API keys are copied into the LaunchAgent or application bundle. Existing
backend configuration continues to load from `backend/.env` in the application.

## Install and daily use

From the repository root, run the one-time installer:

```bash
./scripts/install_personal_app_macos.sh
```

This creates:

- LaunchAgent: `~/Library/LaunchAgents/com.language-ai-platform.plist`
- launcher: `~/Applications/Language AI.app`
- logs: `~/Library/Logs/LanguageAIPlatform/backend.stdout.log` and
  `backend.stderr.log`

The LaunchAgent runs this local-only command from `backend/`:

```text
backend/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

At login the service starts in the background. For daily use, open
`~/Applications/Language AI.app`. The launcher checks `/api/health`, loads or
kickstarts the LaunchAgent when needed, waits at most 30 seconds, and opens
`http://127.0.0.1:8000` in the default browser. If startup fails, it shows a
dialog pointing to the stderr log without exposing configuration values.

## Update, status, and uninstall

After explicitly updating the source checkout, rebuild and restart without a
Git operation:

```bash
./scripts/update_personal_app_macos.sh
```

Inspect installation, service, health, application, and log locations:

```bash
./scripts/status_personal_app_macos.sh
```

Remove only the registered service, plist, and generated launcher:

```bash
./scripts/uninstall_personal_app_macos.sh
```

Uninstall preserves the repository, backend configuration, virtual
environment, Chunk Memory, learner data, generated media, browser storage, and
logs.

## Browser storage migration note

Browser localStorage belongs to an exact origin. A session previously saved at
`http://localhost:5173` is not visible at the personal-mode origin
`http://127.0.0.1:8000`. The first personal-mode launch can therefore start
with a new browser session and learner identity. Backend Chunk Memory is stored
separately and is not changed or deleted by installation, update, or uninstall.

Lesson media remains temporary backend state. Refresh works while the referenced
file remains in `backend/media`, but existing cleanup or a missing file can make
an old lesson audio URL unavailable.

## Required post-merge macOS smoke test

Do not consider the launcher verified until this is run on the target Mac:

1. Update the checkout explicitly and install:

   ```bash
   cd /path/to/language-ai-platform
   git switch main
   git pull --ff-only
   npm --prefix frontend ci
   backend/.venv/bin/python -m pip install -r backend/requirements.txt
   ./scripts/install_personal_app_macos.sh
   ./scripts/status_personal_app_macos.sh
   ```

2. Close every Terminal window.
3. In Finder, double-click `~/Applications/Language AI.app`.
4. Confirm the default browser opens `http://127.0.0.1:8000` and
   `http://127.0.0.1:8000/api/health` reports healthy.
5. Generate or open a Lesson and confirm its `/media/...` audio plays.
6. Complete an Active Recall action and confirm Chunk Memory reads and writes.
7. Enter a later sentence in training, refresh the browser, and confirm the
   Lesson, stage, and sentence position return.
8. Log out and back in, then confirm the application works without opening a
   Terminal window.
9. Inspect service and logs if needed:

   ```bash
   cd /path/to/language-ai-platform
   ./scripts/status_personal_app_macos.sh
   tail -n 100 ~/Library/Logs/LanguageAIPlatform/backend.stderr.log
   tail -n 100 ~/Library/Logs/LanguageAIPlatform/backend.stdout.log
   ```

10. Confirm `lsof -nP -iTCP:8000 -sTCP:LISTEN` reports only a
    `127.0.0.1:8000` listener.
