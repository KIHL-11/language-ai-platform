"""Read-only old-vs-new dictionary comparison with two independent verdicts.

* Dictionary Data Quality — sense-level SQLite checks (``data_quality``).
* Context Sense Selection — the gold sentence cases through
  ``DictionaryService`` (``release_quality.evaluate_gold_cases``).

Coverage change is decomposed when a counterfactual database (the *old*
importer run on the *new* source snapshot) is supplied:

    baseline (old importer, old snapshot)
      -> counterfactual (old importer, new snapshot)   = snapshot effect
      -> candidate      (new importer, new snapshot)   = importer effect

Nothing here writes to any database.
"""

import gzip
import json
import sqlite3
from pathlib import Path

from services.dictionary.data_quality import broadcast_profile, evaluate_data_quality
from services.dictionary.provider import (
    _SENSE_ALIGNMENT_GENERIC_TOKENS,
    _SENSE_ALIGNMENT_STOPWORDS,
    _aligned_entry_translations,
    _strings,
    entry_translation_records,
    lexical_tokens,
    normalize_lexical,
)
from services.dictionary.release_manifest import dictionary_statistics
from services.dictionary.recovery import (
    chinese_script_rejection,
    equivalence_groups,
    raw_translation_hints,
    single_sense_fallback_allowed,
)
from services.dictionary.release_quality import evaluate_gold_cases

_LANGUAGES = ("en", "de")


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def word_chinese(database: Path, language: str, words: list[str]) -> dict[str, list[str]]:
    """Chinese values stored on any sense of any entry with this normalized lemma."""
    connection = _readonly(database)
    try:
        output = {}
        for word in words:
            values: list[str] = []
            for (translations_json,) in connection.execute(
                """SELECT senses.translations_json FROM entries
                   JOIN senses ON senses.entry_id = entries.id
                   WHERE entries.language = ? AND entries.normalized_lemma = ?""",
                (language, normalize_lexical(word)),
            ):
                for value in json.loads(translations_json).get("zh-CN", []):
                    if value not in values:
                        values.append(value)
            output[word] = values
        return output
    finally:
        connection.close()


def _coverage(statistics: dict) -> dict:
    coverage = statistics["chinese_coverage"]
    return {
        language: {
            "entries": statistics["entries"][language],
            "senses": statistics["senses"][language],
            "entries_with_chinese": coverage["entries_with_chinese"][language],
            "entry_percent": coverage["entry_percent"][language],
            "senses_with_chinese": coverage["senses_with_chinese"][language],
            "sense_percent": coverage["sense_percent"][language],
        }
        for language in (*_LANGUAGES, "total")
    }


def _delta(old: dict, new: dict) -> dict:
    output = {}
    for language, values in new.items():
        output[language] = {}
        for key, value in values.items():
            before = old[language][key]
            output[language][key] = {
                "from": before,
                "to": value,
                "absolute": round(value - before, 6),
                "relative_percent": round(100 * (value - before) / before, 4) if before else None,
            }
    return output


def common_word_changes(
    baseline: Path,
    candidate: Path,
    counterfactual: Path | None,
    words: dict[str, list[str]],
) -> dict:
    output = {}
    for language in _LANGUAGES:
        lexicon = list(dict.fromkeys(words.get(language, [])))
        old = word_chinese(baseline, language, lexicon)
        new = word_chinese(candidate, language, lexicon)
        mid = word_chinese(counterfactual, language, lexicon) if counterfactual else None
        lost, gained, kept = [], [], []
        for word in lexicon:
            if old[word] and not new[word]:
                record = {"word": word, "baseline": old[word][:6]}
                if mid is not None:
                    record["cause"] = "importer" if mid[word] else "snapshot"
                    record["counterfactual"] = mid[word][:6]
                lost.append(record)
            elif new[word] and not old[word]:
                gained.append({"word": word, "candidate": new[word][:6]})
            elif new[word]:
                kept.append(word)
        output[language] = {
            "words": len(lexicon),
            "baseline_with_chinese": sum(1 for word in lexicon if old[word]),
            "candidate_with_chinese": sum(1 for word in lexicon if new[word]),
            "counterfactual_with_chinese": (
                sum(1 for word in lexicon if mid[word]) if mid is not None else None
            ),
            "lost": lost,
            "gained": gained,
            "kept": len(kept),
        }
    return output


