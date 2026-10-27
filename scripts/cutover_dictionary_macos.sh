#!/usr/bin/env bash
# Controlled dictionary release lifecycle for the Language AI LaunchAgent.
#
#   scripts/cutover_dictionary_macos.sh status
#   scripts/cutover_dictionary_macos.sh legacy-rollback [--dry-run]
#   scripts/cutover_dictionary_macos.sh cutover --build-id ID --source-root DIR \
#       [--dry-run | --confirm "CUTOVER ID"]
#   scripts/cutover_dictionary_macos.sh rollback --build-id ID \
#       [--dry-run | --confirm "ROLLBACK ID"]
#
# This wrapper never downloads, imports, or deletes data. See
# docs/DQ-02-PHASE7A-MIGRATION-RUNBOOK.md before running a non-dry-run command.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Dictionary cutover is only supported on macOS." >&2
  exit 1
fi
if ! command -v launchctl >/dev/null 2>&1; then
  echo "Required macOS command is unavailable: launchctl" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPOSITORY="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
PYTHON="${REPOSITORY}/backend/.venv/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Backend virtual environment is missing: ${PYTHON}" >&2
  exit 1
fi

exec "${PYTHON}" "${REPOSITORY}/backend/scripts/dictionary_cutover.py" \
  --storage-root "${REPOSITORY}/backend/storage" "$@"
