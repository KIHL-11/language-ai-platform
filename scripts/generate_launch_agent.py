#!/usr/bin/env python3
"""Generate the local-only per-user LaunchAgent without embedding secrets."""

import plistlib
import sys
from pathlib import Path


LABEL = "com.language-ai-platform"
TOOL_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def main() -> int:
    if len(sys.argv) != 5:
        print(
            "usage: generate_launch_agent.py OUTPUT PYTHON BACKEND LOG_DIR",
            file=sys.stderr,
        )
        return 2

    output, python, backend, log_dir = map(Path, sys.argv[1:])
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
