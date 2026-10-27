#!/usr/bin/env python3
"""Read-only READY eligibility validation for a prepared dictionary release."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.dictionary.release_manifest import (  # noqa: E402
    ManifestValidationError,
    validate_ready_eligibility,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--releases-root", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        manifest = validate_ready_eligibility(
            arguments.release,
            arguments.releases_root,
            arguments.source_root,
        )
    except (ManifestValidationError, OSError, ValueError) as exc:
        print(f"Dictionary release is not READY-eligible: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "ready_eligible": True,
        "build_id": manifest["build_id"],
        "importer_git_sha": manifest["repository"]["importer_git_sha"],
        "rollback_build_id": manifest["rollback"]["previous_build_id"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
