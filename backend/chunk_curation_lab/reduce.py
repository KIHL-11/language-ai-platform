"""Global reduce: verify, de-duplicate, aggregate, rank and select.

Order independence is a design property, not an accident:
- observations are grouped by identity (a dict of sets/lists that are only
  read through sorted() or order-free aggregates);
- each source uses minima, then sources use a lower median; scores are exact Fractions;
- sources are de-duplicated by key and ordered by evidence quality, then by
  sentence fingerprint, never by transcript position or arrival order;
- ranking keys are quality only; exact ties form a group that is selected or
  excluded as a whole, so neither the alphabet nor arrival order decides
  inclusion. Inside a selected tie group the display order is by identity and
  is marked as a tie.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Iterable, Mapping

from chunk_curation_lab.coverage import CoverageManifest
from chunk_curation_lab.provenance import (
    ProviderObservation,
    RejectedObservation,
    VerifiedEvidence,
    VerifiedObservation,
    verify_observation,
)
from chunk_curation_lab.scoring import QUALITY_FLOOR, Assessment, assess, merge_scores, novelty
from chunk_curation_lab.snapshot import LibrarySnapshot
from chunk_curation_lab.windowing import WindowPlan

TARGET_MIN = 8
TARGET_MAX = 12


@dataclass(frozen=True)
class CuratedCandidate:
    identity: str
    display_text: str
    status: str  # recommended | below_floor | not_selected_tie | not_selected_limit | unscored | gated
    assessment: Assessment
    observations: int
    sources: tuple[VerifiedEvidence, ...]
    variations: tuple[str, ...]  # origin = ai_variation; never evidence
    tied_with: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReduceResult:
    status: str  # ready | partial | unscored | empty
    recommended: tuple[CuratedCandidate, ...]
    others: tuple[CuratedCandidate, ...]
    rejected: Mapping[str, int]
    claim_mismatches: Mapping[str, int]
    notes: tuple[str, ...]
    coverage: CoverageManifest


def _source_order(evidence: VerifiedEvidence):
    # Evidence quality first; the fingerprint only makes the order total.
    return (-evidence.reliability, evidence.start is None, evidence.sentence_fingerprint,
            evidence.evidence_start, evidence.evidence_end)


def _rank_key(candidate: CuratedCandidate):
    a = candidate.assessment
    distinct_sentences = len({source.sentence_fingerprint for source in candidate.sources})
    return (-a.score, -a.min_core, -a.weight_coverage, -distinct_sentences)


def reduce_candidates(
    observations: Iterable[ProviderObservation],
    *,
    snapshot: LibrarySnapshot,
    plan: WindowPlan,
    coverage: CoverageManifest,
    saved: Mapping[str, bool] | None = None,
) -> ReduceResult:
    saved = saved or {}
    rejected: Counter[str] = Counter()
    mismatches: Counter[str] = Counter()
    groups: dict[str, list[VerifiedObservation]] = {}
    for observation in observations:
        try:
            result = verify_observation(observation, snapshot, plan)
        except Exception:  # noqa: BLE001 - per-observation boundary (Round 3, M3)
            # An unexpected failure on one untrusted observation rejects only
            # that observation; it never aborts the reduce or becomes valid.
            rejected["verification_error"] += 1
            continue
        if isinstance(result, RejectedObservation):
            rejected[result.reason] += 1
            continue
        mismatches.update(result.claim_mismatches)
        groups.setdefault(result.identity, []).append(result)

    candidates: list[CuratedCandidate] = []
    for identity, members in groups.items():
        by_key: dict[tuple, list[VerifiedObservation]] = {}
        for member in members:
            by_key.setdefault(member.evidence.key, []).append(member)
        ordered_sources = tuple(sorted((_merge_evidence(group) for group in by_key.values()), key=_source_order))
        primary = ordered_sources[0]
        assessment = assess(
            # One vote per distinct source. Conflicting reports use minima,
            # so duplicate high scores cannot change that source's vote (F1).
            (merge_scores(group.scores for group in key_members) for key_members in by_key.values()),
            novelty_value=novelty(identity, saved),
            reliability=max(source.reliability for source in ordered_sources),
        )
        candidates.append(CuratedCandidate(
            identity=identity,
            display_text=primary.sentence_text[primary.evidence_start:primary.evidence_end],
            status=assessment.status,
            assessment=assessment,
            observations=len(members),
            sources=ordered_sources,
            variations=tuple(sorted({v for member in members for v in member.variations})),
        ))

    scored = [c for c in candidates if c.status == "scored"]
    above = [c for c in scored if c.assessment.score >= QUALITY_FLOOR]
    below = [_with(c, "below_floor") for c in scored if c.assessment.score < QUALITY_FLOOR]

    tie_groups: dict[tuple, list[CuratedCandidate]] = {}
    for candidate in above:
        tie_groups.setdefault(_rank_key(candidate), []).append(candidate)
    recommended: list[CuratedCandidate] = []
    excluded: list[CuratedCandidate] = []
    notes: list[str] = []
    full = False
    for key in sorted(tie_groups):  # quality keys only
        group = sorted(tie_groups[key], key=lambda c: c.identity)  # display order of a tie
        if not full and len(recommended) + len(group) > TARGET_MAX:
            full = True
            if len(group) > 1 and len(recommended) < TARGET_MAX:
                # The tie straddles the limit: keeping some of it would let
                # the alphabet decide, so the whole group stays out.
                notes.append("tie_group_excluded_at_limit")
                excluded.extend(_with(c, "not_selected_tie") for c in group)
                continue
        if full:
            excluded.extend(_with(c, "not_selected_limit") for c in group)
            continue
        names = tuple(c.identity for c in group) if len(group) > 1 else ()
        recommended.extend(
            _with(c, "recommended", tuple(n for n in names if n != c.identity)) for c in group
        )

    if coverage.status == "empty":
        status = "empty"
    elif coverage.status != "complete":
        status = "partial"  # never ready while any content is unanalysed
    elif candidates and not scored and all(c.status == "unscored" for c in candidates):
        status = "unscored"  # no real quality signal: nothing is recommended
    else:
        status = "ready"
    if any(c.status == "unscored" for c in candidates):
        notes.append("unscored_candidates_present")
    if status in {"ready", "partial"} and len(recommended) < TARGET_MIN:
        notes.append("few_high_quality_candidates")
    others = sorted(
        excluded + below + [c for c in candidates if c.status in {"unscored", "gated"}],
        key=lambda c: c.identity,
    )
    return ReduceResult(
        status=status,
        recommended=tuple(recommended),
        others=tuple(others),
        rejected=dict(sorted(rejected.items())),
        claim_mismatches=dict(sorted(mismatches.items())),
        notes=tuple(sorted(set(notes))),
        coverage=coverage,
    )


def _merge_evidence(members: list[VerifiedObservation]) -> VerifiedEvidence:
    """All observations of one evidence key point at the same server-verified
    span, so every field but reliability is identical. Reliability is the
    best server-verified match for that span: it records that the server saw
    the provider text equal the source text (ignoring case); it is never a
    provider claim, and it never changes the span, the source or a semantic
    score."""
    first, *rest = (member.evidence for member in members)
    best = max(evidence.reliability for evidence in (first, *rest))
    if any(replace(evidence, reliability=best) != replace(first, reliability=best) for evidence in rest):
        raise AssertionError("evidence with one key must share every field but reliability")
    return replace(first, reliability=best)


def _with(candidate: CuratedCandidate, status: str, tied_with: tuple[str, ...] = ()) -> CuratedCandidate:
    return CuratedCandidate(
        identity=candidate.identity,
        display_text=candidate.display_text,
        status=status,
        assessment=candidate.assessment,
        observations=candidate.observations,
        sources=candidate.sources,
        variations=candidate.variations,
        tied_with=tied_with,
    )
