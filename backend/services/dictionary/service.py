import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from services.dictionary.provider import (
    DictionaryProvider,
    ProviderEntry,
    ProviderSense,
    SQLiteDictionaryProvider,
    lexical_tokens,
    normalize_lexical,
)
from services.dictionary.pronunciation import (
    DictionaryAndTTSPronunciationProvider,
    PronunciationProvider,
)
from services.schemas.dictionary import (
    DictionaryAttribution,
    DictionaryContext,
    DictionaryExplanation,
    DictionaryExplanationLevels,
    DictionaryLookupResponse,
    DictionarySense,
)


_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "das", "der", "die", "ein", "eine",
    "for", "from", "in", "ist", "it", "mit", "of", "on", "or", "the", "to", "und",
    "von", "was", "zu",
}
_CHINESE_SPLIT = re.compile(r"[;,/、，；／]+")
_CYRILLIC = re.compile(r"[\u0400-\u052f]")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_TRADITIONAL_ONLY = set("國學體銀兒門車馬風書話語東萬與為這個們來時會見長樂後裡麼說")
_SIMPLIFIED_ONLY = set("国学体银儿门车马风书话语东万与为这个们来时会见长乐后里么说")
_INTERJECTION_POS = {"intj", "interj", "interjection"}
_MAX_ALTERNATE_SENSES = 50


@dataclass(frozen=True, slots=True)
class _SenseCandidate:
    entry: ProviderEntry
    sense: ProviderSense
    entry_index: int
    sense_index: int
    lexical_score: int
    interjection_score: int
    morphology_score: int

    @property
    def rank(self) -> tuple[int, int, int]:
        return self.interjection_score, self.lexical_score, self.morphology_score


