#!/usr/bin/env python3
"""Generate the local-only per-user LaunchAgent without embedding secrets."""

import argparse
import plistlib
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from services.dictionary.release_manifest import validate_rollback_release  # noqa: E402
from services.dictionary.release_store import current_build_id, resolve_layout  # noqa: E402


LABEL = "com.language-ai-platform"
TOOL_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


DICTIONARY_NAME = "dictionary.sqlite3"
SIDECAR_NAME = "dictionary.forms.sqlite3"


def dictionary_environment(dictionary_current: Path | None) -> dict[str, str]:
    """Versioned dictionary paths always go through the stable current symlink."""
    if dictionary_current is None:
        return {}
    if not dictionary_current.is_symlink():
        raise SystemExit("--dictionary-current must be the dictionary_current symlink")
    layout = resolve_layout(dictionary_current.parent)
    build_id = current_build_id(layout)
    if build_id is None:
        raise SystemExit("--dictionary-current does not name a release")
    validate_rollback_release(layout.releases, build_id)
    link = dictionary_current.parent.resolve(strict=True) / dictionary_current.name
    return {
        "DICTIONARY_DB_PATH": str(link / DICTIONARY_NAME),
        "DICTIONARY_FORMS_INDEX_PATH": str(link / SIDECAR_NAME),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("python", type=Path)
    parser.add_argument("backend", type=Path)
    parser.add_argument("log_dir", type=Path)
    parser.add_argument(
        "--dictionary-current",
        type=Path,
        help="backend/storage/dictionary_current symlink; preserves versioned dictionary paths",
    )
    arguments = parser.parse_args()
    output, python, backend, log_dir = (
        arguments.output, arguments.python, arguments.backend, arguments.log_dir
    )
    launch_agent = {
        "Label": LABEL,
        "ProgramArguments": [
            str(python),
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        "WorkingDirectory": str(backend),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        "EnvironmentVariables": {
            "PATH": TOOL_PATH,
            "PYTHONUNBUFFERED": "1",
            **dictionary_environment(arguments.dictionary_current),
        },
        "StandardOutPath": str(log_dir / "backend.stdout.log"),
        "StandardErrorPath": str(log_dir / "backend.stderr.log"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as plist_file:
        plistlib.dump(launch_agent, plist_file, sort_keys=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
