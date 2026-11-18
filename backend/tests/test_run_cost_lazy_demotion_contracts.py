"""Passive expiry preserves eviction age; an actual budget probe refreshes it."""

import pytest

from app.api._run_limits import _RunCostWindow


def _partly_expired_window(expired_cost: int) -> _RunCostWindow:
    now = [1000.0]
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=3, clock=lambda: now[0])
    window.charge("user", 11, expired_cost)
    now[0] = 1010.0
    window.charge("user", 11, 1)
    now[0] = 1020.0
    window.charge("user", 22, 1)
    now[0] = 1030.0
    window.charge("user", 33, 1)
    now[0] = 1061.0
    return window


@pytest.mark.parametrize("expired_cost", [1, 50], ids=["repeated-cheap-run", "half-budget-run"])
def test_passive_partial_expiry_retires_the_older_demoted_history_first(
    expired_cost: int,
) -> None:
    window = _partly_expired_window(expired_cost)

    # This is the documented non-touching helper boundary. No dictionary or
    # tier is fabricated: one genuine old run expired, and the later run is
    # still charged. Passive maintenance must not impersonate recent traffic.
    assert window._prune_bucket(("user", 11), touch=False) == 1
    window.charge("user", 44, 1)

    assert not window.would_exceed_budget("user", 11, 100), (
        "Passive demotion must retire the older history before a later cold principal"
    )
    for principal in (22, 33, 44):
        assert window.would_exceed_budget("user", principal, 100), (
            "The later cold principals and newly admitted run must retain their real spend"
        )


def test_an_actual_budget_probe_keeps_partly_expired_history_recent() -> None:
    window = _partly_expired_window(50)

    # A real probe is traffic, so its remaining cheap charge becomes recent.
    # This public path is deliberately distinct from passive maintenance.
    assert window.would_exceed_budget("user", 11, 100)
    window.charge("user", 44, 1)

    assert not window.would_exceed_budget("user", 22, 100)
    for principal in (11, 33, 44):
        assert window.would_exceed_budget("user", principal, 100)
