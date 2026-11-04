"""Cloud dictionary source (B-3A): a synthetic EN/DE fixture, never local data.

Cloud lookups reuse ``DictionaryService`` (ranking, context selection and the
response shape) with a provider built only from the packaged synthetic fixture.
The Personal Desktop SQLite release is never opened in cloud mode, and there is
no way to point the cloud provider at an arbitrary path: the only supported
source is ``CLOUD_DICTIONARY_SOURCE=synthetic-fixture``. Anything else leaves
the cloud dictionary unconfigured, and the route fails closed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from services.dictionary.provider import (
    ProviderEntry,
    ProviderSense,
    normalize_lexical,
)
from services.dictionary.service import DictionaryService
from services.schemas.dictionary import DictionaryPronunciation


SYNTHETIC_FIXTURE_SOURCE = "synthetic-fixture"
SYNTHETIC_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "cloud_synthetic_en_de.json"
_LANGUAGES = {"en", "de"}


class SyntheticFixtureDictionaryProvider:
    """In-memory provider over the packaged synthetic fixture (read once)."""

    def __init__(self, path: Path = SYNTHETIC_FIXTURE_PATH):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("fixture") != "language-ai-cloud-synthetic-dictionary":
            raise ValueError("Not the synthetic cloud dictionary fixture")
        source_name = str(payload["source_name"])
        source_url = str(payload["source_url"])
        license_name = str(payload["license"])
        self._entries: list[ProviderEntry] = []
        self._index: dict[tuple[str, str], list[tuple[int, str]]] = {}
        for entry_id, raw in enumerate(payload["entries"], start=1):
            language = raw["language"]
            if language not in _LANGUAGES:
                raise ValueError("Synthetic fixture language must be en or de")
            forms = tuple(str(value) for value in raw.get("forms", ()))
            senses = tuple(
                ProviderSense(
                    definition=str(sense["definition"]),
                    examples=tuple(str(value) for value in sense.get("examples", ())),
                    translations={
                        key: tuple(str(value) for value in values)
                        for key, values in sense.get("translations", {}).items()
                    },
                )
                for sense in raw["senses"]
            )
            self._entries.append(ProviderEntry(
                entry_id=entry_id,
                language=language,
                lemma=str(raw["lemma"]),
                part_of_speech=raw.get("part_of_speech"),
                definition_language=language,
                source_url=source_url,
                source_name=source_name,
                license=license_name,
                senses=senses,
                forms=forms,
                pronunciations=(),
                match_kind="lemma",
            ))
            self._index.setdefault((language, normalize_lexical(raw["lemma"])), []).append(
                (entry_id, "lemma")
            )
            for form in forms:
                key = (language, normalize_lexical(form))
                matches = self._index.setdefault(key, [])
                if all(existing != entry_id for existing, _ in matches):
                    matches.append((entry_id, "inflection"))

    @property
    def configured(self) -> bool:
        return True

    def lookup(self, language: str, surface: str, sentence: str) -> list[ProviderEntry]:
        matches = self._index.get((language, normalize_lexical(surface)), [])
        return [
            _with_match_kind(self._entries[entry_id - 1], match_kind)
            for entry_id, match_kind in matches
        ]

    def forms_for_entry(self, entry_id: int) -> tuple[str, ...]:
        return self._entries[entry_id - 1].forms


def _with_match_kind(entry: ProviderEntry, match_kind: str) -> ProviderEntry:
    return ProviderEntry(
        entry_id=entry.entry_id,
        language=entry.language,
        lemma=entry.lemma,
        part_of_speech=entry.part_of_speech,
        definition_language=entry.definition_language,
        source_url=entry.source_url,
        source_name=entry.source_name,
        license=entry.license,
        senses=entry.senses,
        forms=entry.forms,
        pronunciations=entry.pronunciations,
        match_kind=match_kind,
    )


class NoPronunciationProvider:
    """Cloud offers no recordings or TTS fallback (TTS has no cloud budget)."""

    def options(self, entry: ProviderEntry) -> list[DictionaryPronunciation]:
        return []


def cloud_dictionary_from_environment() -> DictionaryService | None:
    """The configured cloud dictionary, or None (the route then fails closed)."""

    if os.getenv("CLOUD_DICTIONARY_SOURCE", "").strip() != SYNTHETIC_FIXTURE_SOURCE:
        return None
    return DictionaryService(
        SyntheticFixtureDictionaryProvider(),
        pronunciation_provider=NoPronunciationProvider(),
    )
