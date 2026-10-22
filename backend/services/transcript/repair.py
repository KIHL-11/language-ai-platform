from collections import Counter
from dataclasses import dataclass

from services.schemas.lesson import Lesson, LessonSentence
from services.schemas.library import LibraryItem
from services.transcript.rolling_captions import cues_to_utterances


@dataclass(frozen=True, slots=True)
class TranscriptRepairPlan:
    lesson: Lesson
    old_to_new: dict[int, int | None]
    reset_shadowing_sentence_ids: tuple[int, ...]


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def build_repair_plan(item: LibraryItem) -> TranscriptRepairPlan:
    old = item.lesson.sentences
    utterances = cues_to_utterances([
        (sentence.start, sentence.end, sentence.text) for sentence in old
    ])
    rebuilt: list[LessonSentence] = []
    for index, utterance in enumerate(utterances, start=1):
        overlaps = sorted(
            (
                (_overlap(sentence.start, sentence.end, float(utterance["start"]), float(utterance["end"])), sentence)
                for sentence in old
            ),
            key=lambda value: (-value[0], value[1].id),
        )
        primary = overlaps[0][1] if overlaps and overlaps[0][0] > 0 else None
        base = primary or LessonSentence(
            id=index,
            text=str(utterance["text"]),
            start=float(utterance["start"]),
            end=float(utterance["end"]),
        )
        translations = [
            sentence.translation.strip()
            for overlap, sentence in overlaps
            if overlap > 0 and sentence.translation.strip()
        ]
        translation = " ".join(dict.fromkeys(translations))
        rebuilt.append(base.model_copy(update={
            "id": index,
            "text": str(utterance["text"]),
            "translation": translation or base.translation,
            "start": float(utterance["start"]),
            "end": float(utterance["end"]),
        }))

    mapping: dict[int, int | None] = {}
    for sentence in old:
        candidates = [
            (_overlap(sentence.start, sentence.end, value.start, value.end), value.id)
            for value in rebuilt
        ]
        best = max(candidates, default=(0.0, 0), key=lambda value: (value[0], -value[1]))
        mapping[sentence.id] = best[1] if best[0] > 0 else None
    collisions = Counter(value for value in mapping.values() if value is not None)
    reset = tuple(sorted(
        int(sentence_id)
        for sentence_id in item.shadowing_progress.sentences
        if mapping.get(int(sentence_id)) is None or collisions[mapping[int(sentence_id)]] > 1
    ))
    return TranscriptRepairPlan(
        lesson=item.lesson.model_copy(update={"sentences": rebuilt}),
        old_to_new=mapping,
        reset_shadowing_sentence_ids=reset,
    )
