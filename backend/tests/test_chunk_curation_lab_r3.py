"""Round 3 counterexamples (Codex M1-M3) for the curation lab.

M1: duplicate evidence keys must merge independently of order.
M2: grounding must stay inside the characters the window could cite.
M3: one malformed observation must be rejected without aborting the reduce.
Synthetic data and explicit stub scores only.
"""
from __future__ import annotations

import itertools

import pytest

from chunk_curation_lab import provenance, reduce as reduce_module
from chunk_curation_lab.coverage import build_manifest
from chunk_curation_lab.provenance import ProviderObservation, verify_observation
from chunk_curation_lab.reduce import reduce_candidates
from chunk_curation_lab.scoring import QUALITY_FLOOR
from chunk_curation_lab.snapshot import LibrarySnapshot, SnapshotSentence
from chunk_curation_lab.windowing import Segment, Window, WindowPlan, build_windows, estimate_tokens
from chunk_curation_synthetic import STUB_SCORES

SENTENCE = "Honestly, it depends on the weather."
# On the floor edge: 1.80 with an exact match (reliability 3), 1.73 without.
EDGE = {"reusability": 2, "naturalness": 2, "context_completeness": 2, "independence": 2,
        "level_fit": 0, "transfer": 1}


def _one(text=SENTENCE, budget=200):
    snapshot = LibrarySnapshot("o", "item", "lesson", "en", (SnapshotSentence(1, text, 1.0, 4.0),))
    plan = build_windows(snapshot.sentences, language="en", budget_tokens=budget)
    return snapshot, plan, build_manifest(plan, {w.index for w in plan.windows})


def _everything(result):
    def candidate(c):
        return (c.identity, c.display_text, c.status, c.assessment.status, c.assessment.score,
                tuple(sorted(c.assessment.dimensions.items())), c.observations, c.tied_with, c.variations,
                tuple((s.key, s.reliability, s.sentence_fingerprint, s.start, s.end) for s in c.sources))
    return (result.status, tuple(map(candidate, result.recommended)), tuple(map(candidate, result.others)),
            tuple(result.rejected.items()), tuple(result.claim_mismatches.items()), result.notes)


# --- M1: order-independent merge of one evidence key ---------------------------

def test_exact_and_loose_matches_of_one_span_give_one_result_in_either_order():
    snapshot, plan, coverage = _one()
    exact = ProviderObservation(0, "it depends on", 1, scores=EDGE)
    loose = ProviderObservation(0, "it-depends on", 1, scores=EDGE)

    forward = reduce_candidates([exact, loose], snapshot=snapshot, plan=plan, coverage=coverage)
    backward = reduce_candidates([loose, exact], snapshot=snapshot, plan=plan, coverage=coverage)

    assert _everything(forward) == _everything(backward)
    [candidate] = forward.recommended
    assert candidate.assessment.score == QUALITY_FLOOR  # 1.80, the verified exact match counts
    assert [(s.key, s.reliability) for s in candidate.sources] == [(("item", 1, 10, 23), 3)]


def test_a_loose_match_alone_stays_below_the_floor():
    snapshot, plan, coverage = _one()

    result = reduce_candidates([ProviderObservation(0, "it-depends on", 1, scores=EDGE)],
                               snapshot=snapshot, plan=plan, coverage=coverage)

    assert result.recommended == ()
    assert result.others[0].assessment.score < QUALITY_FLOOR
    assert result.others[0].status == "below_floor"


def test_every_order_of_four_observations_of_one_key_is_identical():
    snapshot, plan, coverage = _one()
    observations = [
        ProviderObservation(0, "it depends on", 1, scores={**EDGE, "transfer": 3}),
        ProviderObservation(0, "it-depends on", 1, scores=EDGE),
        ProviderObservation(0, "IT DEPENDS ON", 1, scores={**EDGE, "reusability": 3}),
        ProviderObservation(0, "it, depends on", 1, scores={**EDGE, "naturalness": 1}),
    ]

    results = {_everything(reduce_candidates(order, snapshot=snapshot, plan=plan, coverage=coverage))
               for order in itertools.permutations(observations)}

    assert len(results) == 1
    [only] = results
    assert only[1] == ()  # a conflicting naturalness=1 is preserved by the source minimum
    assert only[2][0][-1][0][1] == 3  # exact evidence is still retained
    assert only[2][0][4] < QUALITY_FLOOR