def default_dictionary_path() -> Path:
    import os

    configured = os.environ.get("DICTIONARY_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "storage" / "dictionary.sqlite3"


class DictionaryService:
    def __init__(
        self,
        provider: DictionaryProvider,
        pronunciation_provider: PronunciationProvider | None = None,
    ):
        self.provider = provider
        self.pronunciation_provider = (
            pronunciation_provider or DictionaryAndTTSPronunciationProvider()
        )

    @classmethod
    def default(cls) -> "DictionaryService":
        return cls(SQLiteDictionaryProvider(default_dictionary_path()))

    @staticmethod
    def _missing(language: str, kind: str) -> DictionaryExplanation:
        return DictionaryExplanation(
            language=language,
            value=None,
            status="unavailable",
            kind=kind,
        )

    @staticmethod
    def _source_field(entry: ProviderEntry, language: str, value: str, kind: str) -> DictionaryExplanation:
        return DictionaryExplanation(
            language=language,
            value=value,
            status="available",
            source=entry.source_name,
            source_url=entry.source_url,
            license=entry.license,
            kind=kind,
        )

    @staticmethod
    def _sense_score(definition: str, examples: tuple[str, ...], sentence: str, query: str) -> int:
        context = set(lexical_tokens(sentence)) - _STOPWORDS - set(lexical_tokens(query))
        evidence = set(lexical_tokens(" ".join((definition, *examples)))) - _STOPWORDS
        return len(context & evidence)

    @staticmethod
    def _interjection_score(entry: ProviderEntry, sentence: str, query: str) -> int:
        if (entry.part_of_speech or "").casefold() not in _INTERJECTION_POS:
            return 0
        greeting = re.match(
            rf"^\s*{re.escape(query)}\s*[,!?:;]",
            sentence,
            flags=re.IGNORECASE,
        )
        return 1 if greeting else 0

    @staticmethod
    def _phrase_matches_context(entry: ProviderEntry, sentence: str, query: str) -> bool:
        lemma_tokens = lexical_tokens(entry.lemma)
        if len(lemma_tokens) <= 1:
            return True
        query_tokens = lexical_tokens(query)
        if query_tokens == lemma_tokens:
            return True
        sentence_tokens = lexical_tokens(sentence)
        width = len(lemma_tokens)
        return any(
            sentence_tokens[index:index + width] == lemma_tokens
            for index in range(len(sentence_tokens) - width + 1)
        )

    def _candidate_entries(
        self,
        entries: list[ProviderEntry],
        sentence: str,
        query: str,
    ) -> list[ProviderEntry]:
        applicable = [
            entry
            for entry in entries
            if self._phrase_matches_context(entry, sentence, query)
        ]
        preferred = [
            entry
            for entry in applicable
            if entry.definition_language == entry.language and entry.senses
        ]
        return preferred or applicable

    def _ranked_senses(
        self,
        entries: list[ProviderEntry],
        sentence: str,
        query: str,
    ) -> list[_SenseCandidate]:
        candidates = [
            _SenseCandidate(
                entry=entry,
                sense=sense,
                entry_index=entry_index,
                sense_index=sense_index,
                lexical_score=self._sense_score(
                    sense.definition, sense.examples, sentence, query
                ),
                interjection_score=self._interjection_score(
                    entry, sentence, query
                ),
                morphology_score=(
                    1 if entry.match_kind in {"inflection", "separated_form"} else 0
                ),
            )
            for entry_index, entry in enumerate(entries)
            for sense_index, sense in enumerate(entry.senses)
        ]
        return sorted(
            candidates,
            key=lambda value: (
                *value.rank,
                -value.entry_index,
                -value.sense_index,
            ),
            reverse=True,
        )

    @staticmethod
    def _sense_identity(candidate: _SenseCandidate) -> str:
        def canonical(value: str) -> str:
            normalized = unicodedata.normalize("NFKC", value).casefold()
            return " ".join(re.sub(r"[^\w]+", " ", normalized).split())

        return canonical(candidate.sense.definition)

    def _select_candidate(
        self,
        entries: list[ProviderEntry],
        sentence: str,
        query: str,
    ) -> tuple[_SenseCandidate | None, str, bool, list[_SenseCandidate]]:
        ranked = self._ranked_senses(entries, sentence, query)
        if not ranked:
            return None, "unavailable", False, []
        selected = ranked[0]
        tied = [value for value in ranked if value.rank == selected.rank]
        ambiguous = len({self._sense_identity(value) for value in tied}) > 1
        if ambiguous:
            method = "ambiguous"
        elif selected.interjection_score:
            method = "part_of_speech"
        elif selected.lexical_score:
            method = "lexical_overlap"
        elif selected.morphology_score:
            method = "morphology"
        else:
            method = "source_order"
        return selected, method, ambiguous, ranked

    @staticmethod
    def _chinese_values(values: tuple[str, ...]) -> list[str]:
        candidates: list[tuple[int, int, int, str]] = []
        seen: set[str] = set()
        for value in values:
            for raw_variant in _CHINESE_SPLIT.split(value):
                variant = unicodedata.normalize("NFKC", raw_variant).strip()
                if not variant or variant in seen or not _HAN.search(variant):
                    continue
                seen.add(variant)
                simplified = sum(character in _SIMPLIFIED_ONLY for character in variant)
                traditional = sum(character in _TRADITIONAL_ONLY for character in variant)
                cyrillic = 1 if _CYRILLIC.search(variant) else 0
                candidates.append((simplified, -traditional, -cyrillic, variant))
        candidates.sort(key=lambda value: value[:3], reverse=True)
        return [value[3] for value in candidates[:3]]

    @classmethod
    def _translation(cls, sense, language: str) -> str | None:
        if sense is None:
            return None
        values = sense.translations.get(language, ())
        if language == "zh-CN":
            values = tuple(cls._chinese_values(values))
        return "; ".join(values[:3]) if values else None

    @lru_cache(maxsize=1_024)
    def _lookup_cached(self, language: str, surface: str, sentence: str) -> DictionaryLookupResponse:
        if not self.provider.configured:
            return self._empty(surface, language, sentence, "not_configured")
        entries = self.provider.lookup(language, surface, sentence)
        if not entries:
            return self._empty(surface, language, sentence, "not_found")

        candidate_entries = self._candidate_entries(entries, sentence, surface)
        if not candidate_entries:
            return self._empty(surface, language, sentence, "not_found")
        ranked, selection_method, ambiguous, all_ranked = self._select_candidate(
            candidate_entries, sentence, surface
        )
        entry = ranked.entry if ranked else candidate_entries[0]
        selected = ranked.sense if ranked else None
        cross_language = "de" if language == "en" else "en"
        definition = selected.definition if selected and entry.definition_language == language else None
        cross = self._translation(selected, cross_language)
        chinese = self._translation(selected, "zh-CN")
        pronunciations = self.pronunciation_provider.options(entry)
        forms = entry.forms
        load_forms = getattr(self.provider, "forms_for_entry", None)
        if not forms and callable(load_forms):
            forms = load_forms(entry.entry_id)
        alternatives: list[DictionarySense] = []
        seen_senses = {self._sense_identity(ranked)} if ranked else set()
        for candidate in all_ranked:
            identity = self._sense_identity(candidate)
            if identity in seen_senses:
                continue
            seen_senses.add(identity)
            alternatives.append(DictionarySense(
                definition=candidate.sense.definition,
                examples=list(candidate.sense.examples),
                source=candidate.entry.source_name,
            ))
            if len(alternatives) >= _MAX_ALTERNATE_SENSES:
                break
        lemma_candidates = list(dict.fromkeys(
            value.lemma for value in candidate_entries
        ))[:20]
        examples = list(selected.examples if selected else ())[:10]
        attributions = list(dict.fromkeys(
            (value.source_name, value.source_url, value.license)
            for value in candidate_entries
        ))
        return DictionaryLookupResponse(
            query=surface,
            surface_form=surface,
            lemma=entry.lemma,
            lemma_candidates=lemma_candidates,
            language=language,
            part_of_speech=entry.part_of_speech,
            status="ambiguous" if ambiguous else "found",
            pronunciations=pronunciations,
            explanations=DictionaryExplanationLevels(
                monolingual=self._source_field(entry, language, definition, "dictionary_definition") if definition else self._missing(language, "dictionary_definition"),
                cross_language=self._source_field(entry, cross_language, cross, "dictionary_translation") if cross else self._missing(cross_language, "dictionary_translation"),
                chinese=self._source_field(entry, "zh-CN", chinese, "dictionary_translation") if chinese else self._missing("zh-CN", "dictionary_translation"),
            ),
            context=DictionaryContext(
                source_sentence=sentence,
                selection_method=selection_method,
                contextual_sense=definition,
                contextual_explanation=None,
            ),
            inflections=list(forms)[:30],
            examples=examples,
            alternate_senses=alternatives,
            source_attribution=[
                DictionaryAttribution(source=source, source_url=url, license=license)
                for source, url, license in attributions
            ],
        )

    def _empty(self, surface: str, language: str, sentence: str, status: str) -> DictionaryLookupResponse:
        cross = "de" if language == "en" else "en"
        return DictionaryLookupResponse(
            query=surface,
            surface_form=surface,
            language=language,
            status=status,
            explanations=DictionaryExplanationLevels(
                monolingual=self._missing(language, "dictionary_definition"),
                cross_language=self._missing(cross, "dictionary_translation"),
                chinese=self._missing("zh-CN", "dictionary_translation"),
            ),
            context=DictionaryContext(
                source_sentence=sentence,
                selection_method="unavailable",
            ),
        )

    def lookup(self, language: str, word: str, sentence: str) -> DictionaryLookupResponse:
        normalized_word = normalize_lexical(word)
        normalized_sentence = unicodedata.normalize("NFKC", sentence).casefold().strip()
        cached = self._lookup_cached(language, normalized_word, normalized_sentence)
        return cached.model_copy(
            update={
                "query": word,
                "surface_form": word,
                "context": cached.context.model_copy(
                    update={"source_sentence": sentence}
                ),
            }
        )
