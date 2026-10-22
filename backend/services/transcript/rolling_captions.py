from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from services.transcript.utterances import (
    TranscriptToken,
    normalized_token,
    render_tokens,
    timed_text_tokens,
    tokens_to_utterances,
)


REVISION_ADJACENCY_SECONDS = 0.25
MAX_ACTIVE_LEXICAL_TOKENS = 200


@dataclass(frozen=True, slots=True)
class CaptionWindow:
    start: float
    end: float
    text: str
    tokens: tuple[TranscriptToken, ...] = ()
    speaker: str | None = None
    window_id: int | None = None
    quality_flags: tuple[str, ...] = ()
    line_decisions: tuple["ReconstructionDecision", ...] = ()


@dataclass(frozen=True, slots=True)
class ReconstructionDecision:
    window_id: int | None
    action: str
    matched_lexical_tokens: int
    reason: str


@dataclass(frozen=True, slots=True)
class CaptionReconstruction:
    tokens: tuple[TranscriptToken, ...]
    decisions: tuple[ReconstructionDecision, ...]
    quality_flags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CaptionTranscript:
    utterances: tuple[dict, ...]
    reconstruction: CaptionReconstruction
    quality_flags: tuple[str, ...]
    requires_review: bool


def _is_lexical(token: TranscriptToken) -> bool:
    return any(character.isalnum() for character in token.text)


def _lexical_entries(tokens: list[TranscriptToken]):
    return [
        (index, normalized_token(token.text), token)
        for index, token in enumerate(tokens)
        if _is_lexical(token)
    ]


def _lexical(tokens: list[TranscriptToken]) -> list[str]:
    return [value for _, value, _ in _lexical_entries(tokens)]


def _suffix_prefix(existing: list[str], incoming: list[str]) -> int:
    for size in range(min(len(existing), len(incoming)), 0, -1):
        if existing[-size:] == incoming[:size]:
            return size
    return 0


def _after_lexical(tokens: list[TranscriptToken], count: int) -> int:
    seen = 0
    for index, token in enumerate(tokens):
        if _is_lexical(token):
            seen += 1
            if seen == count:
                return index + 1
    return len(tokens)


def _active_tail_start(tokens: list[TranscriptToken]) -> int:
    lexical = 0
    for index in range(len(tokens) - 1, -1, -1):
        if _is_lexical(tokens[index]):
            lexical += 1
            if lexical == MAX_ACTIVE_LEXICAL_TOKENS:
                return index
    return 0


def _matching_source_anchor(
    existing: list[TranscriptToken], incoming: list[TranscriptToken]
) -> int | None:
    incoming_lexical = _lexical_entries(incoming)
    if not incoming_lexical:
        return None
    _, value, first = incoming_lexical[0]
    if first.timing_source != "source_inline":
        return None
    for token_index, candidate, token in reversed(_lexical_entries(existing)):
        if (
            candidate == value
            and token.timing_source == "source_inline"
            and abs(token.start - first.start) <= 0.06
        ):
            return token_index
    return None


def _matched_count(existing: list[TranscriptToken], incoming: list[TranscriptToken]) -> int:
    matcher = SequenceMatcher(None, _lexical(existing), _lexical(incoming), autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks())


def _inherit_matched_timing(
    existing: list[TranscriptToken], incoming: list[TranscriptToken]
) -> list[TranscriptToken]:
    existing_entries = _lexical_entries(existing)
    incoming_entries = _lexical_entries(incoming)
    matcher = SequenceMatcher(
        None,
        [value for _, value, _ in existing_entries],
        [value for _, value, _ in incoming_entries],
        autojunk=False,
    )
    output = list(incoming)
    priority = {"estimated": 0, "source_inline": 1, "acoustic": 2}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            previous_position = block.a + offset
            current_position = block.b + offset
            _, _, previous = existing_entries[previous_position]
            incoming_index, _, current = incoming_entries[current_position]
            should_inherit = (
                priority[previous.timing_source] > priority[current.timing_source]
                or previous.timing_source == current.timing_source != "estimated"
                or (
                    previous.timing_source == current.timing_source == "estimated"
                    and previous_position == current_position
                )
            )
            if not should_inherit:
                continue
            if previous.timing_source == "acoustic":
                output[incoming_index] = replace(
                    current,
                    start=previous.start,
                    end=previous.end,
                    timing_source=previous.timing_source,
                )
            elif previous.timing_source == current.timing_source == "estimated":
                merged_start = min(previous.start, current.start)
                output[incoming_index] = replace(
                    current,
                    start=merged_start,
                    end=max(merged_start, min(previous.end, current.end)),
                )
            else:
                output[incoming_index] = replace(
                    current,
                    start=previous.start,
                    end=max(previous.start, min(previous.end, current.end)),
                    timing_source=previous.timing_source,
                )
    previous_end: float | None = None
    for index, token in enumerate(output):
        if _is_lexical(token):
            previous_end = token.end
        elif previous_end is not None:
            output[index] = replace(token, start=previous_end, end=previous_end)
    return output