def raw_alignment_causes(
    sources: dict[str, Path],
    targets: dict[str, set[str]],
    *,
    record_limit: int = 20,
) -> dict:
    """Re-run the strict aligner on raw source records of selected lemmas.

    For every entry-level zh translation the result says what the old importer
    would have done (any-token overlap, stopwords included) and what the strict
    aligner decided, with the hint and the overlapping tokens.
    """
    output: dict[str, dict] = {language: {} for language in _LANGUAGES}
    for language, path in sources.items():
        wanted = {normalize_lexical(word) for word in targets.get(language, set())}
        if not wanted:
            continue
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                # Cheap prefilter before JSON parsing of a ~25 GB stream.
                if f'"lang_code": "{language}"' not in line and f'"lang_code":"{language}"' not in line:
                    continue
                value = json.loads(line)
                lemma = value.get("word")
                if value.get("lang_code") != language or not isinstance(lemma, str):
                    continue
                key = normalize_lexical(lemma.strip())
                if key not in wanted:
                    continue
                raw_senses = value.get("senses") if isinstance(value.get("senses"), list) else []
                gloss_lists = [
                    glosses
                    for sense in raw_senses
                    if isinstance(sense, dict)
                    for glosses in [_strings(sense.get("glosses"), limit=3, max_length=2_000)]
                    if glosses
                ]
                definitions = [glosses[0] for glosses in gloss_lists]
                # Same record construction as the importer (en/de/zh); decisions are
                # per record, so restricting to zh-CN afterwards does not change them.
                records = [record for record in entry_translation_records(value) if record[0] == "zh-CN"]
                if not records:
                    continue
                decisions: list = []
                _aligned_entry_translations(records, definitions, language=language,
                                            lemma=lemma.strip(), decisions=decisions)
                fallback_allowed, mismatch_allowed = single_sense_fallback_allowed(
                    raw_senses, len(definitions), raw_translation_hints(value),
                    language=language,
                    part_of_speech=value.get("pos") if isinstance(value.get("pos"), str) else None,
                )
                repaired_decisions: list = []
                _aligned_entry_translations(
                    records, definitions, language=language, lemma=lemma.strip(),
                    decisions=repaired_decisions,
                    groups=equivalence_groups(gloss_lists),
                    single_sense_fallback=fallback_allowed,
                    single_table=mismatch_allowed,
                )
                repaired_decisions = [
                    ("dungan_filtered", None) if chinese_script_rejection(code, word) else decision
                    for (code, word, _), decision in zip(records, repaired_decisions)
                ]
                ignored = _SENSE_ALIGNMENT_STOPWORDS[language] | _SENSE_ALIGNMENT_GENERIC_TOKENS
                entry = output[language].setdefault(lemma.strip(), {
                    "entries": 0, "senses": 0, "zh_records": 0,
                    "old_importer_accepts": 0, "old_matches_only_stopword_or_generic": 0,
                    "strict_decisions": {}, "repaired_decisions": {}, "examples": [],
                })
                entry["entries"] += 1
                entry["senses"] += len(definitions)
                for (code, word, hint), (decision, winner), (repaired, _) in zip(records, decisions, repaired_decisions):
                    hint_tokens = set(lexical_tokens(hint))
                    old_senses = [
                        index for index, definition in enumerate(definitions)
                        if len(definitions) == 1 or (hint_tokens and hint_tokens & set(lexical_tokens(definition)))
                    ]
                    old_overlap = sorted({
                        token for definition in definitions for token in hint_tokens & set(lexical_tokens(definition))
                    })
                    entry["zh_records"] += 1
                    if old_senses:
                        entry["old_importer_accepts"] += 1
                        if len(definitions) > 1 and old_overlap and not (set(old_overlap) - ignored):
                            entry["old_matches_only_stopword_or_generic"] += 1
                    entry["strict_decisions"][decision] = entry["strict_decisions"].get(decision, 0) + 1
                    entry["repaired_decisions"][repaired] = entry["repaired_decisions"].get(repaired, 0) + 1
                    if len(entry["examples"]) < record_limit:
                        entry["examples"].append({
                            "zh": word,
                            "hint": hint,
                            "old_importer_senses": len(old_senses),
                            "old_overlap_tokens": old_overlap[:8],
                            "strict_decision": decision,
                            "repaired_decision": repaired,
                            "strict_sense": definitions[winner][:80] if winner is not None else None,
                        })
    return output


