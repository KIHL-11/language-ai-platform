import gzip
import json
import os
import re
import sqlite3
import unicodedata
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Protocol
from urllib.parse import urlparse

from services.dictionary.forms_sidecar import (
    FormsLookupResult,
    default_forms_index_path,
    lookup_forms,
)
from services.dictionary.import_telemetry import ImportTelemetry
from services.dictionary.recovery import SINGLE_SENSE_RECOVERABLE, SINGLE_TABLE_RECOVERABLE
from services.dictionary.recovery import (
    chinese_script_rejection,
    equivalence_groups,
    raw_translation_hints,
    single_sense_fallback_allowed,
)


SCHEMA_VERSION = 1
SOURCE_NAME = "Wiktionary data extracted by Wiktextract/Kaikki"
_LEXICAL = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
_ENTRY_MATCH_LIMIT = 20
_SEPARATED_FORM_SCAN_LIMIT = 100
_SENSE_LIMIT = 100
_PRONUNCIATION_LIMIT = 20
_SENSE_ALIGNMENT_STOPWORDS = {
    "en": frozenset({
        "a", "an", "and", "are", "as", "at", "be", "being", "by", "for",
        "from", "in", "is", "it", "of", "on", "or", "the", "to", "was",
        "were", "with",
    }),
    "de": frozenset({
        "das", "der", "die", "ein", "eine", "einem", "einen", "einer",
        "eines", "er", "es", "für", "im", "in", "ist", "mit", "oder",
        "und", "vom", "von", "was", "zu", "zum", "zur",
    }),
}
_SENSE_ALIGNMENT_GENERIC_TOKENS = frozenset({
    # Source labels and generic placeholders describe usage, not lexical meaning.
    "action", "act", "archaic", "colloquial", "condition", "entity", "etwas",
    "event", "figurative", "form", "general", "idiomatic", "intransitive",
    "jemand", "kind", "literal", "manner", "meaning", "object", "obsolete",
    "one", "occurrence", "person", "process", "quality", "sense", "slang",
    "something", "state", "term", "thing", "transitive", "type", "usage", "way",
    "word",
})


def normalize_lexical(value: str) -> str:
    return " ".join(
        token.casefold().replace("’", "'")
        for token in _LEXICAL.findall(unicodedata.normalize("NFKC", value))
    )


def lexical_tokens(value: str) -> list[str]:
    normalized = normalize_lexical(value)
    return normalized.split() if normalized else []


def safe_wikimedia_audio_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {
        "upload.wikimedia.org", "commons.wikimedia.org"
    }:
        return None
    return value


