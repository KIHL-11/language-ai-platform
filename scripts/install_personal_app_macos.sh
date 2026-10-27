#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is only supported on macOS." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND="${REPOSITORY}/backend"
PYTHON="${BACKEND}/.venv/bin/python"
LABEL="com.language-ai-platform"
DOMAIN="gui/$(id -u)"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/LanguageAIPlatform"
APP_PATH="${HOME}/Applications/Language AI.app"

for command in launchctl osacompile curl; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "Required macOS command is unavailable: ${command}" >&2
    exit 1
  fi
done

"${SCRIPT_DIR}/build_personal_app.sh"
mkdir -p "$(dirname "${PLIST_PATH}")" "${LOG_DIR}" "$(dirname "${APP_PATH}")"

# Keep a completed dictionary cutover: only carry the versioned dictionary
# paths over when the existing LaunchAgent already uses dictionary_current.
# A bare symlink alone (e.g. left by an interrupted run) never redirects the
# service; scripts/cutover_dictionary_macos.sh is the only way to switch.
DICTIONARY_ARGUMENTS=()
if [[ -L "${BACKEND}/storage/dictionary_current" && -f "${PLIST_PATH}" ]] \
  && grep -q "storage/dictionary_current/dictionary.sqlite3" "${PLIST_PATH}"; then
  DICTIONARY_ARGUMENTS=(--dictionary-current "${BACKEND}/storage/dictionary_current")
fi

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  launchctl bootout "${DOMAIN}/${LABEL}"
fi

"${PYTHON}" "${SCRIPT_DIR}/generate_launch_agent.py" \
  "${PLIST_PATH}" "${PYTHON}" "${BACKEND}" "${LOG_DIR}" "${DICTIONARY_ARGUMENTS[@]+"${DICTIONARY_ARGUMENTS[@]}"}"
launchctl bootstrap "${DOMAIN}" "${PLIST_PATH}"
launchctl enable "${DOMAIN}/${LABEL}"
launchctl kickstart -k "${DOMAIN}/${LABEL}"

if [[ "${APP_PATH}" != "${HOME}/Applications/Language AI.app" ]]; then
  echo "Refusing to replace an unexpected application path." >&2
  exit 1
fi
rm -rf "${APP_PATH}"
osacompile -o "${APP_PATH}" "${SCRIPT_DIR}/Language AI.applescript"

for _attempt in {1..30}; do
  if curl --fail --silent --max-time 1 http://127.0.0.1:8000/api/health >/dev/null; then
    echo "Language AI is installed and healthy. Open: ${APP_PATH}"
    exit 0
  fi
  sleep 1
done

echo "Language AI was installed, but the backend did not become healthy." >&2
echo "Inspect: ${LOG_DIR}/backend.stderr.log" >&2
exit 1