_LOSS_CATEGORIES = (
    "single_sense_entry",
    "identical_definition_tie",
    "broadcast_to_distinct_senses",
    "single_target_sense_rejected",
)


def _entry_rows(connection: sqlite3.Connection):
    """Yield (entry_id, language, lemma, [(definition, zh_values)]) in id order."""
    current = None
    for entry_id, language, lemma, definition, translations_json in connection.execute(
        "SELECT entries.id, entries.language, entries.lemma, senses.definition, senses.translations_json "
        "FROM entries JOIN senses ON senses.entry_id = entries.id "
        "ORDER BY entries.id, senses.sense_index"
    ):
        if current is None or current[0] != entry_id:
            if current is not None:
                yield current
            current = (int(entry_id), str(language), str(lemma), [])
        current[3].append((definition, json.loads(translations_json).get("zh-CN", [])))
    if current is not None:
        yield current


def importer_effect_breakdown(counterfactual: Path, candidate: Path, *, examples_per_category: int = 15) -> dict:
    """Classify why entries lost all Chinese between old and new importer.

    Both databases must come from the same source snapshot, so entry IDs and
    sense order line up (verified row by row). Categories:

    * ``single_sense_entry`` - old importer attached unscoped/any-hint values to
      the only sense; strict alignment requires a matching hint;
    * ``identical_definition_tie`` - every old sense holding the value has the
      same definition text (Wiktextract subsenses inherit the parent gloss), so
      strict alignment saw a tie;
    * ``broadcast_to_distinct_senses`` - old importer put one value on several
      senses with different definitions;
    * ``single_target_sense_rejected`` - old importer put it on exactly one sense
      (any-token overlap, often a stopword); strict alignment rejected it.
    """
    old_connection = _readonly(counterfactual)
    new_connection = _readonly(candidate)
    try:
        summary = {language: {"entries_with_chinese_old": 0, "entries_with_chinese_new": 0,
                              "entries_lost_all": 0, "entries_gained": 0,
                              "values_old": 0, "values_new": 0,
                              "values_moved_to_fewer_senses": 0,
                              "lost_by_category": {key: 0 for key in _LOSS_CATEGORIES},
                              "examples": {key: [] for key in _LOSS_CATEGORIES}}
                   for language in _LANGUAGES}
        new_rows = _entry_rows(new_connection)
        for old in _entry_rows(old_connection):
            new = next(new_rows, None)
            if new is None or new[:3] != old[:3] or len(new[3]) != len(old[3]):
                raise ValueError("counterfactual and candidate are not from the same snapshot/order")
            entry_id, language, lemma, old_senses = old
            if language not in summary:
                continue
            bucket = summary[language]
            new_senses = new[3]
            old_values = {value for _, values in old_senses for value in values}
            new_values = {value for _, values in new_senses for value in values}
            bucket["values_old"] += sum(len(values) for _, values in old_senses)
            bucket["values_new"] += sum(len(values) for _, values in new_senses)
            for value in old_values & new_values:
                old_count = sum(value in values for _, values in old_senses)
                new_count = sum(value in values for _, values in new_senses)
                if new_count < old_count:
                    bucket["values_moved_to_fewer_senses"] += 1
            if old_values:
                bucket["entries_with_chinese_old"] += 1
            if new_values:
                bucket["entries_with_chinese_new"] += 1
            if new_values and not old_values:
                bucket["entries_gained"] += 1
            if not old_values or new_values:
                continue
            bucket["entries_lost_all"] += 1
            holders = [definition for definition, values in old_senses if values]
            if len(old_senses) == 1:
                category = "single_sense_entry"
            elif len(holders) > 1 and len(set(holders)) == 1:
                category = "identical_definition_tie"
            elif len(set(holders)) > 1:
                category = "broadcast_to_distinct_senses"
            else:
                category = "single_target_sense_rejected"
            bucket["lost_by_category"][category] += 1
            if len(bucket["examples"][category]) < examples_per_category:
                bucket["examples"][category].append({
                    "lemma": lemma,
                    "senses": len(old_senses),
                    "old_values": sorted(old_values)[:6],
                    "old_holder_definitions": [value[:70] for value in dict.fromkeys(holders)][:3],
                })
        if next(new_rows, None) is not None:
            raise ValueError("candidate has more entries than the counterfactual")
        for bucket in summary.values():
            lost = bucket["entries_lost_all"]
            bucket["lost_percent_by_category"] = {
                key: round(100 * count / lost, 2) if lost else 0.0
                for key, count in bucket["lost_by_category"].items()
            }
        return summary
    finally:
        old_connection.close()
        new_connection.close()