def test_full_permutation_compares_scores_selection_and_provenance_near_the_floor():
    text = "Honestly, it depends on the weather, but to be honest we go anyway."
    snapshot, plan, coverage = _one(text)
    observations = [
        ProviderObservation(0, "it depends on", 1, scores=EDGE),
        ProviderObservation(0, "it-depends on", 1, scores={**EDGE, "reusability": 1}),
        ProviderObservation(0, "to be honest", 1, scores=EDGE),
        ProviderObservation(0, "to-be honest", 1, scores=EDGE),
        ProviderObservation(0, "not in the text", 1, scores=EDGE),
    ]

    results = {_everything(reduce_candidates(order, snapshot=snapshot, plan=plan, coverage=coverage))
               for order in itertools.permutations(observations)}

    assert len(results) == 1
    status, recommended, others, rejected, *_ = next(iter(results))
    assert {c[0] for c in recommended} == {"to be honest"}
    assert {c[0] for c in others} == {"it depends on"}  # the lower median of 1 and 2 is 1
    assert rejected == (("ungrounded", 1),)


def test_repeating_one_source_cannot_outvote_another_source():
    text_a, text_b = "Honestly, it depends on the weather.", "Well, it depends on the guest."
    snapshot = LibrarySnapshot("o", "item", "lesson", "en",
                               (SnapshotSentence(1, text_a, 1.0, 4.0), SnapshotSentence(2, text_b, 5.0, 8.0)))
    plan = build_windows(snapshot.sentences, language="en", budget_tokens=200)
    coverage = build_manifest(plan, {0})
    low = {**STUB_SCORES["it depends on"], "reusability": 1}
    once = [ProviderObservation(0, "it depends on", 1, scores=STUB_SCORES["it depends on"]),
            ProviderObservation(0, "it depends on", 2, scores=low)]
    stuffed = once[:1] * 5 + once[1:]

    a = reduce_candidates(once, snapshot=snapshot, plan=plan, coverage=coverage)
    b = reduce_candidates(stuffed, snapshot=snapshot, plan=plan, coverage=coverage)

    score = lambda r: (r.recommended + r.others)[0].assessment
    assert score(a).score == score(b).score
    assert score(a).dimensions["reusability"] == 1  # lower median of {3, 1} per distinct source


# --- M2: grounding inside the citable character ranges ---------------------------

SPLIT = "We look forward to it, then we talk a lot about the weather, and finally we look forward to it again."


def _split():
    snapshot = LibrarySnapshot("o", "item", "lesson", "en", (SnapshotSentence(1, SPLIT, 0.0, 9.0),))
    plan = build_windows(snapshot.sentences, language="en", budget_tokens=12)
    return snapshot, plan


def test_the_split_used_by_these_tests():
    _, plan = _split()
    assert [[(s.part, s.char_start, s.char_end) for s in w.citable] for w in plan.windows] == [
        [(0, 0, 22)], [(1, 23, 60)], [(2, 61, 94)], [(3, 95, 101)]]


def test_a_span_outside_the_windows_characters_is_rejected():
    snapshot, plan = _split()

    for window in (1, 3):  # the phrase exists in the sentence, but not in these parts
        result = verify_observation(ProviderObservation(window, "look forward to", 1), snapshot, plan)
        assert result.reason == "outside_citable_range"


def test_each_window_grounds_its_own_occurrence_of_a_repeated_phrase():
    snapshot, plan = _split()

    first = verify_observation(ProviderObservation(0, "look forward to", 1), snapshot, plan).evidence
    second = verify_observation(ProviderObservation(2, "look forward to", 1), snapshot, plan).evidence

    assert (first.evidence_start, first.evidence_end) == (3, 18)
    assert (second.evidence_start, second.evidence_end) == (76, 91)
    assert 0 <= first.evidence_start and first.evidence_end <= 22
    assert 61 <= second.evidence_start and second.evidence_end <= 94


