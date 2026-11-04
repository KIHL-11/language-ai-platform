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
        *,
        cloud: bool = False,
    ):
        self.library = library
        self.study_bank = study_bank
        self.dictionary = dictionary
        # Cloud (B-4) is stricter than Personal M5.2, which keeps its
        # behaviour: an ambiguous lookup whose candidates are different
        # lemmas has no safe card identity, so saving it is refused rather
        # than merging unrelated words.
        self.cloud = cloud

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
        if (
            self.cloud
            and entry.status == "ambiguous"
            and len({value.casefold() for value in entry.lemma_candidates}) > 1
        ):
            raise ContextualWordError(
                "ambiguous_lemma: this form matches several dictionary words in this sentence"
            )
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
        find = getattr(self.study_bank, "find_by_identity", None)
        if callable(find):
            # Exactly the card a save would write to (includes target language).
            return find(value, identity)
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


class OwnerLibraryView:
    """The narrow Library read ContextualWordService needs, bound to one
    trusted owner: an unknown or other-owner item is a KeyError (404)."""

    def __init__(self, repository, owner_user_id: str):
        self._repository = repository
        self._owner = owner_user_id

    def get_item(self, library_item_id: str) -> LibraryItem:
        return self._repository.get_for_owner(self._owner, library_item_id)


class OwnerStudyBankView:
    """The narrow Study Bank surface ContextualWordService needs, bound to
    one trusted owner of the PostgreSQL repository. It refuses any value that
    names another user, so a client user_id can never redirect a write."""

    def __init__(self, repository, owner_user_id: str):
        self._repository = repository
        self._owner = owner_user_id

    def list_items(self, user_id: str, active: bool | None = None) -> list[StudyItem]:
        if user_id != self._owner:
            raise ValueError("Study Bank owner does not match authenticated account")
        return self._repository.list_items(self._owner, kind="word", active=active)

    def save_items(self, values: list[StudyItemInput]) -> tuple[list[StudyItem], int]:
        return self._repository.save_items_for_owner(self._owner, values)

    def find_by_identity(self, value: StudyItemInput, identity: str) -> StudyItem | None:
        if value.user_id != self._owner:
            raise ValueError("Study Bank owner does not match authenticated account")
        return self._repository.get_by_identity(
            self._owner,
            kind=value.kind,
            source_language=value.source_language,
            target_language=value.target_language,
            normalized_text=identity,
        )
