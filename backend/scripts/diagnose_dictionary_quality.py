#!/usr/bin/env python3
"""Read-only candidate and query-plan diagnostics for an installed dictionary."""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.dictionary.provider import (  # noqa: E402
    SQLiteDictionaryProvider,
    normalize_lexical,
)
from services.dictionary.service import (  # noqa: E402
    DictionaryService,
    default_dictionary_path,
)


CASES = (
    ("en", "go", "We go to work by train."),
    ("de", "ging", "Er ging nach Hause."),
    ("de", "Hallo", "Hallo, wie geht es dir?"),
)


def _source_entry_evidence(
    connection: sqlite3.Connection,
    language: str,
    surface: str,
) -> list[dict[str, object]]:
    entries = connection.execute(
        """SELECT id, lemma, part_of_speech, definition_language
           FROM entries
           WHERE language = ? AND normalized_lemma = ?
           ORDER BY id LIMIT 40""",
        (language, normalize_lexical(surface)),
    ).fetchall()
    entry_ids = [int(entry["id"]) for entry in entries]
    form_tags: dict[int, list[dict[str, object]]] = {
        entry_id: [] for entry_id in entry_ids
    }
    if entry_ids:
        placeholders = ", ".join("?" for _ in entry_ids)
        for row in connection.execute(
            f"""SELECT entry_id, form, tags_json FROM forms
                  WHERE entry_id IN ({placeholders})
                  ORDER BY id LIMIT 100""",
            entry_ids,
        ):
            form_tags[int(row["entry_id"])].append({
                "form": row["form"],
                "tags": json.loads(row["tags_json"]),
            })
    output = []
    for entry in entries:
        entry_id = int(entry["id"])
        glosses = [
            row["definition"] for row in connection.execute(
                """SELECT definition FROM senses
                   WHERE entry_id = ? ORDER BY sense_index LIMIT 100""",
                (entry_id,),
            )
        ]
        output.append({
            "entry_id": entry_id,
            "lemma": entry["lemma"],
            "part_of_speech": entry["part_of_speech"],
            "definition_language": entry["definition_language"],
            "glosses": glosses,
            "entry_tags": "not retained by dictionary schema v1",
            "sense_tags": "not retained by dictionary schema v1",
            "stored_form_tags": form_tags[entry_id],
        })
    return output