def test_both_occurrences_merge_as_two_sources_of_one_candidate():
    snapshot, plan = _split()
    coverage = build_manifest(plan, {w.index for w in plan.windows})
    observations = [ProviderObservation(w, "look forward to", 1, scores=STUB_SCORES["look forward to"])
                    for w in (0, 2)]

    [candidate] = reduce_candidates(observations, snapshot=snapshot, plan=plan, coverage=coverage).recommended

    assert {s.evidence_start for s in candidate.sources} == {3, 76}


def test_a_phrase_split_across_two_windows_is_rejected_in_both():
    text = "Honestly it depends, on the guest and the topic today."
    snapshot = LibrarySnapshot("o", "item", "lesson", "en", (SnapshotSentence(1, text),))
    plan = build_windows(snapshot.sentences, language="en", budget_tokens=9)

    assert [[(s.char_start, s.char_end) for s in w.citable] for w in plan.windows] == [[(0, 20)], [(21, 47)], [(48, 54)]]
    for window in (0, 1):
        assert verify_observation(ProviderObservation(window, "it depends on", 1), snapshot, plan).reason == \
            "outside_citable_range"


def _hand_plan(text, citable, context=()):
    """A one-window plan with explicit parts of sentence 1 (part, start, end)."""
    parts = len(citable) + len(context)
    seg = lambda p, a, b: Segment(1, p, parts, a, b, text[a:b], estimate_tokens(text[a:b], "en"))
    window = Window(0, tuple(seg(*v) for v in citable), tuple(seg(*v) for v in context))
    return WindowPlan("en", 10_000, (1,), (window,), ())


def test_a_phrase_only_in_a_context_part_of_the_same_sentence_is_rejected():
    text = "We look forward to it, then we talk a lot about the weather."
    snapshot = LibrarySnapshot("o", "item", "lesson", "en", (SnapshotSentence(1, text),))
    plan = _hand_plan(text, citable=[(1, 23, 60)], context=[(0, 0, 22)])

    assert verify_observation(ProviderObservation(0, "look forward to", 1), snapshot, plan).reason == \
        "outside_citable_range"
    assert verify_observation(ProviderObservation(0, "the weather", 1), snapshot, plan).evidence.evidence_start == 48


def test_consecutive_parts_in_one_window_are_joined_but_gaps_are_not():
    text = "Honestly it depends, on the guest and the topic today."
    snapshot = LibrarySnapshot("o", "item", "lesson", "en", (SnapshotSentence(1, text),))
    joined = _hand_plan(text, citable=[(0, 0, 20), (1, 21, 47)])
    gapped = _hand_plan(text, citable=[(0, 0, 20), (2, 21, 47)])

    assert verify_observation(ProviderObservation(0, "it depends on", 1), snapshot, joined).evidence.evidence_start == 9
    assert verify_observation(ProviderObservation(0, "it depends on", 1), snapshot, gapped).reason == \
        "outside_citable_range"


def test_one_legal_and_one_illegal_fragment_together():
    snapshot, plan = _split()
    coverage = build_manifest(plan, {w.index for w in plan.windows})
    observations = [ProviderObservation(0, "look forward to", 1, scores=STUB_SCORES["look forward to"]),
                    ProviderObservation(1, "look forward to", 1, scores=STUB_SCORES["look forward to"])]

    result = reduce_candidates(observations, snapshot=snapshot, plan=plan, coverage=coverage)

    assert result.rejected == {"outside_citable_range": 1}
    [candidate] = result.recommended
    assert [s.evidence_start for s in candidate.sources] == [3]


def test_a_split_sentence_is_covered_once_and_only_when_every_part_is_done():
    snapshot, plan = _split()
    everything = {w.index for w in plan.windows}

    complete = build_manifest(plan, everything)
    missing_part = build_manifest(plan, everything - {2}, {2: "provider_timeout"})

    assert (complete.covered_sentences, complete.status) == (1, "complete")
    assert (missing_part.covered_sentences, missing_part.status) == (0, "partial")


