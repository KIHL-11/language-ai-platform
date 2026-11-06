"""Synthetic EN/DE transcripts and a stub "provider" for the curation lab.

Nothing here is an AI judgement. STUB_SCORES are explicit test inputs that
stand in for a semantic scorer; the stub provider only reports which known
phrases occur in a window's citable sentences, with those fixed scores.
"""
from __future__ import annotations

from chunk_curation_lab.provenance import ProviderObservation
from chunk_curation_lab.snapshot import LibrarySnapshot, SnapshotSentence
from chunk_curation_lab.windowing import WindowPlan

FILLER = {
    "en": "In part {n} of the show we go over the schedule for segment {n}.",
    "de": "Im Teil {n} der Sendung besprechen wir den Ablauf von Abschnitt {n}.",
}

# phrase -> carrier sentence. High quality (HQ) and low quality (LQ) sets.
HQ = {
    "en": {
        "as far as I know": "As far as I know, the bakery opens at seven.",
        "it depends on": "Honestly, it depends on the weather tomorrow.",
        "look forward to": "We really look forward to your questions.",
        "make up your mind": "You need to make up your mind before Friday.",
        "on the other hand": "On the other hand, the train is much cheaper.",
        "to be honest": "To be honest, I didn't expect that answer.",
        "keep in mind": "Keep in mind that the shop closes early today.",
        "it turns out": "It turns out the meeting was moved to Monday.",
        "get used to": "It takes a while to get used to the noise.",
        "come up with": "Can you come up with a better plan?",
    },
    "de": {
        "auf jeden Fall": "Auf jeden Fall rufe ich dich morgen an.",
        "es kommt darauf an": "Es kommt darauf an, wie das Wetter wird.",
        "den Nagel auf den Kopf": "Damit hast du den Nagel auf den Kopf getroffen.",
        "im Grunde genommen": "Im Grunde genommen hast du recht.",
        "Bescheid sagen": "Kannst du mir bitte Bescheid sagen?",
        "keine Ahnung": "Ehrlich gesagt habe ich keine Ahnung.",
        "zum Beispiel": "Wir könnten zum Beispiel früher anfangen.",
        "nach wie vor": "Das Problem besteht nach wie vor.",
        "ab und zu": "Ich gehe ab und zu ins Kino.",
        "unter vier Augen": "Lass uns das unter vier Augen besprechen.",
    },
}
LQ = {
    "en": {
        "studio number": "This is studio number nine in Studio Beta.",
        "Studio Beta tower": "Welcome back to the Studio Beta tower broadcast.",
    },
    "de": {
        "Studio Beta Turm": "Willkommen zurück im Studio Beta Turm.",
        "Nummer neun": "Das ist Raum Nummer neun im Studio Beta.",
    },
}


def _hq_scores():
    return {"reusability": 3, "naturalness": 3, "context_completeness": 3,
            "independence": 3, "level_fit": 2, "transfer": 3}


def _lq_scores():
    return {"reusability": 1, "naturalness": 1, "context_completeness": 1,
            "independence": 0, "level_fit": 1, "transfer": 0}


# Explicit per-phrase stub scores (lower-cased phrase -> scores).
STUB_SCORES = {
    **{phrase.casefold(): _hq_scores() for language in HQ for phrase in HQ[language]},
    **{phrase.casefold(): _lq_scores() for language in LQ for phrase in LQ[language]},
}


def transcript(language: str, total: int, placed: dict[int, str]) -> LibrarySnapshot:
    """`placed` maps sentence id -> carrier sentence text; others are filler."""
    sentences = tuple(
        SnapshotSentence(n, placed.get(n, FILLER[language].format(n=n)), float(n * 4), float(n * 4 + 3.5))
        for n in range(1, total + 1)
    )
    return LibrarySnapshot("synthetic-owner", f"item-{language}", f"lesson-{language}", language, sentences)


def spread(language: str, total: int, where: str) -> dict[int, str]:
    """Place all HQ carriers at the start, middle or end of the transcript."""
    carriers = list(HQ[language].values())
    count = len(carriers)
    first = {"start": 1, "middle": total // 2 - count // 2, "end": total - count + 1}[where]
    return {first + index: text for index, text in enumerate(carriers)}


def stub_provider(snapshot: LibrarySnapshot, plan: WindowPlan, scores=None) -> list[ProviderObservation]:
    """Report every known phrase found in each window's citable sentences."""
    scores = STUB_SCORES if scores is None else scores
    phrases = [*HQ[snapshot.language], *LQ[snapshot.language]]
    observations = []
    for window in plan.windows:
        for sentence_id in sorted(window.citable_sentence_ids):
            text = snapshot.sentence(sentence_id).text
            for phrase in phrases:
                if phrase.casefold() in text.casefold():
                    observations.append(ProviderObservation(
                        window_index=window.index, text=phrase, evidence_sentence_id=sentence_id,
                        scores=scores.get(phrase.casefold(), {}),
                    ))
    return observations