def _query_diagnostics(
    connection: sqlite3.Connection,
    language: str,
    surface: str,
    sentence: str,
) -> dict[str, object]:
    normalized = normalize_lexical(surface)
    counts = {
        "exact": (
            "SELECT id FROM entries WHERE language = ? AND normalized_lemma = ?",
            (language, normalized),
        ),
        "inflection": (
            """SELECT entries.id FROM forms
               JOIN entries ON entries.id = forms.entry_id
               WHERE entries.language = ? AND forms.normalized_form = ?""",
            (language, normalized),
        ),
        "separated_component": (
            """SELECT forms.id FROM form_components
               JOIN forms ON forms.id = form_components.form_id
               JOIN entries ON entries.id = forms.entry_id
               WHERE entries.language = ? AND form_components.component = ?""",
            (language, normalized),
        ),
    }
    greeting_context = SQLiteDictionaryProvider._greeting_context(surface, sentence)
    plans = {
        "exact": (
            """SELECT id FROM entries
               WHERE language = ? AND normalized_lemma = ?
               ORDER BY
                   CASE WHEN ? AND lower(coalesce(part_of_speech, '')) IN ('intj', 'interj', 'interjection', 'phrase')
                        THEN 0 ELSE 1 END,
                   id
               LIMIT ?""",
            (language, normalized, greeting_context, 20),
        ),
        "inflection": (
            """SELECT DISTINCT entries.id FROM forms
               JOIN entries ON entries.id = forms.entry_id
               WHERE entries.language = ? AND forms.normalized_form = ?
               ORDER BY entries.id LIMIT ?""",
            (language, normalized, 20),
        ),
        "separated_component": (
            """SELECT forms.id, forms.entry_id, forms.normalized_form,
                      entries.normalized_lemma
               FROM form_components JOIN forms ON forms.id = form_components.form_id
               JOIN entries ON entries.id = forms.entry_id
               WHERE entries.language = ? AND form_components.component = ?
               ORDER BY forms.entry_id, forms.id LIMIT ?""",
            (language, normalized, 100),
        ),
    }
    output: dict[str, object] = {}
    for name, (count_query, count_parameters) in counts.items():
        count_sql = f"SELECT count(*) FROM ({count_query})"
        count = int(connection.execute(count_sql, count_parameters).fetchone()[0])
        plan_query, plan_parameters = plans[name]
        plan = [
            str(row[3])
            for row in connection.execute(
                f"EXPLAIN QUERY PLAN {plan_query}", plan_parameters
            )
        ]
        output[name] = {"count": count, "plan": plan}
    output["hydration"] = {
        "senses": [
            str(row[3]) for row in connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT * FROM senses
                   WHERE entry_id = ? ORDER BY sense_index LIMIT ?""",
                (0, 100),
            )
        ],
        "pronunciations": [
            str(row[3]) for row in connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT * FROM pronunciations
                   WHERE entry_id = ? ORDER BY pronunciation_index LIMIT ?""",
                (0, 20),
            )
        ],
        "selected_entry_forms": [
            str(row[3]) for row in connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT form FROM forms
                   WHERE entry_id = ? ORDER BY id LIMIT ?""",
                (0, 30),
            )
        ],
    }
    return output


def diagnose(database: Path) -> list[dict[str, object]]:
    provider = SQLiteDictionaryProvider(database)
    service = DictionaryService(provider)
    output: list[dict[str, object]] = []
    connection = provider._connect()
    try:
        connection.execute("PRAGMA query_only = ON")
        for language, surface, sentence in CASES:
            started = time.perf_counter()
            entries = provider.lookup(language, surface, sentence)
            provider_elapsed_ms = round((time.perf_counter() - started) * 1_000, 2)
            applicable = {
                entry.entry_id
                for entry in service._candidate_entries(entries, sentence, surface)
            }
            ranked = service._ranked_senses(entries, sentence, surface)
            top_ranked = [
                candidate for candidate in ranked
                if ranked and candidate.rank == ranked[0].rank
            ]
            top_definitions = {
                service._sense_identity(candidate) for candidate in top_ranked
            }
            best_by_entry = {}
            for candidate in ranked:
                best_by_entry.setdefault(candidate.entry.entry_id, candidate)
            candidates = []
            for entry in entries:
                candidate = best_by_entry.get(entry.entry_id)
                candidates.append({
                    "entry_id": entry.entry_id,
                    "lemma": entry.lemma,
                    "part_of_speech": entry.part_of_speech,
                    "match_kind": entry.match_kind,
                    "applicable": entry.entry_id in applicable,
                    "lexical_score": candidate.lexical_score if candidate else None,
                    "morphology_score": candidate.morphology_score if candidate else None,
                    "greeting_score": candidate.greeting_score if candidate else None,
                    "noun_context_score": (
                        candidate.noun_context_score if candidate else None
                    ),
                    "selected_definition": candidate.sense.definition if candidate else None,
                })
            started = time.perf_counter()
            selected = service.lookup(language, surface, sentence)
            lookup_elapsed_ms = round((time.perf_counter() - started) * 1_000, 2)
            output.append({
                "language": language,
                "surface": surface,
                "sentence": sentence,
                "provider_elapsed_ms": provider_elapsed_ms,
                "lookup_elapsed_ms": lookup_elapsed_ms,
                "provider_candidate_count": len(entries),
                "top_rank_tie_count": len(top_ranked),
                "top_rank_distinct_definition_count": len(top_definitions),
                "top_rank_duplicate_definition_count": (
                    len(top_ranked) - len(top_definitions)
                ),
                "candidates": candidates,
                "source_entries": _source_entry_evidence(
                    connection, language, surface
                ),
                "selected": {
                    "lemma": selected.lemma,
                    "part_of_speech": selected.part_of_speech,
                    "status": selected.status,
                    "selection_method": selected.context.selection_method,
                    "definition": selected.explanations.monolingual.value,
                    "alternate_sense_count": len(selected.alternate_senses),
                },
                "queries": _query_diagnostics(
                    connection, language, surface, sentence
                ),
            })
    finally:
        connection.close()
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=default_dictionary_path(),
        help="installed dictionary SQLite path (opened read-only)",
    )
    arguments = parser.parse_args()
    if not arguments.database.is_file():
        parser.error(f"dictionary is not installed at {arguments.database}")
    print(json.dumps(diagnose(arguments.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
