import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol
from urllib.parse import urlparse


SCHEMA_VERSION = 1
CONTENT_LICENSE = "CC BY-SA 3.0 or GFDL"
SOURCE_NAME = "Wiktionary data extracted by Wiktextract/Kaikki"
_LEXICAL = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


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


def _sense_translation_values(
    records: list[tuple[str, str, str]],
    definition: str,
    *,
    sense_count: int,
) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    definition_tokens = set(lexical_tokens(definition))
    for code, word, hint in records:
        hint_tokens = set(lexical_tokens(hint))
        if sense_count > 1 and (not hint_tokens or not definition_tokens.intersection(hint_tokens)):
            continue
        values = output.setdefault(code, [])
        if word not in values and len(values) < 10:
            values.append(word)
    return output


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


def import_kaikki_sources(
    sources: Iterable[tuple[Path, str, str]],
    database_path: Path,
    *,
    content_license: str = CONTENT_LICENSE,
) -> int:
    """Atomically stream one or more edition-tagged JSONL files into SQLite."""
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
            with input_path.open(encoding="utf-8") as source:
                for line_number, line in enumerate(source, start=1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"Invalid JSON on line {line_number}") from exc
                    language = value.get("lang_code")
                    lemma = value.get("word")
                    if language not in {"en", "de"} or not isinstance(lemma, str):
                        continue
                    lemma = lemma.strip()
                    normalized = normalize_lexical(lemma)
                    if not normalized or len(lemma) > 200:
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

                    translation_records: list[tuple[str, str, str]] = []
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
                            hint = translation.get("sense")
                            translation_records.append((
                                code,
                                word.strip(),
                                hint.strip() if isinstance(hint, str) else "",
                            ))

                    senses = value.get("senses") if isinstance(value.get("senses"), list) else []
                    valid_sense_count = sum(
                        bool(_strings(sense.get("glosses"), limit=1, max_length=2_000))
                        for sense in senses if isinstance(sense, dict)
                    )
                    sense_index = 0
                    for sense in senses:
                        if not isinstance(sense, dict):
                            continue
                        definitions = _strings(sense.get("glosses"), limit=3, max_length=2_000)
                        if not definitions:
                            continue
                        local_records = list(translation_records)
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
                                local_records.append((code, word.strip(), definitions[0]))
                        translations = _sense_translation_values(
                            local_records,
                            definitions[0],
                            sense_count=valid_sense_count,
                        )
                        examples: list[str] = []
                        for example in sense.get("examples", []):
                            if isinstance(example, dict):
                                text = example.get("text")
                                if isinstance(text, str) and 0 < len(text.strip()) <= 2_000:
                                    examples.append(text.strip())
                            if len(examples) >= 5:
                                break
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
                        sense_index += 1

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
                    count += 1
        connection.commit()
    except Exception:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise
    else:
        connection.close()
        temporary.replace(database_path)
    return count


def import_kaikki_jsonl(
    input_path: Path,
    database_path: Path,
    *,
    edition_language: str,
    source_url: str,
) -> int:
    """Backward-compatible one-source import helper used by focused tests."""
    return import_kaikki_sources(
        [(input_path, edition_language, source_url)], database_path
    )


class SQLiteDictionaryProvider:
    def __init__(self, path: Path):
        self.path = path

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
    def _ordered_subsequence(components: list[str], sentence: list[str]) -> bool:
        iterator = iter(sentence)
        return all(any(value == component for value in iterator) for component in components)

    def _entry_ids(self, connection: sqlite3.Connection, language: str, surface: str, sentence: str) -> list[tuple[int, str]]:
        normalized = normalize_lexical(surface)
        exact = connection.execute(
            "SELECT id FROM entries WHERE language = ? AND normalized_lemma = ? ORDER BY id",
            (language, normalized),
        ).fetchall()
        output: list[tuple[int, str]] = [(int(row["id"]), "lemma") for row in exact]
        forms = connection.execute(
            """SELECT DISTINCT entries.id FROM forms
               JOIN entries ON entries.id = forms.entry_id
               WHERE entries.language = ? AND forms.normalized_form = ? ORDER BY entries.id""",
            (language, normalized),
        ).fetchall()
        output.extend((int(row["id"]), "inflection") for row in forms if int(row["id"]) not in {value[0] for value in output})
        sentence_tokens = lexical_tokens(sentence)
        if normalized and sentence_tokens:
            separated = connection.execute(
                """SELECT forms.id AS form_id, forms.entry_id, forms.normalized_form
                   FROM form_components JOIN forms ON forms.id = form_components.form_id
                   JOIN entries ON entries.id = forms.entry_id
                   WHERE entries.language = ? AND form_components.component = ?
                   ORDER BY forms.entry_id, forms.id""",
                (language, normalized),
            ).fetchall()
            known = {value[0] for value in output}
            for row in separated:
                components = str(row["normalized_form"]).split()
                entry_id = int(row["entry_id"])
                if len(components) > 1 and entry_id not in known and self._ordered_subsequence(components, sentence_tokens):
                    output.append((entry_id, "separated_form"))
                    known.add(entry_id)
        return output[:20]

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
                        "SELECT * FROM senses WHERE entry_id = ? ORDER BY sense_index", (entry_id,)
                    )
                )
                forms = tuple(
                    value["form"] for value in connection.execute(
                        "SELECT form FROM forms WHERE entry_id = ? ORDER BY id LIMIT 30", (entry_id,)
                    )
                )
                pronunciations = tuple(
                    ProviderPronunciation(value["ipa"], value["accent"], value["audio_url"])
                    for value in connection.execute(
                        "SELECT * FROM pronunciations WHERE entry_id = ? ORDER BY pronunciation_index", (entry_id,)
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
                    forms=forms,
                    pronunciations=pronunciations,
                    match_kind=match_kind,
                ))
            return output
        finally:
            connection.close()
