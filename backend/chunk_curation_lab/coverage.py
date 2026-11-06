"""Coverage manifest: which unique sentences were really analysed.

A sentence is covered only when every one of its segments was citable in a
window that finished successfully. Context-only appearances and overlap never
count, and each sentence counts once however many windows saw it. A plan with
any failed, unprocessed or oversized content is `partial`, never `complete`.
"""
from __future__ import annotations

from dataclasses import dataclass

from chunk_curation_lab.windowing import WindowPlan

NOT_PROCESSED = "not_processed"  # e.g. the job budget ran out first


@dataclass(frozen=True)
class SkippedRange:
    first_sentence_id: int
    last_sentence_id: int
    reason: str


@dataclass(frozen=True)
class CoverageManifest:
    total_sentences: int
    analysable_sentences: int  # excludes empty sentences
    covered_sentence_ids: frozenset[int]
    skipped: tuple[SkippedRange, ...]
    windows_total: int
    windows_done: int
    windows_failed: int
    status: str  # "complete" | "partial" | "empty"

    @property
    def covered_sentences(self) -> int:
        return len(self.covered_sentence_ids)


def build_manifest(
    plan: WindowPlan, done: set[int] | frozenset[int], failed: dict[int, str] | None = None
) -> CoverageManifest:
    failed = dict(failed or {})
    if set(done) & set(failed):
        raise ValueError("a window cannot be both done and failed")
    known = {window.index for window in plan.windows}
    if not set(done) <= known or not set(failed) <= known:
        raise ValueError("unknown window index")

    parts_needed: dict[int, int] = {}
    parts_done: dict[int, set[int]] = {}
    reason_by_sentence: dict[int, str] = {}
    for window in plan.windows:
        for segment in window.citable:
            parts_needed[segment.sentence_id] = segment.parts
            if window.index in done:
                parts_done.setdefault(segment.sentence_id, set()).add(segment.part)
            elif segment.sentence_id not in reason_by_sentence:
                reason_by_sentence[segment.sentence_id] = failed.get(window.index, NOT_PROCESSED)
    for skip in plan.skipped:
        reason_by_sentence[skip.sentence_id] = skip.reason

    covered = frozenset(
        sentence_id for sentence_id, parts in parts_done.items()
        if len(parts) == parts_needed[sentence_id]
    )
    for sentence_id in parts_needed:
        if sentence_id not in covered and sentence_id not in reason_by_sentence:
            reason_by_sentence[sentence_id] = NOT_PROCESSED

    ranges: list[SkippedRange] = []
    for sentence_id in plan.sentence_ids:  # transcript order, for display only
        reason = reason_by_sentence.get(sentence_id) if sentence_id not in covered else None
        if reason is None:
            continue
        last = ranges[-1] if ranges else None
        previous = plan.sentence_ids[plan.sentence_ids.index(sentence_id) - 1] if sentence_id != plan.sentence_ids[0] else None
        if last and last.reason == reason and last.last_sentence_id == previous:
            ranges[-1] = SkippedRange(last.first_sentence_id, sentence_id, reason)
        else:
            ranges.append(SkippedRange(sentence_id, sentence_id, reason))

    empty = sum(1 for skip in plan.skipped if skip.reason == "empty_sentence")
    analysable = len(plan.sentence_ids) - empty
    if analysable == 0:
        status = "empty"
    elif len(covered) == analysable and not failed and len(done) == len(plan.windows):
        status = "complete"
    else:
        status = "partial"
    return CoverageManifest(
        total_sentences=len(plan.sentence_ids),
        analysable_sentences=analysable,
        covered_sentence_ids=covered,
        skipped=tuple(ranges),
        windows_total=len(plan.windows),
        windows_done=len(done),
        windows_failed=len(failed),
        status=status,
    )
