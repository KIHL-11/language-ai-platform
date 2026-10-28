"""DQ-02 Phase 7B importer recovery rules (pure functions, no I/O).

Three Owner-approved, narrowly scoped repairs to strict sense alignment:

1. **Single-sense fallback.** When a Kaikki entry has exactly one sense and that
   sense has a usable gloss, a translation that strict alignment rejected for
   weak evidence (weak single token, stem/compound similarity, stopword/label/
   lemma-only hint) is attached to that sense with provenance
   ``single_sense_fallback`` and the original rejection reason. A hint with
   content and zero overlap (``single_sense_mismatch``) is recovered only if
   the entry has one translation table (all records share one hint); several
   distinct hints mean the table covers meanings this entry lacks.
2. **Equivalent parent/child groups.** Wiktextract repeats a parent gloss as
   ``glosses[0]`` of each subsense. Senses are grouped only when they share the
   exact same ``glosses[0]`` *and* the nesting is explicit: at least one member
   is a subsense (``len(glosses) >= 2``) and at most one member is the bare
   parent. Two independent senses that merely have identical text (both
   without subsenses) are never merged. Groups never cross entries, so never
   cross POS. A tie whose winners all sit in one group resolves to the group
   representative (the parent, else the first subsense).
3. **Script filter.** Values tagged as Chinese but containing no Han character
   (Dungan Cyrillic such as ``щён``, or bare romanization) are not Chinese
   definitions and are rejected with a counted reason.
"""

import re

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0002ebef]")
_CYRILLIC = re.compile(r"[Ѐ-ӿԀ-ԯ]")

# Strict-alignment rejections a safe single-sense entry may recover from: the
# hint either relates to the only sense (weak overlap, stem/compound similarity)
# or carries no contrary meaning (only stopwords, usage labels or the lemma).
SINGLE_SENSE_RECOVERABLE = frozenset({
    "weak_single_token_evidence",
    "unsupported_morphology",
    "generic_stopword_or_lemma_only",
})
# A hint with content but zero overlap may name a meaning this entry does not
# have (e.g. "river shore" in a bank entry whose only sense is financial). It
# is recovered only when the whole entry has a single translation table (one
# distinct hint), so the table can only describe the single sense.
SINGLE_TABLE_RECOVERABLE = frozenset({"single_sense_mismatch"})


def single_translation_table(hints: list[str]) -> bool:
    """True when every translation record of the entry shares one hint."""
    return len({" ".join(hint.casefold().split()) for hint in hints}) == 1


def chinese_script_rejection(code: str, word: str, tags: object = None) -> str | None:
    """Return a rejection reason for a zh-CN value that is not written in Han script."""
    if code != "zh-CN":
        return None
    if _HAN.search(word):
        return None
    tag_text = " ".join(tag for tag in tags if isinstance(tag, str)) if isinstance(tags, list) else ""
    if _CYRILLIC.search(word) or "dungan" in tag_text.casefold():
        return "dungan_cyrillic"
    return "non_han_script"


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