def safe_dictionary_source_url(value: object, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (
        host == "kaikki.org" or host.endswith(".wiktionary.org")
    ):
        return fallback
    return value


@dataclass(frozen=True, slots=True)
class ProviderSense:
    definition: str
    examples: tuple[str, ...]
    translations: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class ProviderPronunciation:
    ipa: str | None
    accent: str
    audio_url: str | None


@dataclass(frozen=True, slots=True)
class ProviderEntry:
    entry_id: int
    language: str
    lemma: str
    part_of_speech: str | None
    definition_language: str
    source_url: str
    source_name: str
    license: str
    senses: tuple[ProviderSense, ...]
    forms: tuple[str, ...]
    pronunciations: tuple[ProviderPronunciation, ...]
    match_kind: str


class DictionaryProvider(Protocol):
    @property
    def configured(self) -> bool: ...

    def lookup(self, language: str, surface: str, sentence: str) -> list[ProviderEntry]: ...


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY,
            language TEXT NOT NULL,
            lemma TEXT NOT NULL,
            normalized_lemma TEXT NOT NULL,
            part_of_speech TEXT,
            definition_language TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_name TEXT NOT NULL,
            license TEXT NOT NULL
        );
        CREATE INDEX entries_lookup ON entries(language, normalized_lemma);
        CREATE TABLE senses (
            entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            sense_index INTEGER NOT NULL,
            definition TEXT NOT NULL,
            examples_json TEXT NOT NULL,
            translations_json TEXT NOT NULL,
            PRIMARY KEY(entry_id, sense_index)
        );
        CREATE TABLE forms (
            id INTEGER PRIMARY KEY,
            entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            form TEXT NOT NULL,
            normalized_form TEXT NOT NULL,
            tags_json TEXT NOT NULL
        );
        CREATE INDEX forms_lookup ON forms(normalized_form);
        CREATE TABLE form_components (
            form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
            component TEXT NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY(form_id, position)
        );
        CREATE INDEX form_components_lookup ON form_components(component);
        CREATE TABLE translation_provenance (
            entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            sense_index INTEGER NOT NULL,
            language TEXT NOT NULL,
            value TEXT NOT NULL,
            method TEXT NOT NULL,
            recovered_from TEXT,
            hint TEXT
        );
        CREATE TABLE pronunciations (
            entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            pronunciation_index INTEGER NOT NULL,
            ipa TEXT,
            accent TEXT NOT NULL,
            audio_url TEXT,
            PRIMARY KEY(entry_id, pronunciation_index)
        );
        """
    )
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def _translation_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.casefold()
    if code in {"en", "english"}:
        return "en"
    if code in {"de", "german", "deutsch"}:
        return "de"
    if code in {"zh", "zh-cn", "zh-hans", "cmn", "chinese", "mandarin"}:
        return "zh-CN"
    return None


_TRANSLATION_LIMIT_PER_LANGUAGE = 10


def _translation_values(
    aligned_entry_records: list[tuple[str, str]],
    sense_records: list[tuple[str, str]],
    *,
    outcomes: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Merge direct and aligned translations for one sense.

    When ``outcomes`` is supplied it receives one status per input record, in
    input order, under ``"sense"`` and ``"entry"``. It never changes the output.
    """
    output: dict[str, list[str]] = {}

    def append(code: str, word: str) -> str:
        values = output.setdefault(code, [])
        if word in values:
            return "duplicate"
        if len(values) >= _TRANSLATION_LIMIT_PER_LANGUAGE:
            return "over_limit"
        values.append(word)
        return "stored"

    # A translation nested under a source sense is direct evidence and takes priority.
    sense_statuses = [append(code, word) for code, word in sense_records]
    entry_statuses = [append(code, word) for code, word in aligned_entry_records]
    if outcomes is not None:
        outcomes["sense"] = sense_statuses
        outcomes["entry"] = entry_statuses
    return output


def _morphology_only_similarity(hint_tokens: set[str], definition_tokens: list[set[str]]) -> bool:
    """Telemetry-only: hint and definition share a stem/compound but no exact token."""
    for hint in hint_tokens:
        if len(hint) < 4:
            continue
        for tokens in definition_tokens:
            for token in tokens:
                if len(token) >= 4 and token != hint and (hint in token or token in hint):
                    return True
    return False


