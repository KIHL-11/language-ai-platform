#!/usr/bin/env python3
"""Prepare one immutable dictionary release candidate (never switches the backend).

Imports already-downloaded, hash-verified Kaikki gzip artifacts into a new
``dictionary_releases/<build-id>`` directory, validates it, builds its forms
Sidecar, writes manifest/quality/build reports, and writes READY last.
``--dry-run`` performs only the read-only preflight and creates nothing.
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from services.dictionary.release_prepare import (  # noqa: E402
    PrepareError,
    prepare_release,
    repository_state,
)
from services.dictionary.release_store import ReleaseStoreError, resolve_layout  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--storage-root", type=Path, default=BACKEND / "storage")
    parser.add_argument("--source-root", type=Path, required=True,
                        help="Directory holding the verified .jsonl.gz artifacts")
    parser.add_argument("--source-evidence", type=Path, required=True,
                        help="JSON with evidence_version=1 and the two manifest source records")
    parser.add_argument("--gold-set", type=Path, required=True,
                        help="Owner-approved production gold set (gold_set_version=1)")
    parser.add_argument("--rollback-build-id", required=True)
    parser.add_argument("--projected-dictionary-bytes", type=int, default=0)
    parser.add_argument("--projected-sidecar-bytes", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    try:
        layout = resolve_layout(arguments.storage_root, create=not arguments.dry_run)
        result = prepare_release(
            layout=layout,
            source_root=arguments.source_root,
            source_evidence=arguments.source_evidence,
            gold_set=arguments.gold_set,
            rollback_build_id=arguments.rollback_build_id,
            repository=repository_state(BACKEND.parent),
            dry_run=arguments.dry_run,
            projected_dictionary_bytes=arguments.projected_dictionary_bytes,
            projected_sidecar_bytes=arguments.projected_sidecar_bytes,
        )
    except (PrepareError, ReleaseStoreError, OSError, sqlite3.Error, RuntimeError,
            ValueError, subprocess.CalledProcessError) as exc:
        print(f"Dictionary release was not prepared: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
