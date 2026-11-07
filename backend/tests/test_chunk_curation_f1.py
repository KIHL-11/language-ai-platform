"""F1: repeated score reports for one source cannot manufacture quality."""
from __future__ import annotations

from dataclasses import replace
import itertools

import pytest

from chunk_curation_lab.coverage import build_manifest
from chunk_curation_lab.provenance import ProviderObservation
from chunk_curation_lab.reduce import reduce_candidates
from chunk_curation_lab.snapshot import LibrarySnapshot, SnapshotSentence
from chunk_curation_lab.windowing import build_windows

EDGE = {"reusability": 2, "naturalness": 2, "context_completeness": 2,
        "independence": 2, "level_fit": 0, "transfer": 1}


def _reduce(observations):
    snapshot = LibrarySnapshot("synthetic", "item", "lesson", "en", (
        SnapshotSentence(1, "Honestly, it depends on the weather."),
        SnapshotSentence(2, "Well, it depends on the guest."),
    ))
    plan = build_windows(snapshot.sentences, language="en", budget_tokens=200)
    return reduce_candidates(observations, snapshot=snapshot, plan=plan,
                             coverage=build_manifest(plan, {0}))


def _quality(result):
    return tuple((c.identity, c.status, c.assessment, c.sources)
                 for c in result.recommended + result.others)


@pytest.mark.parametrize("copies", [1, 2, 10, 100])
def test_duplicate_high_score_cannot_lift_one_source_above_the_floor(copies):
    low = ProviderObservation(0, "it depends on", 1, scores={**EDGE, "reusability": 0})
    high = ProviderObservation(0, "it depends on", 1, scores=EDGE)
    baseline = _reduce([low, high])
    stuffed = _reduce([low, *[high] * copies])

    assert baseline.recommended == stuffed.recommended == ()
    assert _quality(stuffed) == _quality(baseline)
    assert stuffed.others[0].assessment.dimensions["reusability"] == 0


def test_conflicting_source_scores_cannot_be_stuffed_past_another_source():
    low = ProviderObservation(0, "it depends on", 1,
                              scores={name: 0 for name in EDGE})
    high = ProviderObservation(0, "it depends on", 1,
                               scores={name: 3 for name in EDGE})
    other_source = replace(high, evidence_sentence_id=2)
    baseline = _reduce([low, high, other_source])

    for copies in (2, 10, 100):
        result = _reduce([low, *[high] * copies, other_source])
        assert _quality(result) == _quality(baseline)
        assert result.others[0].status == "gated"


def test_partial_score_maps_and_duplicate_order_do_not_change_quality():
    low = ProviderObservation(0, "it depends on", 1,
                              scores={**EDGE, "naturalness": 0})
    high = replace(low, scores=EDGE)
    partial = replace(low, scores={"naturalness": 3, "level_fit": None})
    reference = _quality(_reduce([low, high, partial]))
    for order in itertools.permutations([low, high, partial, high]):
        assert _quality(_reduce(order)) == reference
    assert _reduce([low, high, partial]).others[0].status == "gated"
