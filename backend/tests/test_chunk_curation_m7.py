"""M7: full ReduceResult invariance for identical subtitle occurrences."""
from __future__ import annotations

import itertools

import pytest

from chunk_curation_lab.coverage import build_manifest
from chunk_curation_lab.provenance import ProviderObservation
from chunk_curation_lab.reduce import _rank_key, reduce_candidates
from chunk_curation_lab.scoring import SEMANTIC
from chunk_curation_lab.snapshot import LibrarySnapshot, SnapshotSentence
from chunk_curation_lab.windowing import build_windows

TEXT = "Honestly, it depends on the weather; to be honest, we may stay."
SCORES = {name: 3 for name in SEMANTIC}


def _inputs(first=(10.0, 12.0), second=(40.0, 42.0)):
    # Identical text/fingerprint/spans, distinct source ids and timings.
    # Transcript order intentionally differs from the identity tie-break.
    snapshot = LibrarySnapshot("synthetic-owner", "m7-item", "m7-lesson", "en", (
        SnapshotSentence(20, TEXT, *first),
        SnapshotSentence(7, TEXT, *second),
    ))
    plan = build_windows(snapshot.sentences, language="en", budget_tokens=200)
    assert len(plan.windows) == 1
    return snapshot, plan, build_manifest(plan, {0})


def _reduce(observations, inputs):
    snapshot, plan, coverage = inputs
    return reduce_candidates(observations, snapshot=snapshot, plan=plan, coverage=coverage)


@pytest.mark.parametrize("first,second,expected", [
    ((10.0, 12.0), (40.0, 42.0), [7, 20]),
    ((None, None), (None, None), [7, 20]),
    ((10.0, 12.0), (None, None), [20, 7]),  # existing timed-source preference wins
])
def test_identical_text_sources_have_stable_identity_and_primary(first, second, expected):
    inputs = _inputs(first, second)
    observations = [ProviderObservation(0, "it depends on", sid, scores=SCORES) for sid in (20, 7)]
    reference = _reduce(observations, inputs)

    # Compare the complete dataclass, including coverage, all evidence fields,
    # assessments, counters, notes and recommended/other candidate sequences.
    for order in itertools.permutations(observations):
        assert _reduce(order, inputs) == reference
    [candidate] = reference.recommended
    assert [source.sentence_id for source in candidate.sources] == expected
    assert candidate.sources[0].sentence_id == expected[0]
    for source in candidate.sources:
        sentence = inputs[0].sentence(source.sentence_id)
        assert (source.start, source.end, source.sentence_text) == (
            sentence.start, sentence.end, sentence.text)
    assert candidate.assessment.score == 3
    assert candidate.assessment.weight_coverage == 1
    assert candidate.assessment.min_core == 3
    # Identical text remains one corroborating fingerprint, not two votes in
    # the rank key; the change only resolves provenance presentation ties.
    assert _rank_key(candidate) == (-3, -3, -1, -1)
    assert len(candidate.sources) == 2 and candidate.observations == 2


def test_every_permutation_compares_the_entire_reduce_result():
    inputs = _inputs()
    observations = [
        ProviderObservation(0, phrase, sid, scores=SCORES,
                            claimed_start=999.0 if (phrase, sid) == ("it depends on", 20) else None,
                            variations=("It depends on you.",) if phrase == "it depends on" else ())
        for phrase in ("it depends on", "to be honest") for sid in (20, 7)
    ]
    observations.extend([
        ProviderObservation(0, "not in this transcript", 20),
        ProviderObservation(0, "it depends on", 7, claimed_origin="ai_variation"),
    ])
    reference = _reduce(observations, inputs)

    for order in itertools.permutations(observations):  # all 720 orders
        assert _reduce(order, inputs) == reference
    assert {candidate.identity for candidate in reference.recommended} == {"it depends on", "to be honest"}
    assert reference.others == ()
    assert reference.rejected == {"ungrounded": 1, "variation_not_source": 1}
    assert reference.claim_mismatches == {"start": 1}
    for candidate in reference.recommended:
        assert [source.sentence_id for source in candidate.sources] == [7, 20]
        assert candidate.assessment.score == 3
        assert candidate.tied_with
        assert _rank_key(candidate) == (-3, -3, -1, -1)