RECOVERY_METHODS = ("single_sense_fallback", "equivalent_group")


def recovery_analysis(strict: Path, repaired: Path, *, samples_per_method: int = 40) -> dict:
    """Same-snapshot strict vs repaired: what recovery added, and what it removed.

    Entry IDs and sense order must match (verified row by row). Removed values
    are expected only from the zh-CN script filter; anything else is reported.
    """
    from services.dictionary.recovery import chinese_script_rejection

    strict_connection = _readonly(strict)
    repaired_connection = _readonly(repaired)
    try:
        summary = {language: {"entries_with_chinese_strict": 0, "entries_with_chinese_repaired": 0,
                              "entries_gained": 0, "entries_lost": 0,
                              "values_added": 0, "values_removed_by_script_filter": 0,
                              "values_removed_other": 0, "removed_other_examples": []}
                   for language in _LANGUAGES}
        repaired_rows = _entry_rows(repaired_connection)
        for old in _entry_rows(strict_connection):
            new = next(repaired_rows, None)
            if new is None or new[:3] != old[:3] or len(new[3]) != len(old[3]):
                raise ValueError("strict and repaired databases are not from the same snapshot/order")
            _, language, lemma, old_senses = old
            if language not in summary:
                continue
            bucket = summary[language]
            old_any = any(values for _, values in old_senses)
            new_any = any(values for _, values in new[3])
            bucket["entries_with_chinese_strict"] += old_any
            bucket["entries_with_chinese_repaired"] += new_any
            bucket["entries_gained"] += new_any and not old_any
            bucket["entries_lost"] += old_any and not new_any
            for (_, before), (_, after) in zip(old_senses, new[3]):
                bucket["values_added"] += len([value for value in after if value not in before])
                for value in before:
                    if value in after:
                        continue
                    if chinese_script_rejection("zh-CN", value) is not None:
                        bucket["values_removed_by_script_filter"] += 1
                    else:
                        bucket["values_removed_other"] += 1
                        if len(bucket["removed_other_examples"]) < 20:
                            bucket["removed_other_examples"].append({"lemma": lemma, "value": value})
        if next(repaired_rows, None) is not None:
            raise ValueError("repaired database has more entries than the strict one")

        methods = {}
        for language, method, recovered_from, count in repaired_connection.execute(
            """SELECT entries.language, translation_provenance.method,
                      coalesce(translation_provenance.recovered_from, ''), count(*)
               FROM translation_provenance JOIN entries ON entries.id = translation_provenance.entry_id
               WHERE translation_provenance.language = 'zh-CN'
               GROUP BY 1, 2, 3"""
        ):
            methods.setdefault(str(language), {}).setdefault(method, {})[recovered_from or "-"] = int(count)
        samples = {}
        for method in RECOVERY_METHODS:
            # Deterministic spread across the whole dictionary for manual review.
            rows = repaired_connection.execute(
                """SELECT entries.language, entries.lemma, entries.part_of_speech, senses.definition,
                          translation_provenance.value, translation_provenance.recovered_from,
                          translation_provenance.hint
                   FROM translation_provenance
                   JOIN entries ON entries.id = translation_provenance.entry_id
                   JOIN senses ON senses.entry_id = translation_provenance.entry_id
                              AND senses.sense_index = translation_provenance.sense_index
                   WHERE translation_provenance.method = ? AND translation_provenance.language = 'zh-CN'
                   ORDER BY (translation_provenance.entry_id * 2654435761) % 4294967296
                   LIMIT ?""",
                (method, samples_per_method),
            ).fetchall()
            samples[method] = [
                {"language": row[0], "lemma": row[1], "pos": row[2], "definition": row[3][:120],
                 "zh": row[4], "strict_reason": row[5], "hint": row[6]}
                for row in rows
            ]
        return {"summary": summary, "zh_values_by_method": methods, "review_samples": samples}
    finally:
        strict_connection.close()
        repaired_connection.close()


