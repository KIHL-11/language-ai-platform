#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY="$(cd "${SCRIPT_DIR}/.." && pwd)"
FRONTEND="${REPOSITORY}/frontend"
BACKEND="${REPOSITORY}/backend"
PYTHON="${BACKEND}/.venv/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Backend virtual environment is missing. Create backend/.venv and install backend/requirements.txt." >&2
  exit 1
fi
if [[ ! -x "${FRONTEND}/node_modules/.bin/vite" ]]; then
  echo "Frontend dependencies are missing. Run: cd \"${FRONTEND}\" && npm ci" >&2
  exit 1
fi

echo "Building the Language AI personal frontend..."
(cd "${FRONTEND}" && npm run build)

if [[ ! -f "${FRONTEND}/dist/index.html" ]]; then
  echo "Frontend build did not create frontend/dist/index.html." >&2
  exit 1
fi

echo "Checking the FastAPI personal-mode routes..."
(cd "${BACKEND}" && "${PYTHON}" -c '
from fastapi.testclient import TestClient
from app import FRONTEND_DIST, app
paths = {route.path for route in app.routes}
assert FRONTEND_DIST.joinpath("index.html").is_file()
assert "/api/health" in paths
assert "/media/{media_path:path}" in paths
assert "/{frontend_path:path}" in paths
client = TestClient(app)
assert client.get("/api/health").json()["ok"] is True
root = client.get("/")
assert root.status_code == 200 and "text/html" in root.headers["content-type"]
')

echo "Personal application build is ready at ${FRONTEND}/dist."
