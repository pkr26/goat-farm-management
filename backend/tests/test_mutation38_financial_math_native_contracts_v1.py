"""Native mathematical helper contracts for undefined returns and loan cash flows.

The direct amortization helper is a pure financial-math boundary. Its documented
positive-rate annuity contract also covers rates beyond the scenario editor's
policy limit; this test does not claim that those rates are admitted by that API.
"""

import math

import pytest

from app.simulation.finance import amortization_schedule, mirr, monthly_emi


@pytest.mark.parametrize(
    "flows,times,expected",
    [
        pytest.param([], [], None, id="empty-project"),
        pytest.param([-1.0, 2.0], [0.0], None, id="unpaired-observations"),
        pytest.param([-1.0, 2.0], [0.0, 0.0], None, id="zero-observation-horizon"),
        pytest.param([0.0, 2.0], [0.0, 1.0], None, id="no-investment"),
        pytest.param([-1.0, 0.0], [0.0, 1.0], None, id="no-benefit"),
        pytest.param([-1.0, 2.0], [0.0, 1.0], 1.0, id="one-year-doubling"),
        pytest.param([-1.0, 0.0, 2.0], [0.0, 0.5, 1.0], 1.0, id="neutral-interim-flow"),
    ],
)
def test_mirr_reports_the_defined_return_or_an_undefined_project(
    flows: list[float], times: list[float], expected: float | None
) -> None:
    try:
        actual = mirr(flows, times, 0.0, 0.0)
    except (ValueError, ZeroDivisionError) as exc:
        pytest.fail(f"The documented undefined-project cases must return None: {exc}")
    if expected is None:
        assert actual is None
    else:
        assert actual == pytest.approx(expected)


def test_mirr_discounts_a_later_investment_at_its_actual_fractional_time() -> None:
    assert mirr([-1.0, 2.0], [0.5, 1.0], 0.5, 0.0) == pytest.approx(2.0 * math.sqrt(1.5) - 1.0)


def test_no_repayment_term_has_no_monthly_instalment() -> None:
    try:
        payment = monthly_emi(120.0, 0.12, 0)
    except (ValueError, ZeroDivisionError) as exc:
        pytest.fail(f"A nonpositive repayment term has the documented zero instalment: {exc}")
    assert payment == 0.0


def test_zero_interest_schedule_conserves_principal_without_interest() -> None:
    try:
        rows = amortization_schedule(120.0, 0.0, 4, 0)
    except (ValueError, ZeroDivisionError) as exc:
        pytest.fail(f"A valid zero-interest loan must produce its repayment schedule: {exc}")
    assert len(rows) == 4
    assert all(row.interest == 0.0 and row.payment == 30.0 for row in rows)
    assert sum(row.principal for row in rows) == 120.0
    assert rows[-1].closing_balance == 0.0


def test_native_annuity_helper_preserves_equated_payments_at_a_positive_high_rate() -> None:
    # The independent three-month annuity is 500*(1/2)*(3/2)^3 / ((3/2)^3-1)
    # = 6750/19. Checking its cash flows catches a cap that underpays interest.
    rows = amortization_schedule(500.0, 6.0, 3, 0)
    assert len(rows) == 3
    assert [row.payment for row in rows] == pytest.approx([6750.0 / 19.0] * 3)
    assert all(row.principal > 0.0 for row in rows)
    assert sum(row.principal for row in rows) == pytest.approx(500.0)
    assert rows[-1].closing_balance == 0.0
    assert sum(row.payment for row in rows) == pytest.approx(
        500.0 + sum(row.interest for row in rows)
    )