def _aligned_entry_translations(
    records: list[tuple[str, str, str]],
    definitions: list[str],
    *,
    language: str,
    lemma: str,
    decisions: list[tuple[str, int | None]] | None = None,
    groups: list[int] | None = None,
    single_sense_fallback: bool = False,
    single_table: bool = False,
    recovered_from: list[str | None] | None = None,
) -> list[list[tuple[str, str]]]:
    """Assign each entry translation to at most one source sense.

    Unscoped translations remain usable only when there is exactly one valid
    sense. Scoped translations require a unique best lexical match across the
    complete entry. Ties and unsupported morphological guesses are omitted.

    Phase 7B recovery (all off unless requested):

    * ``groups`` maps each sense to its equivalence-group representative
      (``recovery.equivalence_groups``). A tie whose winners all share one
      group, or an unscoped translation on an entry that is one group, is
      assigned to the representative with outcome ``equivalent_group``.
    * ``single_sense_fallback`` attaches a recoverable rejection on an eligible
      single-sense entry (``recovery.single_sense_fallback_allowed``) to that
      sense with outcome ``single_sense_fallback``; ``single_table`` (the
      helper's ``mismatch_allowed``) additionally allows ``single_sense_mismatch``.

    When ``decisions`` is supplied it receives one ``(outcome, winner)`` pair per
    record; ``recovered_from`` receives the strict rejection reason that a
    recovery overrode (else ``None``). Recording never changes alignment.
    """
    recoverable = SINGLE_SENSE_RECOVERABLE | (SINGLE_TABLE_RECOVERABLE if single_table else frozenset())

    aligned: list[list[tuple[str, str]]] = [[] for _ in definitions]

    def decide(outcome: str, winner: int | None = None, recovered: str | None = None) -> None:
        if decisions is not None:
            decisions.append((outcome, winner))
        if recovered_from is not None:
            recovered_from.append(recovered)

    def reject(code: str, word: str, outcome: str) -> None:
        if single_sense_fallback and len(definitions) == 1 and outcome in recoverable:
            aligned[0].append((code, word))
            decide("single_sense_fallback", 0, outcome)
            return
        decide(outcome)

    if not definitions:
        for _ in records:
            decide("zero_evidence")
        return aligned

    single_group = groups is not None and len(set(groups)) == 1
    ignored = (
        _SENSE_ALIGNMENT_STOPWORDS[language]
        | _SENSE_ALIGNMENT_GENERIC_TOKENS
        | set(lexical_tokens(lemma))
    )
    definition_token_lists = [
        [token for token in lexical_tokens(value) if token not in ignored]
        for value in definitions
    ]
    definition_tokens = [set(tokens) for tokens in definition_token_lists]

    for code, word, hint in records:
        raw_hint_tokens = set(lexical_tokens(hint))
        if not raw_hint_tokens:
            if len(definitions) == 1:
                aligned[0].append((code, word))
                decide("unscoped_single_sense", 0)
            elif single_group:
                aligned[groups[0]].append((code, word))
                decide("equivalent_group", groups[0])
            else:
                decide("ambiguous_or_tied")
            continue

        hint_tokens = raw_hint_tokens - ignored
        if not hint_tokens:
            reject(code, word, "generic_stopword_or_lemma_only")
            continue
        scores = [len(hint_tokens & tokens) for tokens in definition_tokens]
        best_score = max(scores)
        if best_score == 0:
            if _morphology_only_similarity(hint_tokens, definition_tokens):
                reject(code, word, "unsupported_morphology")
            elif len(definitions) == 1:
                reject(code, word, "single_sense_mismatch")
            else:
                reject(code, word, "zero_evidence")
            continue
        winners = [index for index, score in enumerate(scores) if score == best_score]
        grouped = False
        if len(winners) != 1:
            if groups is None or len({groups[index] for index in winners}) != 1:
                decide("ambiguous_or_tied")
                continue
            winner = groups[winners[0]]
            grouped = True
        else:
            winner = winners[0]
        if best_score == 1:
            # A lone token is reliable only when the source hint itself has one
            # content token and that token is the definition's lexical head.
            # This retains labels such as "understand" -> "To understand..."
            # without accepting an incidental word later in a long definition.
            if len(hint_tokens) != 1 or not definition_token_lists[winner]:
                reject(code, word, "weak_single_token_evidence")
                continue
            if next(iter(hint_tokens)) != definition_token_lists[winner][0]:
                reject(code, word, "weak_single_token_evidence")
                continue
            aligned[winner].append((code, word))
            decide("equivalent_group" if grouped else "reliable_single_token_head", winner)
            continue
        aligned[winner].append((code, word))
        decide("equivalent_group" if grouped else "unique_multi_token", winner)

    return aligned


def _strings(values: object, *, limit: int, max_length: int) -> list[str]:
    if not isinstance(values, list):
        return []
    output: list[str] = []
    for value in values:
        if isinstance(value, str):
            candidate = value.strip()
            if candidate and len(candidate) <= max_length and candidate not in output:
                output.append(candidate)
        if len(output) >= limit:
            break
    return output


def _open_dictionary_source(input_path: Path):
    if input_path.suffix.casefold() == ".gz":
        return gzip.open(input_path, mode="rt", encoding="utf-8")
    return input_path.open(encoding="utf-8")


