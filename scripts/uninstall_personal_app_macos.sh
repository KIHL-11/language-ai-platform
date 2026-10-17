#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This uninstaller is only supported on macOS." >&2
  exit 1
fi

LABEL="com.language-ai-platform"
DOMAIN="gui/$(id -u)"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
APP_PATH="${HOME}/Applications/Language AI.app"

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  launchctl bootout "${DOMAIN}/${LABEL}"
fi
rm -f "${PLIST_PATH}"

if [[ "${APP_PATH}" != "${HOME}/Applications/Language AI.app" ]]; then
  echo "Refusing to remove an unexpected application path." >&2
  exit 1
fi
rm -rf "${APP_PATH}"

echo "Language AI LaunchAgent and launcher application were removed."
echo "Repository files, local learning data, virtual environment, media, and logs were preserved."