def telemetry_summary(build_report: dict | None) -> dict | None:
    if not build_report:
        return None
    rows = build_report.get("import_telemetry", {}).get("by_edition_and_target", [])
    return {
        row["edition"]: {"sense_local": row["sense_local"], "entry_level": row["entry_level"]}
        for row in rows
        if row.get("target_language") == "zh-CN"
    }


def compare_releases(
    *,
    baseline: Path,
    candidate: Path,
    data_quality_cases: dict,
    gold: dict,
    counterfactual: Path | None = None,
    common_words: dict[str, list[str]] | None = None,
    build_report: dict | None = None,
    sources: dict[str, Path] | None = None,
    strict: Path | None = None,
) -> dict:
    databases = {"baseline": baseline, "candidate": candidate}
    if counterfactual is not None:
        databases["counterfactual"] = counterfactual
    if strict is not None:
        databases["strict"] = strict
    coverage = {name: _coverage(dictionary_statistics(path)) for name, path in databases.items()}
    decomposition = {"total": _delta(coverage["baseline"], coverage["candidate"])}
    if counterfactual is not None:
        decomposition["snapshot_effect"] = _delta(coverage["baseline"], coverage["counterfactual"])
        decomposition["importer_effect"] = _delta(coverage["counterfactual"], coverage["candidate"])
    data_quality = {name: evaluate_data_quality(path, data_quality_cases) for name, path in databases.items()}
    context = {name: evaluate_gold_cases(path, None, gold) for name, path in databases.items()}
    broadcast = {name: broadcast_profile(path) for name, path in databases.items()}
    words = common_words or gold.get("common_words") or {}
    word_changes = common_word_changes(baseline, candidate, counterfactual, words)
    breakdown = importer_effect_breakdown(counterfactual, candidate) if counterfactual is not None else None
    recovery = recovery_analysis(strict, candidate) if strict is not None else None
    strict_words = None
    if strict is not None:
        strict_words = {
            language: sum(1 for values in word_chinese(strict, language, list(dict.fromkeys(words.get(language, [])))).values() if values)
            for language in _LANGUAGES
        }
    causes = None
    if sources:
        targets = {language: {item["word"] for item in word_changes[language]["lost"]} for language in _LANGUAGES}
        causes = raw_alignment_causes(sources, targets)
    candidate_dq = data_quality["candidate"]
    candidate_context = context["candidate"]
    verdicts = {
        "dictionary_data_quality": {
            "passed": candidate_dq["data_quality_failures"] == 0,
            "candidate_failures": candidate_dq["data_quality_failures"],
            "candidate_incorrect_associations": candidate_dq["incorrect_associations"],
            "baseline_failures": data_quality["baseline"]["data_quality_failures"],
            "baseline_incorrect_associations": data_quality["baseline"]["incorrect_associations"],
        },
        "context_sense_selection": {
            "passed": candidate_context["gold_case_failures"] == 0,
            "candidate_failures": candidate_context["gold_case_failures"],
            "baseline_failures": context["baseline"]["gold_case_failures"],
            "same_status_and_sense_as_baseline": [
                case["id"] for case, base in zip(candidate_context["cases"], context["baseline"]["cases"])
                if (case["status"], case["selection_method"], case["definition"])
                == (base["status"], base["selection_method"], base["definition"])
            ],
        },
    }
    return {
        "comparison_version": 1,
        "verdicts": verdicts,
        "coverage": coverage,
        "coverage_decomposition": decomposition,
        "data_quality": data_quality,
        "context_sense_selection": context,
        "broadcast_profile": broadcast,
        "importer_effect_breakdown": breakdown,
        "recovery_analysis": recovery,
        "strict_common_words_with_chinese": strict_words,
        "common_words": word_changes,
        "strict_alignment_causes_for_lost_words": causes,
        "candidate_zh_telemetry": telemetry_summary(build_report),
    }


