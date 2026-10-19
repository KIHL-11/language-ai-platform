from statistics import mean

from services.schemas.shadowing import ShadowingMetrics, TimedWord
from services.shadowing.alignment import (
    align_tokens,
    normalize_timed_words,
    normalize_tokens,
    public_alignment,
)


def _milliseconds(value: float) -> int:
    return round(value * 1_000)


def calculate_metrics(
    target_text: str,
    recognized_text: str,
    attempt_words: list[TimedWord],
    reference_words: list[TimedWord] | None,
    *,
    timing_unavailable_reason: str | None = None,
) -> ShadowingMetrics:
    target = normalize_tokens(target_text)
    recognized = normalize_tokens(recognized_text)
    steps = align_tokens(target, recognized)
    counts = {name: sum(step.status == name for step in steps) for name in ("matched", "missed", "extra", "substituted")}
    edit_distance = counts["missed"] + counts["extra"] + counts["substituted"]
    denominator = max(len(target), len(recognized), 1)
    transcript_match = max(0.0, 1 - edit_distance / denominator) * 100

    attempt = normalize_timed_words(attempt_words)
    reference = normalize_timed_words(reference_words or [])
    timing_available = bool(reference)
    reference_duration = attempt_duration = duration_delta = None
    duration_ratio = start_delay = word_mae = pause_mae = None
    matched_pairs: list[tuple[TimedWord, TimedWord]] = []
    if timing_available:
        reference_alignment = align_tokens(target, [word.word for word in reference])
        reference_edits = sum(step.status != "matched" for step in reference_alignment)
        reference_quality = max(0.0, 1 - reference_edits / max(len(target), len(reference), 1))
        if reference_quality < 0.70 or not any(step.status == "matched" for step in reference_alignment):
            timing_available = False
            timing_unavailable_reason = "reference_alignment_unreliable"

    if timing_available and attempt:
        reference_matches = {
            step.target_index: step.recognized_index
            for step in reference_alignment
            if step.status == "matched"
            and step.target_index is not None
            and step.recognized_index is not None
        }
        for step in steps:
            if step.status == "matched" and step.target_index is not None and step.recognized_index is not None:
                reference_index = reference_matches.get(step.target_index)
                if reference_index is not None and step.recognized_index < len(attempt):
                    matched_pairs.append((reference[reference_index], attempt[step.recognized_index]))

        reference_duration = _milliseconds(max(0, matched_pairs[-1][0].end - matched_pairs[0][0].start)) if matched_pairs else None
        attempt_duration = _milliseconds(max(0, matched_pairs[-1][1].end - matched_pairs[0][1].start)) if matched_pairs else None
        if reference_duration is not None and attempt_duration is not None:
            duration_delta = attempt_duration - reference_duration
            if reference_duration > 0:
                duration_ratio = abs(duration_delta) / reference_duration
        if matched_pairs:
            start_delay = _milliseconds(matched_pairs[0][1].start - matched_pairs[0][0].start)
        if len(matched_pairs) >= 3:
            ref_origin, attempt_origin = matched_pairs[0][0].start, matched_pairs[0][1].start
            word_mae = round(mean(abs((attempt_word.start - attempt_origin) - (reference_word.start - ref_origin)) for reference_word, attempt_word in matched_pairs[1:]) * 1_000)
            gaps = [
                abs((matched_pairs[index][1].start - matched_pairs[index - 1][1].end) - (matched_pairs[index][0].start - matched_pairs[index - 1][0].end))
                for index in range(1, len(matched_pairs))
            ]
            if len(gaps) >= 2:
                pause_mae = round(mean(gaps) * 1_000)
    elif timing_available:
        timing_unavailable_reason = "insufficient_speech"

    recognized_speech = bool(recognized and attempt)
    sync_target = bool(
        recognized_speech
        and transcript_match >= 90
        and duration_ratio is not None
        and duration_ratio <= 0.20
        and (not timing_available or (start_delay is not None and abs(start_delay) <= 600))
        and (word_mae is None or word_mae <= 500)
        and (pause_mae is None or pause_mae <= 450)
    )
    return ShadowingMetrics(
        recognized_text=recognized_text.strip(),
        target_word_count=len(target),
        recognized_word_count=len(recognized),
        matched_count=counts["matched"],
        missed_count=counts["missed"],
        extra_count=counts["extra"],
        substituted_count=counts["substituted"],
        transcript_match_percent=round(transcript_match, 1),
        reference_speech_duration_ms=reference_duration,
        attempt_speech_duration_ms=attempt_duration if recognized_speech else None,
        duration_delta_ms=duration_delta,
        duration_delta_ratio=round(duration_ratio, 4) if duration_ratio is not None else None,
        start_delay_ms=start_delay,
        word_timing_mae_ms=word_mae,
        pause_mae_ms=pause_mae,
        aligned_timed_word_count=len(matched_pairs),
        timing_reference_available=timing_available,
        timing_unavailable_reason=None if timing_available else (timing_unavailable_reason or "reference_audio_unavailable"),
        sync_target_met=sync_target,
        aligned_words=public_alignment(steps),
    )
