#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.dictionary.provider import import_kaikki_sources  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the local Language AI dictionary index from Kaikki/Wiktextract JSONL.",
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--content-license",
        required=True,
        help="Verified content-license label for the downloaded dictionary editions",
    )
    parser.add_argument("--english-input", type=Path)
    parser.add_argument("--english-source-url")
    parser.add_argument("--german-input", type=Path)
    parser.add_argument("--german-source-url")
    args = parser.parse_args()
    sources = []
    if args.english_input:
        if not args.english_source_url:
            parser.error("--english-source-url is required with --english-input")
        sources.append((args.english_input, "en", args.english_source_url))
    if args.german_input:
        if not args.german_source_url:
            parser.error("--german-source-url is required with --german-input")
        sources.append((args.german_input, "de", args.german_source_url))
    if not sources:
        parser.error("at least one input is required")
    count = import_kaikki_sources(
        sources,
        args.database,
        content_license=args.content_license,
    )
    print(f"Imported {count} English/German lexical entries into {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
