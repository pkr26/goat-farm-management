"""Observable simulation-admission behavior at expiry and eviction boundaries."""

import pytest

from app.api._run_limits import _RunCostWindow


@pytest.mark.parametrize(("elapsed", "can_admit"), [(59.999, False), (60.0, True), (60.001, True)])
def test_a_full_cost_charge_expires_at_the_exact_window_boundary(
    elapsed: float, can_admit: bool
) -> None:
    now = [1000.0]
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=4, clock=lambda: now[0])
    window.charge("user", 1, 100)
    assert window.would_exceed_budget("user", 1, 1)

    now[0] += elapsed

    assert window.would_exceed_budget("user", 1, 100) is not can_admit
    if can_admit:
        window.charge("user", 1, 100)
        assert window.is_over_budget("user", 1)


def test_one_half_budget_run_is_protected_from_a_new_principal_spray() -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=2)
    window.charge("user", 1, 50)
    window.charge("user", 2, 1)

    window.charge("user", 3, 1)

    # The expensive principal keeps its spent budget; the cheap one makes room.
    assert window.would_exceed_budget("user", 1, 51)
    assert not window.would_exceed_budget("user", 2, 100)
    assert window.would_exceed_budget("user", 3, 100)


def test_a_live_probe_refreshes_recency_when_old_spend_expires() -> None:
    now = [1000.0]
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=2, clock=lambda: now[0])
    window.charge("user", 1, 50)
    now[0] = 1030.0
    window.charge("user", 1, 1)
    now[0] = 1031.0
    window.charge("user", 2, 1)

    # Expiring the old large charge makes user1 a cheap, recently observed user.
    now[0] = 1061.0
    assert window.would_exceed_budget("user", 1, 100)
    window.charge("user", 3, 1)

    assert window.would_exceed_budget("user", 1, 100)
    assert not window.would_exceed_budget("user", 2, 100)


def test_successful_eviction_frees_a_slot_and_preserves_the_excluded_principal() -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=2)
    window.charge("user", 1, 1)
    window.charge("user", 2, 1)

    assert window._evict_one(exclude={("user", 2)}) is True

    # Admission retries rely on True meaning that the ledger actually shrank.
    # Check that progress before another charge can retry the admission loop.
    assert not window.would_exceed_budget("user", 1, 100)
    assert window.would_exceed_budget("user", 2, 100)
    assert len(window._spend) == 1
    window.charge("user", 3, 1)
    assert window.would_exceed_budget("user", 2, 100)
    assert window.would_exceed_budget("user", 3, 100)