def import_kaikki_sources(
    sources: Iterable[tuple[Path, str, str]],
    database_path: Path,
    *,
    content_license: str,
    telemetry: ImportTelemetry | None = None,
    recovery: bool = True,
) -> int:
    """Atomically stream edition-tagged JSONL or JSONL gzip files into SQLite.

    The finished database is fsynced before it replaces ``database_path`` and the
    containing directory is fsynced afterward. ``telemetry`` only observes.

    ``recovery`` enables the Phase 7B repairs (``services/dictionary/recovery``):
    the zh-CN script filter, equivalent parent/child groups for ties, and the
    single-sense fallback. ``recovery=False`` reproduces Phase 7A strict output
    (plus the provenance table) for same-snapshot comparisons.
    Every stored translation gets one ``translation_provenance`` row.
    """
    source_values = list(sources)
    if not source_values:
        raise ValueError("At least one dictionary source is required")
    if any(language not in {"en", "de"} for _, language, _ in source_values):
        raise ValueError("edition_language must be en or de")
    if not content_license.strip() or len(content_license) > 200:
        raise ValueError("A bounded content license label is required")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = database_path.with_suffix(database_path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    count = 0
    connection = sqlite3.connect(temporary)
    try:
        initialize_database(connection)
        connection.execute(
            "INSERT INTO metadata VALUES ('source_url', ?)",
            (";".join(value[2] for value in source_values),),
        )
        connection.execute("INSERT INTO metadata VALUES ('content_license', ?)", (content_license,))
        for input_path, edition_language, source_url in source_values:
            try:
                with _open_dictionary_source(input_path) as source:
                    for line_number, line in enumerate(source, start=1):
                        if telemetry is not None:
                            telemetry.record(edition_language, "lines_read")
                        if not line.strip():
                            if telemetry is not None:
                                telemetry.record(edition_language, "blank_lines")
                            continue
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ValueError(
                                f"Invalid JSON in {input_path.name} on line {line_number}"
                            ) from exc
                        language = value.get("lang_code")
                        lemma = value.get("word")
                        if language != edition_language:
                            if telemetry is not None:
                                telemetry.record(edition_language, "skipped_language")
                            continue
                        if not isinstance(lemma, str):
                            if telemetry is not None:
                                telemetry.record(edition_language, "skipped_lemma")
                            continue
                        lemma = lemma.strip()
                        normalized = normalize_lexical(lemma)
                        if not normalized or len(lemma) > 200:
                            if telemetry is not None:
                                telemetry.record(edition_language, "skipped_lemma")
                            continue
                        pos = value.get("pos") if isinstance(value.get("pos"), str) else None
                        fallback_source = f"https://{edition_language}.wiktionary.org/wiki/{lemma.replace(' ', '_')}"
                        entry_source = safe_dictionary_source_url(
                            value.get("source_url"), fallback=fallback_source
                        )
                        cursor = connection.execute(
                            """INSERT INTO entries(
                                language, lemma, normalized_lemma, part_of_speech,
                                definition_language, source_url, source_name, license
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (language, lemma, normalized, pos, edition_language, entry_source, SOURCE_NAME, content_license),
                        )
                        entry_id = int(cursor.lastrowid)

                        translation_records = entry_translation_records(
                            value,
                            script_filter=recovery,
                            on_reject=(
                                (lambda reason: telemetry.script_rejected(edition_language, "entry_level", reason))
                                if telemetry is not None else None
                            ),
                        )

                        senses = value.get("senses") if isinstance(value.get("senses"), list) else []
                        valid_senses: list[tuple[dict, list[str]]] = []
                        for sense in senses:
                            if isinstance(sense, dict):
                                definitions = _strings(
                                    sense.get("glosses"), limit=3, max_length=2_000
                                )
                                if definitions:
                                    valid_senses.append((sense, definitions))
                                elif telemetry is not None:
                                    telemetry.record(edition_language, "senses_without_definition")
                                    for code, _ in _sense_translation_records(
                                        sense,
                                        script_filter=recovery,
                                        on_reject=lambda reason: telemetry.script_rejected(
                                            edition_language, "sense_local", reason
                                        ),
                                    ):
                                        telemetry.sense_local_discarded_with_invalid_sense(
                                            edition_language, code
                                        )
                            elif telemetry is not None:
                                telemetry.record(edition_language, "senses_without_definition")
                        decisions: list[tuple[str, int | None]] = []
                        recovered_from: list[str | None] = []
                        fallback_allowed, mismatch_allowed = single_sense_fallback_allowed(
                            senses,
                            len(valid_senses),
                            raw_translation_hints(value),
                            language=language,
                            part_of_speech=pos,
                        )
                        aligned_entry_translations = _aligned_entry_translations(
                            translation_records,
                            [definitions[0] for _, definitions in valid_senses],
                            language=language,
                            lemma=lemma,
                            decisions=decisions,
                            groups=(
                                equivalence_groups([definitions for _, definitions in valid_senses])
                                if recovery else None
                            ),
                            single_sense_fallback=recovery and fallback_allowed,
                            single_table=recovery and mismatch_allowed,
                            recovered_from=recovered_from,
                        )
                        # Per sense, the provenance of each aligned record, in order.
                        aligned_methods: list[list[tuple[str, str | None, str]]] = [[] for _ in valid_senses]
                        for (_, _, hint), (outcome, winner), recovered in zip(
                            translation_records, decisions, recovered_from
                        ):
                            if winner is not None:
                                aligned_methods[winner].append((outcome, recovered, hint))
                        entry_statuses_by_sense: list[list[str]] = []
                        for sense_index, (sense, definitions) in enumerate(valid_senses):
                            sense_translation_records = _sense_translation_records(
                                sense,
                                script_filter=recovery,
                                on_reject=(
                                    (lambda reason: telemetry.script_rejected(edition_language, "sense_local", reason))
                                    if telemetry is not None else None
                                ),
                            )
                            outcomes: dict[str, list[str]] = {}
                            translations = _translation_values(
                                aligned_entry_translations[sense_index],
                                sense_translation_records,
                                outcomes=outcomes,
                            )
                            provenance_rows = [
                                (entry_id, sense_index, code, word, "sense_local", None, None)
                                for (code, word), status in zip(sense_translation_records, outcomes["sense"])
                                if status == "stored"
                            ] + [
                                (entry_id, sense_index, code, word, method, recovered, hint[:200] or None)
                                for (code, word), status, (method, recovered, hint) in zip(
                                    aligned_entry_translations[sense_index],
                                    outcomes["entry"],
                                    aligned_methods[sense_index],
                                )
                                if status == "stored"
                            ]
                            connection.executemany(
                                "INSERT INTO translation_provenance VALUES (?, ?, ?, ?, ?, ?, ?)",
                                provenance_rows,
                            )
                            if telemetry is not None and outcomes is not None:
                                for (code, _), status in zip(
                                    sense_translation_records, outcomes["sense"]
                                ):
                                    telemetry.sense_local(edition_language, code, {
                                        "stored": "retained",
                                        "duplicate": "deduplicated",
                                        "over_limit": "truncated",
                                    }[status])
                                entry_statuses_by_sense.append(list(outcomes["entry"]))
                            examples: list[str] = []
                            for example in sense.get("examples", []):
                                if isinstance(example, dict):
                                    text = example.get("text")
                                    if isinstance(text, str) and 0 < len(text.strip()) <= 2_000:
                                        examples.append(text.strip())
                                if len(examples) >= 5:
                                    break
                            if telemetry is not None:
                                telemetry.record(edition_language, "senses_imported")
                            connection.execute(
                                "INSERT INTO senses VALUES (?, ?, ?, ?, ?)",
                                (
                                    entry_id,
                                    sense_index,
                                    definitions[0],
                                    json.dumps(examples, ensure_ascii=False),
                                    json.dumps(translations, ensure_ascii=False),
                                ),
                            )

                        forms_seen: set[str] = set()
                        for form in value.get("forms", []):
                            if not isinstance(form, dict) or not isinstance(form.get("form"), str):
                                continue
                            text = form["form"].strip()
                            normalized_form = normalize_lexical(text)
                            if not normalized_form or normalized_form == normalized or normalized_form in forms_seen:
                                continue
                            if len(text) > 300:
                                continue
                            forms_seen.add(normalized_form)
                            form_cursor = connection.execute(
                                "INSERT INTO forms(entry_id, form, normalized_form, tags_json) VALUES (?, ?, ?, ?)",
                                (entry_id, text, normalized_form, json.dumps(form.get("tags", []), ensure_ascii=False)),
                            )
                            for position, component in enumerate(normalized_form.split()):
                                connection.execute(
                                    "INSERT INTO form_components VALUES (?, ?, ?)",
                                    (int(form_cursor.lastrowid), component, position),
                                )

                        pronunciation_index = 0
                        for sound in value.get("sounds", []):
                            if not isinstance(sound, dict):
                                continue
                            ipa = sound.get("ipa") if isinstance(sound.get("ipa"), str) else None
                            audio_url = safe_wikimedia_audio_url(sound.get("mp3_url") or sound.get("ogg_url"))
                            if not ipa and not audio_url:
                                continue
                            tags = _strings(sound.get("tags"), limit=4, max_length=50)
                            accent = ", ".join(tags) if tags else ("Standard German" if language == "de" else "Unspecified")
                            connection.execute(
                                "INSERT INTO pronunciations VALUES (?, ?, ?, ?, ?)",
                                (entry_id, pronunciation_index, ipa, accent, audio_url),
                            )
                            pronunciation_index += 1
                        if telemetry is not None:
                            _record_entry_decisions(
                                telemetry,
                                edition_language,
                                translation_records,
                                decisions,
                                entry_statuses_by_sense,
                                recovered_from,
                            )
                            telemetry.record(edition_language, "entries_imported")
                        count += 1
            except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
                raise ValueError(
                    f"Corrupt or incomplete gzip dictionary source: {input_path.name}"
                ) from exc
        connection.commit()
    except Exception:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise
    else:
        connection.close()
        _fsync_file(temporary)
        temporary.replace(database_path)
        _fsync_directory(database_path.parent)
    return count


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def entry_translation_records(
    value: dict,
    *,
    script_filter: bool = False,
    on_reject=None,
) -> list[tuple[str, str, str]]:
    """Entry-level (code, word, hint) records exactly as the importer aligns them."""
    records: list[tuple[str, str, str]] = []
    for translation in value.get("translations", []):
        if not isinstance(translation, dict):
            continue
        code = _translation_code(
            translation.get("code")
            or translation.get("lang_code")
            or translation.get("lang")
        )
        word = translation.get("word")
        if code and isinstance(word, str) and 0 < len(word.strip()) <= 500:
            if script_filter:
                reason = chinese_script_rejection(code, word.strip(), translation.get("tags"))
                if reason is not None:
                    if on_reject is not None:
                        on_reject(reason)
                    continue
            hint = translation.get("sense")
            records.append((code, word.strip(), hint.strip() if isinstance(hint, str) else ""))
    return records


def _sense_translation_records(
    sense: dict,
    *,
    script_filter: bool = False,
    on_reject=None,
) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for translation in sense.get("translations", []):
        if not isinstance(translation, dict):
            continue
        code = _translation_code(
            translation.get("code")
            or translation.get("lang_code")
            or translation.get("lang")
        )
        word = translation.get("word")
        if code and isinstance(word, str) and word.strip():
            if script_filter:
                reason = chinese_script_rejection(code, word.strip(), translation.get("tags"))
                if reason is not None:
                    if on_reject is not None:
                        on_reject(reason)
                    continue
            records.append((code, word.strip()))
    return records


def _record_entry_decisions(
    telemetry: ImportTelemetry,
    edition: str,
    records: list[tuple[str, str, str]],
    decisions: list[tuple[str, int | None]],
    entry_statuses_by_sense: list[list[str]],
    recovered_from: list[str | None] | None = None,
) -> None:
    """Reconcile alignment decisions with what each sense actually stored."""
    if len(decisions) != len(records):
        raise RuntimeError("alignment telemetry lost a translation record")
    recovered_from = recovered_from or [None] * len(decisions)
    positions = [0 for _ in entry_statuses_by_sense]
    for (code, _, _), (outcome, winner), recovered in zip(records, decisions, recovered_from):
        if winner is None:
            telemetry.entry_level(edition, code, outcome)
            continue
        status = entry_statuses_by_sense[winner][positions[winner]]
        positions[winner] += 1
        if status == "over_limit":
            telemetry.entry_level(edition, code, "over_limit")
            continue
        telemetry.entry_level(edition, code, outcome)
        if recovered is not None:
            # Count only recoveries that reached storage (or were duplicates).
            telemetry.recovered(edition, outcome, recovered)
        if status == "duplicate":
            telemetry.entry_level_duplicate(edition, code)
    if any(position != len(statuses) for position, statuses in zip(positions, entry_statuses_by_sense)):
        raise RuntimeError("alignment telemetry does not reconcile with stored senses")


def import_kaikki_jsonl(
    input_path: Path,
    database_path: Path,
    *,
    edition_language: str,
    source_url: str,
    content_license: str,
    recovery: bool = True,
) -> int:
    """Backward-compatible one-source import helper used by focused tests."""
    return import_kaikki_sources(
        [(input_path, edition_language, source_url)],
        database_path,
        content_license=content_license,
        recovery=recovery,
    )


class SQLiteDictionaryProvider:
    def __init__(self, path: Path, forms_index_path: Path | None = None):
        self.path = path
        self.forms_index_path = forms_index_path or default_forms_index_path(path)

    @property
    def configured(self) -> bool:
        return self.path.is_file()

    def _connect(self) -> sqlite3.Connection:
        if not self.configured:
            raise FileNotFoundError("Local dictionary index is not installed")
        uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        version = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if version is None or version["value"] != str(SCHEMA_VERSION):
            connection.close()
            raise ValueError("Unsupported local dictionary schema")
        return connection

    @staticmethod
    def _contiguous_subsequence(components: list[str], sentence: list[str]) -> bool:
        width = len(components)
        return any(
            sentence[index:index + width] == components
            for index in range(len(sentence) - width + 1)
        )

    @staticmethod
    def _greeting_context(surface: str, sentence: str) -> bool:
        query = normalize_lexical(surface)
        if not query:
            return False
        return bool(re.match(
            rf"^\s*{re.escape(query)}\s*[,!?:;]",
            unicodedata.normalize("NFKC", sentence).casefold(),
        ))

    @staticmethod
    def _separated_form_matches(components: list[str], sentence: str) -> bool:
        """Require the final particle at a clause boundary, not before its object."""
        normalized_sentence = unicodedata.normalize("NFKC", sentence).casefold()
        matches = list(_LEXICAL.finditer(normalized_sentence))
        tokens = [match.group(0).replace("’", "'") for match in matches]
        for start_index, token in enumerate(tokens):
            if token != components[0]:
                continue
            component_index = 1
            for token_index in range(start_index + 1, len(tokens)):
                if tokens[token_index] != components[component_index]:
                    continue
                component_index += 1
                if component_index != len(components):
                    continue
                tail = normalized_sentence[matches[token_index].end():]
                if re.match(r"\s*(?:$|[,.!?:;])", tail):
                    return True
                break
        return False

    def _entry_ids(self, connection: sqlite3.Connection, language: str, surface: str, sentence: str) -> list[tuple[int, str]]:
        normalized = normalize_lexical(surface)
        greeting_context = self._greeting_context(surface, sentence)
        exact = connection.execute(
            """SELECT id FROM entries
               WHERE language = ? AND normalized_lemma = ?
               ORDER BY
                   CASE WHEN ? AND lower(coalesce(part_of_speech, '')) IN ('intj', 'interj', 'interjection', 'phrase')
                        THEN 0 ELSE 1 END,
                   id
               LIMIT ?""",
            (language, normalized, greeting_context, _ENTRY_MATCH_LIMIT),
        ).fetchall()
        output: list[tuple[int, str]] = [(int(row["id"]), "lemma") for row in exact]
        forms = connection.execute(
            """SELECT DISTINCT entries.id FROM forms
               JOIN entries ON entries.id = forms.entry_id
               WHERE entries.language = ? AND forms.normalized_form = ?
               ORDER BY entries.id LIMIT ?""",
            (language, normalized, _ENTRY_MATCH_LIMIT),
        ).fetchall()
        output.extend((int(row["id"]), "inflection") for row in forms if int(row["id"]) not in {value[0] for value in output})
        sentence_tokens = lexical_tokens(sentence)
        if normalized and sentence_tokens:
            separated = connection.execute(
                """SELECT forms.id AS form_id, forms.entry_id, forms.normalized_form,
                          entries.normalized_lemma
                   FROM form_components JOIN forms ON forms.id = form_components.form_id
                   JOIN entries ON entries.id = forms.entry_id
                   WHERE entries.language = ? AND form_components.component = ?
                   ORDER BY forms.entry_id, forms.id
                   LIMIT ?""",
                (language, normalized, _SEPARATED_FORM_SCAN_LIMIT),
            ).fetchall()
            known = {value[0] for value in output}
            for row in separated:
                components = str(row["normalized_form"]).split()
                entry_id = int(row["entry_id"])
                if len(components) <= 1 or entry_id in known:
                    continue
                lemma_components = str(row["normalized_lemma"]).split()
                if len(lemma_components) > 1:
                    matches_context = self._contiguous_subsequence(components, sentence_tokens)
                else:
                    matches_context = self._separated_form_matches(components, sentence)
                if matches_context:
                    output.append((entry_id, "separated_form"))
                    known.add(entry_id)
        return output[:40]

    def lookup(self, language: str, surface: str, sentence: str) -> list[ProviderEntry]:
        connection = self._connect()
        try:
            matches = self._entry_ids(connection, language, surface, sentence)
            output: list[ProviderEntry] = []
            for entry_id, match_kind in matches:
                row = connection.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
                senses = tuple(
                    ProviderSense(
                        definition=value["definition"],
                        examples=tuple(json.loads(value["examples_json"])),
                        translations={
                            key: tuple(items)
                            for key, items in json.loads(value["translations_json"]).items()
                        },
                    )
                    for value in connection.execute(
                        "SELECT * FROM senses WHERE entry_id = ? ORDER BY sense_index LIMIT ?",
                        (entry_id, _SENSE_LIMIT),
                    )
                )
                pronunciations = tuple(
                    ProviderPronunciation(value["ipa"], value["accent"], value["audio_url"])
                    for value in connection.execute(
                        "SELECT * FROM pronunciations WHERE entry_id = ? ORDER BY pronunciation_index LIMIT ?",
                        (entry_id, _PRONUNCIATION_LIMIT),
                    )
                )
                output.append(ProviderEntry(
                    entry_id=entry_id,
                    language=row["language"],
                    lemma=row["lemma"],
                    part_of_speech=row["part_of_speech"],
                    definition_language=row["definition_language"],
                    source_url=row["source_url"],
                    source_name=row["source_name"],
                    license=row["license"],
                    senses=senses,
                    forms=(),
                    pronunciations=pronunciations,
                    match_kind=match_kind,
                ))
            return output
        finally:
            connection.close()

    @staticmethod
    def _file_stamp(path: Path) -> tuple[int, int, int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino)

    @lru_cache(maxsize=512)
    def _forms_for_entry_cached(
        self,
        entry_id: int,
        source_stamp: tuple[int, int, int, int] | None,
        index_stamp: tuple[int, int, int, int] | None,
    ) -> FormsLookupResult:
        return lookup_forms(self.path, self.forms_index_path, entry_id)

    def forms_for_entry(self, entry_id: int) -> FormsLookupResult:
        """Read bounded display-only forms from the disposable indexed sidecar."""
        return self._forms_for_entry_cached(
            entry_id,
            self._file_stamp(self.path),
            self._file_stamp(self.forms_index_path),
        )
