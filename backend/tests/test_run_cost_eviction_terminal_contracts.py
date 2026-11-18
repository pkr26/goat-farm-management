"""Eviction reports progress only when a real charged principal can be retired."""

import pytest

from app.api._run_limits import _RunCostWindow

type Principal = tuple[str, int]
type History = tuple[
    dict[Principal, tuple[tuple[float, int], ...]],
    dict[Principal, int],
    tuple[Principal, ...],
    tuple[Principal, ...],
]


def _history(window: _RunCostWindow) -> History:
    return (
        {principal: tuple(charges) for principal, charges in window._spend.items()},
        dict(window._totals),
        tuple(window._cold),
        tuple(window._protected),
    )


@pytest.mark.parametrize("previously_charged", [False, True], ids=["new", "cleared"])
def test_eviction_of_an_empty_window_reports_no_progress(
    previously_charged: bool,
) -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=4, clock=lambda: 1000.0)
    if previously_charged:
        window.charge_many((("user", 11, 10), ("farm", 21, 50)))
        window.charge("user", 11, 10)
        window.clear()
    before = _history(window)

    assert window._evict_one(exclude=set()) is False, (
        "An empty ledger cannot report that it freed an admission slot"
    )

    assert _history(window) == before, "An unsuccessful eviction must leave the ledger unchanged"
    window.charge("user", 31, 100)
    assert window.is_over_budget("user", 31), "No-progress eviction must leave admission usable"


def test_excluding_all_charged_principals_preserves_their_live_history() -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=4, clock=lambda: 1000.0)
    window.charge_many((("user", 11, 10), ("farm", 21, 50)))
    window.charge("user", 11, 10)
    window.charge_many((("user", 31, 1), ("farm", 41, 2)))
    protected_request = {("user", 11), ("farm", 21), ("user", 31), ("farm", 41)}
    before = _history(window)

    assert window._evict_one(exclude=protected_request) is False, (
        "A ledger containing only excluded principals cannot report a freed slot"
    )

    assert _history(window) == before, "Excluded live spend, history and recency must be preserved"
    for scope, principal in protected_request:
        assert window.would_exceed_budget(scope, principal, 100), (
            "Every excluded principal must retain the spend it genuinely booked"
        )
    # Removing an exclusion supplies a genuine candidate. The same populated
    # ledger must then report real progress while preserving the other charges.
    assert window._evict_one(exclude=protected_request - {("user", 31)}) is True
    assert not window.would_exceed_budget("user", 31, 100)
    for scope, principal in protected_request - {("user", 31)}:
        assert window.would_exceed_budget(scope, principal, 100)