def _timing_proves_new_speech(
    existing: list[TranscriptToken], incoming: list[TranscriptToken], overlap: int
) -> bool:
    if overlap <= 0:
        return False
    old = _lexical_entries(existing)[-overlap:]
    new = _lexical_entries(incoming)[:overlap]
    if not old or len(old) != len(new):
        return False
    return all(
        previous.timing_source == "source_inline"
        and current.timing_source == "source_inline"
        and current.start >= previous.end + 0.05
        for (_, _, previous), (_, _, current) in zip(old, new)
    )


def _append_without_duplicate_punctuation(
    existing: list[TranscriptToken], incoming: list[TranscriptToken]
) -> list[TranscriptToken]:
    while (
        existing
        and incoming
        and not _is_lexical(existing[-1])
        and not _is_lexical(incoming[0])
        and normalized_token(existing[-1].text) == normalized_token(incoming[0].text)
    ):
        incoming = incoming[1:]
    return existing + incoming


def _reconcile(
    existing: list[TranscriptToken],
    incoming: list[TranscriptToken],
    *,
    temporally_related: bool,
    window_id: int | None,
) -> tuple[list[TranscriptToken], ReconstructionDecision, bool]:
    if not existing:
        return incoming, ReconstructionDecision(
            window_id, "append", 0, "first lexical content"
        ), False
    if not incoming or not temporally_related:
        return existing + incoming, ReconstructionDecision(
            window_id, "append", 0, "temporally distinct content"
        ), False

    tail_start = _active_tail_start(existing)
    active_tail = existing[tail_start:]
    existing_lexical = _lexical_entries(active_tail)
    incoming_lexical = _lexical_entries(incoming)
    if (
        existing_lexical
        and incoming_lexical
        and existing_lexical[-1][2].speaker
        and incoming_lexical[0][2].speaker
        and existing_lexical[-1][2].speaker != incoming_lexical[0][2].speaker
    ):
        return existing + incoming, ReconstructionDecision(
            window_id,
            "append",
            0,
            "explicit source speaker changed",
        ), False
    if (
        existing_lexical
        and incoming_lexical
        and incoming_lexical[0][2].timing_source == "source_inline"
        and incoming_lexical[0][2].start >= existing_lexical[-1][2].end + 0.05
    ):
        return existing + incoming, ReconstructionDecision(
            window_id,
            "append",
            0,
            "source timestamps prove later speech despite overlapping display windows",
        ), False

    anchor = _matching_source_anchor(active_tail, incoming)
    if anchor is not None:
        absolute_anchor = tail_start + anchor
        active = existing[absolute_anchor:]
        matches = _matched_count(active, incoming)
        denominator = max(1, min(len(_lexical(active)), len(_lexical(incoming))))
        if matches / denominator >= 0.5:
            revised = _inherit_matched_timing(active, incoming)
            return existing[:absolute_anchor] + revised, ReconstructionDecision(
                window_id,
                "replace_active_revision",
                matches,
                "same source-provided timestamp anchors a revised display snapshot",
            ), False

    existing_values = _lexical(active_tail)
    incoming_values = _lexical(incoming)
    overlap = _suffix_prefix(existing_values, incoming_values)
    if overlap and not _timing_proves_new_speech(active_tail, incoming, overlap):
        if overlap >= 2:
            cut = _after_lexical(incoming, overlap)
            return _append_without_duplicate_punctuation(
                existing, incoming[cut:]
            ), ReconstructionDecision(
                window_id,
                "append_novel_suffix",
                overlap,
                "temporally related exact lexical suffix/prefix",
            ), False
        if overlap == 1:
            old = _lexical_entries(active_tail)[-1][2]
            new = _lexical_entries(incoming)[0][2]
            if (
                old.timing_source == "source_inline"
                and new.timing_source == "source_inline"
                and abs(old.start - new.start) <= 0.06
            ):
                cut = _after_lexical(incoming, 1)
                return _append_without_duplicate_punctuation(
                    existing, incoming[cut:]
                ), ReconstructionDecision(
                    window_id,
                    "append_novel_suffix",
                    1,
                    "single repeated token has the same source timestamp",
                ), False

    matches = _matched_count(active_tail, incoming)
    uncertain = bool(matches >= 2 and temporally_related)
    marked = [replace(token, ambiguous=True) for token in incoming] if uncertain else incoming
    return existing + marked, ReconstructionDecision(
        window_id,
        "preserve_ambiguous_content" if uncertain else "append",
        matches,
        "overlap was not safe to classify as display revision",
    ), uncertain


