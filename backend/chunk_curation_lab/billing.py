"""Pure attempt accounting experiment; no worker, network, database or billing.

An orphan report is an observation of an existing physical call, not a new
charge. All budget projections join on (owner, attempt_id). Distinct retry
calls keep distinct ids even if they use one provider idempotency key.
Costs are non-negative integer units in one configured currency/price scale.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

AttemptState = Literal[
    "reserved", "in_flight", "uncertain", "succeeded",
    "failed_billed", "failed_definitive", "released",
]
_STATES = {"reserved", "in_flight", "uncertain", "succeeded",
           "failed_billed", "failed_definitive", "released"}


@dataclass(frozen=True)
class Attempt:
    owner: str
    attempt_id: str
    job_id: str
    window_index: int
    reserved_cost: int
    state: AttemptState
    request_fingerprint: str
    actual_cost: int | None = None
    usage_evidence: str | None = None
    provider_idempotency_key: str | None = None


@dataclass(frozen=True)
class OrphanReport:
    owner: str
    attempt_id: str
    # Only authenticated provider usage/billing evidence may set these.
    actual_cost: int | None = None
    usage_evidence: str | None = None


@dataclass(frozen=True)
class BudgetTotals:
    spent: int
    held: int
    uncertain_spend: int

    def can_reserve(self, approved_budget: int, new_cost: int) -> bool:
        _cost(approved_budget)
        _cost(new_cost)
        return self.spent + self.held + new_cost <= approved_budget


def _cost(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("cost must be a non-negative integer")


def _receipt(cost: int | None, evidence: str | None) -> None:
    if (cost is None) != (evidence is None):
        raise ValueError("actual cost requires usage evidence, and vice versa")
    if cost is not None:
        _cost(cost)
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("usage evidence must be non-empty")


def budget_totals(
    attempts: Iterable[Attempt], orphans: Iterable[OrphanReport] = (),
) -> BudgetTotals:
    """Project canonical attempt snapshots, never sum the attempt event log.

    Identical rows/reports may be replayed. Conflicting snapshots or receipts
    fail closed instead of selecting an arrival-order winner or refunding.
    A production implementation must enforce the same identity/constraints
    transactionally; the evidence strings here are synthetic test inputs.
    """
    canonical: dict[tuple[str, str], Attempt] = {}
    for attempt in attempts:
        if not all(isinstance(v, str) and v.strip()
                   for v in (attempt.owner, attempt.attempt_id, attempt.job_id, attempt.request_fingerprint)):
            raise ValueError("attempt identity must be non-empty")
        if type(attempt.window_index) is not int or attempt.window_index < 0:
            raise ValueError("invalid window index")
        if attempt.state not in _STATES:
            raise ValueError("invalid attempt state")
        _cost(attempt.reserved_cost)
        _receipt(attempt.actual_cost, attempt.usage_evidence)
        if attempt.state in {"succeeded", "failed_billed"}:
            if attempt.actual_cost is None:
                raise ValueError("billed attempt requires usage evidence")
        elif attempt.actual_cost is not None:
            raise ValueError("only billed terminal snapshots may carry actual cost")
        key = (attempt.owner, attempt.attempt_id)
        if key in canonical and canonical[key] != attempt:
            raise ValueError("conflicting canonical attempt snapshots")
        canonical[key] = attempt

    reports: dict[tuple[str, str], list[OrphanReport]] = {}
    for report in orphans:
        key = (report.owner, report.attempt_id)
        if key not in canonical:
            raise ValueError("orphan report requires its pre-reserved attempt")
        _receipt(report.actual_cost, report.usage_evidence)
        reports.setdefault(key, []).append(report)

    spent = held = uncertain_spend = 0
    for key, attempt in canonical.items():
        orphan_reports = reports.get(key, [])
        costs = {r.actual_cost for r in orphan_reports if r.actual_cost is not None}
        if attempt.actual_cost is not None:
            costs.add(attempt.actual_cost)
        if len(costs) > 1:
            raise ValueError("conflicting usage evidence requires reconciliation")
        if attempt.state in {"released", "failed_definitive"}:
            if orphan_reports:
                raise ValueError("orphan contradicts an unbilled terminal attempt")
            continue
        if costs:
            # A verified receipt replaces the uncertain ceiling for this id;
            # it is not added to it. Actual overruns must never disappear.
            spent += next(iter(costs))
        elif attempt.state == "uncertain" or orphan_reports:
            spent += attempt.reserved_cost
            uncertain_spend += attempt.reserved_cost
        else:  # reserved / in_flight hold budget until settled
            held += attempt.reserved_cost
    return BudgetTotals(spent, held, uncertain_spend)
