#!/usr/bin/env bash
set -u

LABEL="com.language-ai-platform"
DOMAIN="gui/$(id -u)"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
APP_PATH="${HOME}/Applications/Language AI.app"
LOG_DIR="${HOME}/Library/Logs/LanguageAIPlatform"

if [[ -f "${PLIST_PATH}" ]]; then
  echo "LaunchAgent: installed (${PLIST_PATH})"
else
  echo "LaunchAgent: not installed (${PLIST_PATH})"
fi

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  echo "Service: loaded"
  launchctl print "${DOMAIN}/${LABEL}" 2>/dev/null | \
    awk '/state =|pid =|last exit code =/ { sub(/^[[:space:]]+/, ""); print "  " $0 }'
else
  echo "Service: not loaded"
fi

if curl --fail --silent --max-time 2 http://127.0.0.1:8000/api/health >/dev/null; then
  echo "Health: healthy (http://127.0.0.1:8000/api/health)"
else
  echo "Health: unavailable (http://127.0.0.1:8000/api/health)"
fi

echo "Application: ${APP_PATH}"
echo "Logs: ${LOG_DIR}"