def reconcile_caption_lines(
    lines: list[list[TranscriptToken]], *, window_id: int
) -> tuple[list[TranscriptToken], tuple[ReconstructionDecision, ...], tuple[str, ...]]:
    output: list[TranscriptToken] = []
    decisions: list[ReconstructionDecision] = []
    ambiguous = False
    for line in lines:
        output, decision, uncertain = _reconcile(
            output,
            line,
            temporally_related=True,
            window_id=window_id,
        )
        decisions.append(decision)
        ambiguous = ambiguous or uncertain
    return output, tuple(decisions), ("ambiguous_revision",) if ambiguous else ()


def reconstruct_caption_windows(windows: list[CaptionWindow]) -> CaptionReconstruction:
    emitted: list[TranscriptToken] = []
    decisions: list[ReconstructionDecision] = []
    flags: set[str] = set()
    previous: CaptionWindow | None = None
    for window in sorted(windows, key=lambda value: (value.start, value.end)):
        incoming = list(window.tokens) or timed_text_tokens(
            window.text,
            window.start,
            window.end,
            speaker=window.speaker,
            timing_source="estimated",
            source_window=window.window_id,
        )
        flags.update(window.quality_flags)
        decisions.extend(window.line_decisions)
        if not incoming:
            previous = window
            continue
        related = bool(
            previous
            and (
                window.start < previous.end
                or 0 <= window.start - previous.end <= REVISION_ADJACENCY_SECONDS
            )
        )
        emitted, decision, uncertain = _reconcile(
            emitted,
            incoming,
            temporally_related=related,
            window_id=window.window_id,
        )
        decisions.append(decision)
        if uncertain:
            flags.add("ambiguous_revision")
        previous = window
    if any(token.timing_source == "estimated" for token in emitted):
        flags.add("estimated_timing")
    lexical = [token for token in emitted if _is_lexical(token)]
    if any(current.start + 0.02 < previous.start for previous, current in zip(lexical, lexical[1:])):
        flags.add("non_monotonic_timing")
    return CaptionReconstruction(tuple(emitted), tuple(decisions), tuple(sorted(flags)))


def canonical_caption_tokens(windows: list[CaptionWindow]) -> list[TranscriptToken]:
    return list(reconstruct_caption_windows(windows).tokens)


def caption_windows_to_utterances(
    windows: list[CaptionWindow], *, offset: float = 0.0
) -> list[dict]:
    return tokens_to_utterances(canonical_caption_tokens(windows), offset=offset)


def reconstruct_caption_transcript(
    windows: list[CaptionWindow], *, offset: float = 0.0
) -> CaptionTranscript:
    reconstruction = reconstruct_caption_windows(windows)
    utterances = tokens_to_utterances(list(reconstruction.tokens), offset=offset)
    flags = set(reconstruction.quality_flags)
    for utterance in utterances:
        start = float(utterance["start"])
        end = float(utterance["end"])
        if end <= start:
            flags.add("invalid_utterance_timing")
        if end - start > 30:
            flags.add("excessive_utterance_duration")
    for previous, current in zip(utterances, utterances[1:]):
        previous_tokens = _lexical(timed_text_tokens(
            str(previous["text"]), float(previous["start"]), float(previous["end"])
        ))
        current_tokens = _lexical(timed_text_tokens(
            str(current["text"]), float(current["start"]), float(current["end"])
        ))
        if (
            float(current["start"]) < float(previous["end"])
            and _suffix_prefix(previous_tokens, current_tokens) >= 2
        ):
            flags.add("adjacent_card_carryover")
    critical = {
        "ambiguous_revision",
        "adjacent_card_carryover",
        "invalid_utterance_timing",
        "non_monotonic_timing",
        "excessive_utterance_duration",
    }
    return CaptionTranscript(
        tuple(utterances),
        reconstruction,
        tuple(sorted(flags)),
        bool(flags & critical),
    )


def cues_to_utterances(
    cues: list[tuple[float, float, str]], *, offset: float = 0.0
) -> list[dict]:
    return caption_windows_to_utterances(
        [
            CaptionWindow(start, end, text, window_id=index)
            for index, (start, end, text) in enumerate(cues)
        ],
        offset=offset,
    )


def caption_window_text(tokens: list[TranscriptToken]) -> str:
    return render_tokens(tokens)
