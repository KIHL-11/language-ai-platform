#!/usr/bin/env python3
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.dictionary.forms_sidecar import (  # noqa: E402
    build_forms_index,
    default_forms_index_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the disposable indexed forms sidecar without modifying the "
            "source dictionary SQLite database."
        ),
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="Defaults to <database stem>.forms.sqlite3 beside the source database",
    )
    args = parser.parse_args()
    output = args.output or default_forms_index_path(args.database)
    started = time.perf_counter()
    result = build_forms_index(args.database, output)
    elapsed_seconds = time.perf_counter() - started
    print(json.dumps({
        "source": str(args.database.resolve()),
        "output": str(output.resolve()),
        "source_rows_scanned": result.source_rows_scanned,
        "forms_indexed": result.forms_indexed,
        "entries_indexed": result.entries_indexed,
        "size_bytes": result.size_bytes,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
