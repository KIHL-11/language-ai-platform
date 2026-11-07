"""F3 pure budget projection only; no provider or job integration."""
from dataclasses import replace
import itertools

import pytest

from chunk_curation_lab.billing import Attempt, BudgetTotals, OrphanReport, budget_totals


def _attempt(**fields):
    return replace(Attempt("owner", "attempt-1", "job", 0, 100, "uncertain", "synthetic-request"), **fields)


def test_uncertain_and_replayed_orphan_reports_spend_one_ceiling():
    attempt = _attempt()
    orphan = OrphanReport("owner", "attempt-1")
    assert budget_totals([attempt], [orphan] * 100) == BudgetTotals(100, 0, 100)
    assert budget_totals([attempt] * 100, [orphan] * 100) == BudgetTotals(100, 0, 100)


def test_late_usage_replaces_uncertain_ceiling_once_and_replays_are_free():
    attempt = _attempt()
    report = OrphanReport("owner", "attempt-1", 60, "verified-receipt")
    for reports in itertools.permutations([report, report, OrphanReport("owner", "attempt-1")]):
        assert budget_totals([attempt], reports) == BudgetTotals(60, 0, 0)
    settled = replace(attempt, state="succeeded", actual_cost=60, usage_evidence="verified-receipt")
    assert budget_totals([settled], [report] * 10) == BudgetTotals(60, 0, 0)


def test_same_provider_key_does_not_make_distinct_physical_attempts_free():
    old = _attempt(provider_idempotency_key="same-key")
    retry = _attempt(attempt_id="attempt-2", state="succeeded", actual_cost=80,
                     usage_evidence="retry-receipt", provider_idempotency_key="same-key")
    assert budget_totals([old, retry]) == BudgetTotals(180, 0, 100)
    assert not budget_totals([old, retry]).can_reserve(200, 21)


def test_holds_and_spend_both_reduce_available_budget():
    uncertain = _attempt()
    held = _attempt(attempt_id="attempt-2", state="in_flight", reserved_cost=40)
    totals = budget_totals([uncertain, held])
    assert totals == BudgetTotals(100, 40, 100)
    assert totals.can_reserve(150, 10)
    assert not totals.can_reserve(150, 11)
    report = OrphanReport("owner", "attempt-2")
    assert budget_totals([uncertain, held], [report]) == BudgetTotals(140, 0, 140)


@pytest.mark.parametrize("state", ["succeeded", "failed_billed"])
def test_known_actual_overruns_are_recorded_even_above_the_ceiling(state):
    attempt = _attempt(state=state, actual_cost=120, usage_evidence="overrun")
    assert budget_totals([attempt]) == BudgetTotals(120, 0, 0)


@pytest.mark.parametrize("state", ["released", "failed_definitive"])
def test_unbilled_terminal_attempt_cannot_silently_swallow_an_orphan(state):
    attempt = _attempt(state=state)
    assert budget_totals([attempt]) == BudgetTotals(0, 0, 0)
    with pytest.raises(ValueError, match="contradicts"):
        budget_totals([attempt], [OrphanReport("owner", "attempt-1")])


def test_unknown_and_cross_owner_orphan_reports_fail_closed():
    with pytest.raises(ValueError, match="pre-reserved"):
        budget_totals([_attempt()], [OrphanReport("other-owner", "attempt-1")])
    # Equal ids in separate owner namespaces are distinct charges.
    assert budget_totals([_attempt(), _attempt(owner="other-owner")]).spent == 200


@pytest.mark.parametrize("change", [{"state": "reserved"}, {"job_id": "different"},
                                  {"window_index": 1}, {"reserved_cost": 200},
                                  {"request_fingerprint": "different-request"}])
def test_conflicting_canonical_snapshots_cannot_be_chosen_by_arrival_order(change):
    attempt = _attempt()
    for rows in ([attempt, replace(attempt, **change)], [replace(attempt, **change), attempt]):
        with pytest.raises(ValueError, match="conflicting canonical"):
            budget_totals(rows)


def test_conflicting_or_unsupported_usage_cannot_refund_budget():
    attempt = _attempt()
    with pytest.raises(ValueError, match="requires usage evidence"):
        budget_totals([attempt], [OrphanReport("owner", "attempt-1", 0)])
    with pytest.raises(ValueError, match="conflicting usage"):
        budget_totals([attempt], [OrphanReport("owner", "attempt-1", 40, "one"),
                                 OrphanReport("owner", "attempt-1", 60, "two")])


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_invalid_cost_units_are_rejected(value):
    with pytest.raises(ValueError):
        budget_totals([_attempt(reserved_cost=value)])


def test_provider_billing_dedup_needs_evidenced_allocation_not_just_a_shared_key():
    # Only a documented billing receipt/replay proof may allocate zero cost
    # to the second physical send; the shared provider key alone cannot.
    billed = _attempt(state="succeeded", actual_cost=60, usage_evidence="verified-charge",
                      provider_idempotency_key="same-key")
    replay = _attempt(attempt_id="attempt-2", state="succeeded", actual_cost=0,
                      usage_evidence="verified-zero-cost-replay-of-charge",
                      provider_idempotency_key="same-key")
    assert budget_totals([billed, replay]) == BudgetTotals(60, 0, 0)
