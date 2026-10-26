from services.dictionary.provider import lexical_tokens, normalize_lexical
from services.dictionary.service import DictionaryService
from services.library.store import LearningLibraryStore
from services.schemas.library import LibraryItem
from services.schemas.study_bank import MediaStudyProvenance, StudyItem, StudyItemInput
from services.study_bank.store import StudyBankStore, normalized_identity


class ContextualWordError(ValueError):
    """The requested word cannot be grounded in the selected Library sentence."""


class ContextualDictionaryUnavailableError(RuntimeError):
    """The local dictionary is unavailable, unreadable, or has no matching entry."""


class ContextualWordService:
    def __init__(
        self,
        library: LearningLibraryStore,
        study_bank: StudyBankStore,
        dictionary: DictionaryService,
    ):
        self.library = library
        self.study_bank = study_bank
        self.dictionary = dictionary

    @staticmethod
    def _sentence(item: LibraryItem, sentence_id: int):
        matches = [sentence for sentence in item.lesson.sentences if sentence.id == sentence_id]
        if len(matches) != 1:
            raise ContextualWordError(
                "sentence_id must identify exactly one Library sentence"
            )
        return matches[0]

    @staticmethod
    def _validate_token(word: str, sentence_text: str) -> None:
        normalized = normalize_lexical(word)
        if not normalized or len(lexical_tokens(word)) != 1:
            raise ContextualWordError("word must be one complete lexical token")
        if normalized not in lexical_tokens(sentence_text):
            raise ContextualWordError("word is not a complete lexical token in the sentence")

    def build_input(
        self,
        library_item_id: str,
        user_id: str,
        sentence_id: int,
        word: str,
    ) -> StudyItemInput:
        item = self.library.get_item(library_item_id)
        sentence = self._sentence(item, sentence_id)
        self._validate_token(word, sentence.text)
        try:
            entry = self.dictionary.lookup(
                item.lesson.source_language,
                word,
                sentence.text,
            )
        except (OSError, ValueError) as exc:
            raise ContextualDictionaryUnavailableError(
                "Local dictionary data could not be read"
            ) from exc
        if entry.status == "not_configured":
            raise ContextualDictionaryUnavailableError(
                "Local dictionary data is not installed"
            )
        if entry.status not in {"found", "ambiguous"} or not entry.lemma:
            raise ContextualWordError("No source-grounded dictionary entry was found")
        if entry.language != item.lesson.source_language:
            raise ContextualWordError("Dictionary entry language does not match the Library item")
        chinese = entry.explanations.chinese
        meaning = (
            chinese.value
            if chinese.status == "available" and chinese.value
            else ""
        )
        return StudyItemInput(
            user_id=user_id,
            kind="word",
            source_language=item.lesson.source_language,
            target_language=item.lesson.target_language,
            text=word,
            meaning=meaning,
            lemma=entry.lemma,
            part_of_speech=entry.part_of_speech,
            example=entry.examples[0] if entry.examples else None,
            provenance=[MediaStudyProvenance(
                library_item_id=item.id,
                lesson_id=item.lesson.id,
                source_url=item.lesson.source_url,
                media_title=item.lesson.title,
                sentence_id=sentence.id,
                sentence_text=sentence.text,
                sentence_translation=sentence.translation,
                start=sentence.start,
                end=sentence.end,
            )],
        )

    def status(self, value: StudyItemInput) -> StudyItem | None:
        _, identity = normalized_identity(value)
        return next((
            item
            for item in self.study_bank.list_items(value.user_id, active=None)
            if item.kind == "word"
            and item.source_language == value.source_language
            and item.normalized_text == identity
        ), None)

    def save(self, value: StudyItemInput) -> tuple[StudyItem, bool]:
        items, created = self.study_bank.save_items([value])
        return items[0], created == 1
