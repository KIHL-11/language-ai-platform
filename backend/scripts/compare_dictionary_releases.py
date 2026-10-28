#!/usr/bin/env python3
"""Read-only comparison of two (optionally three) dictionary databases.

Writes comparison.json and comparison.md with two independent verdicts:
Dictionary Data Quality (sense-level SQLite checks) and Context Sense Selection
(gold sentence cases through DictionaryService). Never writes to a database and
refuses to write its report inside dictionary_releases/.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.dictionary.data_quality import DataQualityError, load_data_quality_cases  # noqa: E402
from services.dictionary.release_comparison import compare_releases, markdown_summary  # noqa: E402
from services.dictionary.release_quality import QualityGateError, load_gold_set  # noqa: E402
from services.dictionary.release_store import RELEASES_DIRNAME  # noqa: E402


def top_english_words(count: int) -> list[str]:
    if count <= 0:
        return []
    try:
        import wordsegment
    except ImportError:
        return []
    wordsegment.load()
    ranked = sorted(wordsegment.UNIGRAMS.items(), key=lambda item: -item[1])
    return [word for word, _ in ranked if word.isalpha()][:count]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--counterfactual", type=Path,
                        help="old importer run on the candidate's source snapshot")
    parser.add_argument("--data-quality-cases", type=Path, required=True)
    parser.add_argument("--gold-set", type=Path, required=True)
    parser.add_argument("--build-report", type=Path)
    parser.add_argument("--english-source", type=Path)
    parser.add_argument("--german-source", type=Path)
    parser.add_argument("--common-words-top", type=int, default=1000)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output_dir.resolve()
    if RELEASES_DIRNAME in output.parts:
        print("Refusing to write a report inside dictionary_releases/", file=sys.stderr)
        return 1
    try:
        cases = load_data_quality_cases(arguments.data_quality_cases, require_approval=False)
        gold = load_gold_set(arguments.gold_set)
    except (DataQualityError, QualityGateError) as exc:
        print(f"Comparison inputs are invalid: {exc}", file=sys.stderr)
        return 1
    words = {language: list(values) for language, values in gold["common_words"].items()}
    words["en"] = list(dict.fromkeys(words["en"] + top_english_words(arguments.common_words_top)))
    sources = {}
    if arguments.english_source:
        sources["en"] = arguments.english_source
    if arguments.german_source:
        sources["de"] = arguments.german_source
    build_report = json.loads(arguments.build_report.read_text()) if arguments.build_report else None
    report = compare_releases(
        baseline=arguments.baseline,
        candidate=arguments.candidate,
        counterfactual=arguments.counterfactual,
        data_quality_cases=cases,
        gold=gold,
        common_words=words,
        build_report=build_report,
        sources=sources or None,
    )
    report["inputs"] = {
        "data_quality_cases_approved": cases.get("approved") is True,
        "common_words": {language: len(values) for language, values in words.items()},
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "comparison.md").write_text(markdown_summary(report), encoding="utf-8")
    print(markdown_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
