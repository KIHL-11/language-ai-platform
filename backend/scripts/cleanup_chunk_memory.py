"""Write a cleaned copy of an existing chunk bank; never overwrite the source."""

import argparse
import json
from pathlib import Path

from services.retrieval.chunk_quality import plan_chunk_cleanup
from services.retrieval.chunk_store import ChunkStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.destination.resolve()
    if source == destination:
        parser.error("destination must differ from source")
    if not source.is_file():
        parser.error("source must be an existing chunk bank file")
    if destination.exists():
        parser.error("destination already exists")

    plan = plan_chunk_cleanup(ChunkStore(source).read_chunks())
    output_store = ChunkStore(destination)
    output_store.add_chunks(plan.chunks)
    print(
        json.dumps(
            {
                "cleaned_ids": plan.cleaned_ids,
                "removed_ids": plan.removed_ids,
                "collision_ids": plan.collision_ids,
                "destination": str(destination),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
