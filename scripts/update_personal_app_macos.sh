#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This updater is only supported on macOS." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.language-ai-platform"
DOMAIN="gui/$(id -u)"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/LanguageAIPlatform"

if [[ ! -f "${PLIST_PATH}" ]]; then
  echo "Language AI is not installed. Run scripts/install_personal_app_macos.sh first." >&2
  exit 1
fi

"${SCRIPT_DIR}/build_personal_app.sh"
if ! launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  launchctl bootstrap "${DOMAIN}" "${PLIST_PATH}"
fi
launchctl kickstart -k "${DOMAIN}/${LABEL}"

for _attempt in {1..30}; do
  if curl --fail --silent --max-time 1 http://127.0.0.1:8000/api/health >/dev/null; then
    echo "Language AI was rebuilt, restarted, and is healthy."
    exit 0
  fi
  sleep 1
done

echo "Language AI did not become healthy after the update." >&2
echo "Inspect: ${LOG_DIR}/backend.stderr.log" >&2
exit 1
