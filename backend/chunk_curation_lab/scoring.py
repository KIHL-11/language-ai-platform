"""Candidate quality model (FULL-TRANSCRIPT-RANKING-SPEC.md §3).

Eight dimensions, each an integer 0-3. Six are semantic and must come from a
scorer (a model, or a test stub supplying explicit scores); two are computed
by the server. Position is not a dimension and is never an input.

Aggregation across duplicate observations uses the lower median per
dimension (conservative, order-independent). All arithmetic is exact
(Fraction), so the result cannot depend on summation order.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Mapping

SEMANTIC = (
    "reusability",           # used in everyday speech across many situations
    "naturalness",           # idiomatic, grammatical, sounds native
    "context_completeness",  # meaning is clear from the chunk + its sentence
    "independence",          # works without proper nouns or this video's topic
    "level_fit",             # matches the lesson level (B1/B2/C1)
    "transfer",              # adaptable to new speaking scenarios
)
SERVER = ("novelty", "evidence_reliability")
DIMENSIONS = SEMANTIC + SERVER

# Integer weights summing to 100; rationale in the spec.
WEIGHTS: Mapping[str, int] = {
    "reusability": 22,
    "naturalness": 18,
    "transfer": 14,
    "context_completeness": 12,
    "independence": 10,
    "level_fit": 10,
    "novelty": 7,
    "evidence_reliability": 7,
}
CORE = ("reusability", "naturalness")  # without these a candidate is unscored
MIN_WEIGHT_COVERAGE = Fraction(80, 100)
QUALITY_FLOOR = Fraction(18, 10)        # on the 0-3 scale
GATES = ("naturalness", "independence")  # a 0 here excludes the candidate

assert sum(WEIGHTS.values()) == 100 and set(WEIGHTS) == set(DIMENSIONS)


def lower_median(values: Iterable[int]) -> int:
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def merge_scores(observed: Iterable[Mapping[str, int | None]]) -> dict[str, int | None]:
    """Lower median per semantic dimension, ignoring missing values."""
    observed = list(observed)
    merged: dict[str, int | None] = {}
    for name in SEMANTIC:
        present = [scores[name] for scores in observed if scores.get(name) is not None]
        merged[name] = lower_median(present) if present else None
    return merged


def novelty(identity: str, saved: Mapping[str, bool]) -> int:
    """3 not saved, 1 saved but archived, 0 saved and active."""
    if identity not in saved:
        return 3
    return 0 if saved[identity] else 1


@dataclass(frozen=True)
class Assessment:
    status: str  # "scored" | "unscored" | "gated"
    score: Fraction | None
    dimensions: Mapping[str, int | None]
    weight_coverage: Fraction
    min_core: int | None
    reasons: tuple[str, ...]


def assess(
    observed: Iterable[Mapping[str, int | None]], *, novelty_value: int, reliability: int
) -> Assessment:
    observed = list(observed)
    for scores in observed:
        unknown = set(scores) - set(SEMANTIC)
        if unknown:
            raise ValueError(f"unknown score dimensions: {sorted(unknown)}")
        for value in scores.values():
            if value is not None and (isinstance(value, bool) or value not in (0, 1, 2, 3)):
                raise ValueError("scores must be integers 0-3 or None")
    dimensions: dict[str, int | None] = {}
    for name in SEMANTIC:
        present = [scores[name] for scores in observed if scores.get(name) is not None]
        dimensions[name] = lower_median(present) if present else None
    dimensions["novelty"] = novelty_value
    dimensions["evidence_reliability"] = reliability

    present = {name: value for name, value in dimensions.items() if value is not None}
    coverage = Fraction(sum(WEIGHTS[name] for name in present), 100)
    reasons = []
    if any(dimensions[name] is None for name in CORE):
        reasons.append("core_dimension_missing")
    if coverage < MIN_WEIGHT_COVERAGE:
        reasons.append("insufficient_weight_coverage")
    if reasons:
        return Assessment("unscored", None, dimensions, coverage, None, tuple(reasons))
    gated = tuple(f"gate_{name}" for name in GATES if dimensions[name] == 0)
    score = Fraction(sum(WEIGHTS[name] * value for name, value in present.items()),
                     sum(WEIGHTS[name] for name in present))
    min_core = min(dimensions[name] for name in CORE)
    if gated:
        return Assessment("gated", score, dimensions, coverage, min_core, gated)
    return Assessment("scored", score, dimensions, coverage, min_core, ())