def test_the_snapshot_is_never_modified_by_verification():
    snapshot, plan = _split()
    before = snapshot.transcript_revision

    verify_observation(ProviderObservation(1, "look forward to", 1, claimed_quote="forged"), snapshot, plan)

    assert snapshot.transcript_revision == before and snapshot.sentences[0].text == SPLIT


# --- M3: malformed observations are rejected one by one ---------------------------

@pytest.mark.parametrize("variations", [None, "a string", 42, {"text": "x"}, ("ok", 7), ("ok", None),
                                        ("x" * 201,), ("one", "two", "three"), ("ok", "   ")])
def test_malformed_variations_reject_only_that_observation(variations):
    snapshot, plan, coverage = _one()
    bad = ProviderObservation(0, "it depends on", 1, scores=STUB_SCORES["it depends on"], variations=variations)
    good = ProviderObservation(0, "the weather", 1, scores=STUB_SCORES["it depends on"], variations=["Nice weather."])

    result = reduce_candidates([bad, good], snapshot=snapshot, plan=plan, coverage=coverage)

    assert result.rejected == {"invalid_variations": 1}
    assert [c.identity for c in result.recommended] == ["the weather"]
    assert result.recommended[0].variations == ("Nice weather.",)


@pytest.mark.parametrize("field,value,reason", [
    ("text", None, "invalid_text"), ("text", 12, "invalid_text"),
    ("window_index", "0", "invalid_window_index"), ("window_index", True, "invalid_window_index"),
    ("evidence_sentence_id", "1", "invalid_sentence_id"), ("evidence_sentence_id", 1.0, "invalid_sentence_id"),
    ("claimed_origin", None, "invalid_origin"),
    ("scores", None, "invalid_scores"), ("scores", [("reusability", 3)], "invalid_scores"),
    ("scores", {1: 3}, "invalid_scores"),
    ("claimed_quote", 5, "invalid_claims"), ("claimed_start", "1.0", "invalid_claims"),
    ("claimed_end", True, "invalid_claims"),
])
def test_other_malformed_fields_are_rejected_not_coerced(field, value, reason):
    snapshot, plan, coverage = _one()
    fields = dict(window_index=0, text="it depends on", evidence_sentence_id=1,
                  scores=STUB_SCORES["it depends on"])
    fields[field] = value
    good = ProviderObservation(0, "the weather", 1, scores=STUB_SCORES["it depends on"])

    result = reduce_candidates([ProviderObservation(**fields), good], snapshot=snapshot, plan=plan, coverage=coverage)

    assert result.rejected == {reason: 1}
    assert [c.identity for c in result.recommended] == ["the weather"]


def test_one_malformed_among_many_valid_observations():
    snapshot, plan, coverage = _one("Honestly, it depends on the weather, but to be honest we go anyway.")
    valid = [ProviderObservation(0, text, 1, scores=STUB_SCORES["it depends on"])
             for text in ("it depends on", "to be honest", "the weather", "we go anyway")]
    bad = ProviderObservation(0, "it depends on", 1, scores=STUB_SCORES["it depends on"], variations=None)

    with_bad = reduce_candidates([*valid[:2], bad, *valid[2:]], snapshot=snapshot, plan=plan, coverage=coverage)
    without = reduce_candidates(valid, snapshot=snapshot, plan=plan, coverage=coverage)

    assert with_bad.rejected == {"invalid_variations": 1}
    assert _everything(with_bad)[1:3] == _everything(without)[1:3]


def test_an_unexpected_verification_failure_rejects_only_that_observation(monkeypatch):
    snapshot, plan, coverage = _one("Honestly, it depends on the weather, but to be honest we go anyway.")
    real = provenance.verify_observation

    def flaky(observation, *args):
        if observation.text == "to be honest":
            raise KeyError("unexpected internal failure")
        return real(observation, *args)

    monkeypatch.setattr(reduce_module, "verify_observation", flaky)
    result = reduce_candidates([ProviderObservation(0, t, 1, scores=STUB_SCORES["it depends on"])
                                for t in ("it depends on", "to be honest")],
                               snapshot=snapshot, plan=plan, coverage=coverage)

    assert result.rejected == {"verification_error": 1}
    assert [c.identity for c in result.recommended] == ["it depends on"]
