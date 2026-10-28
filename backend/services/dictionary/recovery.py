"""DQ-02 Phase 7B importer recovery rules (pure functions, no I/O).

Three Owner-approved, narrowly scoped repairs to strict sense alignment:

1. **Single-sense fallback.** Only for a Kaikki entry with exactly one raw sense
   (which has a usable gloss) and exactly one raw translation table (every
   translation record, in any language, shares one hint). A translation that
   strict alignment rejected for weak evidence (weak single token,
   stem/compound similarity, stopword/label/lemma-only hint) or for zero
   overlap is attached to that sense with provenance ``single_sense_fallback``
   and the original rejection reason. Zero overlap is not recovered for an
   English non-verb entry whose table reads "to ..." (likely misfiled).
2. **Equivalent parent/child groups.** Wiktextract repeats a parent gloss as
   ``glosses[0]`` of each subsense. Senses are grouped only when they share the
   exact same ``glosses[0]`` *and* the nesting is explicit: at least one member
   is a subsense (``len(glosses) >= 2``) and at most one member is the bare
   parent. Two independent senses that merely have identical text (both
   without subsenses) are never merged. Groups never cross entries, so never
   cross POS. A tie whose winners all sit in one group resolves to the group
   representative (the parent, else the first subsense).
3. **Dungan filter.** zh-CN values with no Han character that are Cyrillic or
   tagged Dungan (``щён``) are not Mandarin definitions and are rejected with a
   counted reason. Other non-Han values are left untouched (approved scope).
"""

import re

# Han script: BMP blocks, Ext B-I, compatibility supplement, and the ideographic zero.
HAN_PATTERN = re.compile(
    "[\\u3007\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff"
    "\\U00020000-\\U0002ee5f\\U0002f800-\\U0002fa1f\\U00030000-\\U000323af]"
)
_HAN = HAN_PATTERN
_CYRILLIC = re.compile("[\\u0400-\\u04ff\\u0500-\\u052f]")

# Strict-alignment rejections a safe single-sense entry may recover from. Every
# recovery additionally requires that the source entry has a single
# translation table (``single_translation_table`` over *raw* records of every
# language): several distinct hints mean the tables cover meanings this entry
# lacks (e.g. "river shore" inside a bank entry whose only sense is financial).
SINGLE_SENSE_RECOVERABLE = frozenset({
    "weak_single_token_evidence",
    "unsupported_morphology",
    "generic_stopword_or_lemma_only",
})
# Zero-overlap hints are recoverable only when ``mismatch_allowed`` (below).
SINGLE_TABLE_RECOVERABLE = frozenset({"single_sense_mismatch"})


def raw_translation_hints(value: dict) -> list[str]:
    """Hints of every raw translation record (any language, before any filter)."""
    translations = value.get("translations")
    hints = []
    for translation in translations if isinstance(translations, list) else []:
        if isinstance(translation, dict):
            hint = translation.get("sense")
            hints.append(hint.strip() if isinstance(hint, str) else "")
    return hints


def single_translation_table(hints: list[str]) -> bool:
    """True when every translation record of the entry shares one hint."""
    return len({" ".join(hint.casefold().split()) for hint in hints}) == 1


def _verb_table_on_non_verb(language: str, part_of_speech: str | None, hints: list[str]) -> bool:
    """English verb-style table ("to ...") under a non-verb entry: likely misplaced."""
    if language != "en" or (part_of_speech or "").casefold() == "verb":
        return False
    return any(" ".join(hint.casefold().split()).startswith("to ") for hint in hints)


def single_sense_fallback_allowed(
    raw_senses: object,
    valid_sense_count: int,
    raw_hints: list[str],
    *,
    language: str,
    part_of_speech: str | None,
) -> tuple[bool, bool]:
    """Return ``(fallback_allowed, mismatch_allowed)`` for one source entry.

    Fallback needs one raw sense that was imported and one raw translation
    table (all records of every language share a hint). A zero-overlap
    mismatch is additionally refused for an English non-verb entry whose table
    reads like a verb gloss ("to ..."), a common sign of misfiled translations.
    """
    allowed = (
        single_sense_entry_is_safe(raw_senses, valid_sense_count)
        and bool(raw_hints)
        and single_translation_table(raw_hints)
    )
    return allowed, allowed and not _verb_table_on_non_verb(language, part_of_speech, raw_hints)


def chinese_script_rejection(code: str, word: str, tags: object = None) -> str | None:
    """Return a rejection reason for a zh-CN value that is not written in Han script."""
    if code != "zh-CN":
        return None
    if _HAN.search(word):
        return None
    tag_text = " ".join(tag for tag in tags if isinstance(tag, str)) if isinstance(tags, list) else ""
    if _CYRILLIC.search(word) or "dungan" in tag_text.casefold():
        return "dungan_cyrillic"
    # Approved scope is the Dungan filter only; other non-Han values (e.g.
    # "DNA", pinyin) are kept unchanged.
    return None


def equivalence_groups(gloss_lists: list[list[str]]) -> list[int]:
    """Map each valid sense index to its group representative index.

    ``gloss_lists[i]`` is the (deduplicated) gloss list of valid sense ``i``;
    ``gloss_lists[i][0]`` is the text stored as its definition.
    """
    representative = list(range(len(gloss_lists)))
    buckets: dict[str, list[int]] = {}
    for index, glosses in enumerate(gloss_lists):
        if glosses:
            buckets.setdefault(glosses[0], []).append(index)
    for members in buckets.values():
        if len(members) < 2:
            continue
        parents = [index for index in members if len(gloss_lists[index]) == 1]
        children = [index for index in members if len(gloss_lists[index]) >= 2]
        if not children or len(parents) > 1:
            continue  # identical text without explicit nesting: independent senses
        leader = parents[0] if parents else members[0]
        for index in members:
            representative[index] = leader
    return representative


def single_sense_entry_is_safe(raw_senses: object, valid_sense_count: int) -> bool:
    """Exactly one sense in the source entry, and it is the one we imported."""
    if not isinstance(raw_senses, list) or valid_sense_count != 1:
        return False
    return sum(1 for sense in raw_senses if isinstance(sense, dict)) == 1
