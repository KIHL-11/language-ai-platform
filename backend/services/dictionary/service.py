from functools import lru_cache
from pathlib import Path

from services.dictionary.provider import (
    DictionaryProvider,
    ProviderEntry,
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

    def _select_sense(self, entry: ProviderEntry, sentence: str, query: str):
        if not entry.senses:
            return None, "unavailable"
        scores = [self._sense_score(sense.definition, sense.examples, sentence, query) for sense in entry.senses]
        best = max(scores)
        if best > 0 and scores.count(best) == 1:
            return entry.senses[scores.index(best)], "lexical_overlap"
        if len(entry.senses) == 1:
            return entry.senses[0], "source_order"
        return entry.senses[0], "ambiguous"

    @staticmethod
    def _translation(sense, language: str) -> str | None:
        if sense is None:
            return None
        values = sense.translations.get(language, ())
        return "; ".join(values[:3]) if values else None

    @lru_cache(maxsize=1_024)
    def _lookup_cached(self, language: str, surface: str, sentence: str) -> DictionaryLookupResponse:
        if not self.provider.configured:
            return self._empty(surface, language, sentence, "not_configured")
        entries = self.provider.lookup(language, surface, sentence)
        if not entries:
            return self._empty(surface, language, sentence, "not_found")

        preferred = [value for value in entries if value.definition_language == language and value.senses]
        entry = (preferred or entries)[0]
        selected, selection_method = self._select_sense(entry, sentence, surface)
        cross_language = "de" if language == "en" else "en"
        definition = selected.definition if selected and entry.definition_language == language else None
        cross = self._translation(selected, cross_language)
        chinese = self._translation(selected, "zh-CN")
        pronunciations = self.pronunciation_provider.options(entry)
        alternatives = [
            DictionarySense(
                definition=value.definition,
                examples=list(value.examples),
                source=entry.source_name,
            )
            for value in entry.senses
            if selected is None or value is not selected
        ][:10]
        lemma_candidates = list(dict.fromkeys(value.lemma for value in entries))[:20]
        examples = list(selected.examples if selected else ())[:10]
        return DictionaryLookupResponse(
            query=surface,
            surface_form=surface,
            lemma=entry.lemma,
            lemma_candidates=lemma_candidates,
            language=language,
            part_of_speech=entry.part_of_speech,
            status="ambiguous" if len(lemma_candidates) > 1 or selection_method == "ambiguous" else "found",
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
            inflections=list(entry.forms)[:30],
            examples=examples,
            alternate_senses=alternatives,
            source_attribution=[DictionaryAttribution(
                source=entry.source_name,
                source_url=entry.source_url,
                license=entry.license,
            )],
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
        normalized_sentence = " ".join(lexical_tokens(sentence))
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