def markdown_summary(report: dict) -> str:
    v = report["verdicts"]
    lines = ["# DQ-02 dictionary comparison", ""]
    dq, cs = v["dictionary_data_quality"], v["context_sense_selection"]
    lines += [
        f"- **Dictionary Data Quality:** {'PASS' if dq['passed'] else 'FAIL'} "
        f"(candidate failures {dq['candidate_failures']}, incorrect associations "
        f"{dq['candidate_incorrect_associations']}; baseline failures {dq['baseline_failures']}, "
        f"incorrect {dq['baseline_incorrect_associations']})",
        f"- **Context Sense Selection:** {'PASS' if cs['passed'] else 'FAIL'} "
        f"(candidate failures {cs['candidate_failures']}, baseline failures {cs['baseline_failures']}; "
        f"identical status/sense to baseline: {len(cs['same_status_and_sense_as_baseline'])} case(s))",
        "",
        "## Chinese coverage (entries with at least one zh-CN value)",
        "",
        "| step | language | from | to | abs Δ (pp) | rel Δ % |",
        "|---|---|---|---|---|---|",
    ]
    for step, values in report["coverage_decomposition"].items():
        for language in ("en", "de"):
            item = values[language]["entry_percent"]
            lines.append(f"| {step} | {language} | {item['from']}% | {item['to']}% | {item['absolute']} | {item['relative_percent']} |")
    breakdown = report.get("importer_effect_breakdown")
    if breakdown:
        lines += ["", "## Entries that lost all Chinese (same snapshot, old vs new importer)", "",
                  "| language | lost | single-sense | identical-definition tie | broadcast | single-target rejected |",
                  "|---|---|---|---|---|---|"]
        for language in ("en", "de"):
            item = breakdown[language]
            counts = item["lost_by_category"]
            lines.append(f"| {language} | {item['entries_lost_all']} | {counts['single_sense_entry']} | "
                         f"{counts['identical_definition_tie']} | {counts['broadcast_to_distinct_senses']} | "
                         f"{counts['single_target_sense_rejected']} |")
    if "strict" in report["coverage"]:
        lines += ["", "## Same-snapshot three-way coverage (entries with zh-CN)", "",
                  "| language | old importer | strict (7A) | repaired (7B) |", "|---|---|---|---|"]
        for language in ("en", "de"):
            row = [report["coverage"][name][language] for name in ("counterfactual", "strict", "candidate")
                   if name in report["coverage"]]
            lines.append(f"| {language} | " + " | ".join(f"{r['entries_with_chinese']} ({r['entry_percent']}%)" for r in row) + " |")
        recovery = report.get("recovery_analysis") or {}
        for language in ("en", "de"):
            item = (recovery.get("summary") or {}).get(language)
            if item:
                lines.append(f"- {language}: +{item['entries_gained']} entries / +{item['values_added']} values recovered; "
                             f"removed by script filter {item['values_removed_by_script_filter']}, other removals {item['values_removed_other']}")
    lines += ["", "## Data quality cases (candidate)", ""]
    for case in report["data_quality"]["candidate"]["cases"]:
        lines.append(f"- {case['id']}: {'PASS' if case['passed'] else 'FAIL'} {case['classification']} {case['chinese_values'][:6]}")
    lines += ["", "## Common words that lost Chinese", ""]
    for language in ("en", "de"):
        item = report["common_words"][language]
        lines.append(f"- {language}: {item['baseline_with_chinese']}/{item['words']} → {item['candidate_with_chinese']}/{item['words']}; "
                     f"lost {len(item['lost'])}: " + ", ".join(f"{x['word']}({x.get('cause', '?')})" for x in item["lost"][:40]))
    return "\n".join(lines) + "\n"
