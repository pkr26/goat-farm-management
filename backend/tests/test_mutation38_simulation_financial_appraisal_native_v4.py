"""Actual loan ledgers and mathematically known public appraisal results."""

import json
import math

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import break_even_meat_price, run_simulation
from app.simulation.finance import (
    _crossing_decimal_power_roots,
    _positive_power_roots,
    amortization_schedule,
    assess_irr,
    bcr,
    irr,
    irr_roots,
    mirr,
    monthly_emi,
    npv,
    payback_month,
)
from app.simulation.planner import build_dpr_markdown
from app.simulation.subsidy import nlm_unit_subsidy_cap


@pytest.mark.parametrize("rate", [0.0, 0.12, 1e-18])
def test_actual_complete_loan_ledger_reconciles_every_payment_and_final_settlement(
    rate: float,
) -> None:
    try:
        rows = amortization_schedule(120_000.0, rate, 24, 3)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A supported ordinary loan must return its complete native ledger: {exc}")
    assert len(rows) == 24 and [row.month for row in rows] == list(range(1, 25))
    for row in rows:
        assert row.interest == pytest.approx(row.opening_balance * rate / 12)
        assert row.payment == pytest.approx(row.interest + row.principal)
        assert row.opening_balance == pytest.approx(row.principal + row.closing_balance)
        assert math.isfinite(row.payment) and row.principal >= 0.0
    assert all(row.principal == 0.0 for row in rows[:3])
    assert all(row.opening_balance == 120_000.0 for row in rows[:3])
    assert sum(row.principal for row in rows) == pytest.approx(120_000.0)
    assert rows[-1].closing_balance == 0.0
    if rate < 1e-15:
        assert monthly_emi(120_000.0, rate, 21) == pytest.approx(120_000 / 21)


@pytest.mark.parametrize(
    "flows,times,expected",
    [
        ([-100.0, 121.0], [0.0, 2.0], [0.1]),
        ([1.0, -6.0, 8.0], [0.0, 1.0, 2.0], [1.0, 3.0]),
        ([1.0, -4.0, 4.0], [0.0, 1.0, 2.0], [1.0]),
        ([4.0, -13.0, 10.0], [0.0, 0.5, 1.0], [0.5625, 3.0]),
        ([513 / 2048, -1025 / 1024, 1.0], [0.0, 1.0, 2.0], [1024 / 513 - 1, 1.0]),
        ([0.5, -2.5, 4.5, -3.5, 1.0], [0.0, 1.0, 2.0, 3.0, 4.0], [0.0, 1.0]),
        ([0.0, -100.0, 121.0, 0.0], [0.0, 1.0, 3.0, 4.0], [0.1]),
        ([-60.0, -40.0, 121.0], [0.0, 0.0, 2.0], [0.1]),
    ],
)
def test_actual_public_irr_assessment_retains_every_known_distinct_project_return(
    flows: list[float],
    times: list[float],
    expected: list[float],
) -> None:
    # These are analytic appraisal polynomials, including a double root,
    # nearby distinct returns and a flat crossing. They do not constrain an
    # internal precision, refinement count or workspace budget.
    try:
        roots = irr_roots(flows, times)
        assessment = assess_irr(flows, times)
        unique = irr(flows, times)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A supported actual appraisal series must return its disposition: {exc}")
    assert roots == pytest.approx(expected, abs=1e-10)
    assert assessment.roots == pytest.approx(expected, abs=1e-10)
    assert assessment.status == ("unique" if len(expected) == 1 else "multiple_roots")
    if len(expected) == 1:
        assert unique == pytest.approx(expected[0])
    else:
        assert unique is None
    for root in roots:
        assert npv(root, flows, times) == pytest.approx(0.0, abs=1e-8)


def test_actual_zero_and_one_sign_projects_do_not_report_a_unique_return() -> None:
    try:
        assert assess_irr([0.0, 0.0], [0.0, 1.0]).status == "indeterminate"
        assert assess_irr([100.0, -100.0], [1.0, 1.0]).status == "indeterminate"
        for flows in ([100.0, 20.0], [-100.0, -20.0], [0.0, 20.0]):
            assert assess_irr(flows, [0.0, 1.0]).status == "no_root"
            assert irr(flows, [0.0, 1.0]) is None
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A real zero or one-sign appraisal must return its disposition: {exc}")


def test_actual_discounted_cash_and_mirr_use_explicit_partial_year_times() -> None:
    assert npv(0.21, [-100.0, 110.0], [0.0, 0.5]) == pytest.approx(0.0)
    assert mirr([-100.0, 121.0], [0.0, 2.0], 0.1, 0.2) == pytest.approx(0.1)
    assert mirr([-100.0, 50.0, 60.0], [0.0, 1.0, 2.0], 0.1, 0.21) == pytest.approx(
        math.sqrt(1.205) - 1
    )
    assert mirr([0.0, 0.0], [0.0, 1.0], 0.1, 0.1) is None
    assert mirr([], [], 0.1, 0.1) is None
    assert mirr([-100.0, 120.0], [0.0, 0.0], 0.1, 0.1) is None
    assert bcr(0.1, [0.0, 121.0], [100.0, 0.0], [0.0, 2.0]) == pytest.approx(1.0)
    assert bcr(0.1, [100.0], [0.0], [0.0]) is None
    assert payback_month([0.0, -20.0, 0.0, 10.0]) == 2
    assert payback_month([0.0, -20.0, -1.0]) is None
    # Public cash series carry paired times: unequal lengths are malformed,
    # rather than a permission to silently omit a project's final cash flow.
    with pytest.raises(ValueError):
        npv(0.1, [-100.0, 121.0], [0.0])


@pytest.mark.parametrize(
    "females,males,expected",
    [
        (99, 5, 0.0),
        (100, 4, 0.0),
        (0, 0, 0.0),
        (100, 5, 1_000_000.0),
        (200, 10, 2_000_000.0),
        (300, 15, 3_000_000.0),
        (400, 20, 4_000_000.0),
        (500, 25, 5_000_000.0),
        (600, 30, None),
        (100, 6, None),
        (101, 5, None),
        (200, 5, None),
    ],
)
def test_versioned_public_nlm_unit_policy_admits_only_its_published_exact_bands(
    females: int,
    males: int,
    expected: float | None,
) -> None:
    # Exact current DAHD-NLM-2025-01 product contract; a band estimate does
    # not represent applicant eligibility, approval or a cash receipt.
    assert nlm_unit_subsidy_cap(females, males) == expected


@pytest.mark.parametrize("subsidy", ["none", "custom", "nlm", "nlm-unknown"])
def test_real_native_project_report_faithfully_renders_actual_funding_and_appraisal(
    subsidy: str,
) -> None:
    payload: dict[str, object] = {
        "meta": {"horizon_months": 25},
        "finance": {
            "loan_fraction_of_project_cost": 0.5,
            "subsidy_fraction": 0.1 if subsidy == "custom" else 0.0,
            "nlm_subsidy": subsidy.startswith("nlm"),
            "nlm_eligible_capital_cost": 100_000.0 if subsidy == "nlm" else None,
        },
        "herd": {
            "does": 100 if subsidy.startswith("nlm") else 20,
            "bucks": 5 if subsidy.startswith("nlm") else 1,
        },
    }
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        document = build_dpr_markdown(assumptions, result)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A genuine completed native project must render its appraisal report: {exc}")
    metrics = result.metrics
    assert f"25 months ({25 / 12:g} years)" in document
    assert f"| Bank loan | ₹{metrics.loan_amount:,.0f} |" in document
    upfront = 0.0 if assumptions.finance.nlm_subsidy else metrics.subsidy_amount
    assert f"| Up-front capital subsidy | ₹{upfront:,.0f} |" in document
    assert f"| Promoter equity | ₹{metrics.equity:,.0f} |" in document
    assert metrics.loan_amount + metrics.equity + upfront == pytest.approx(metrics.project_cost)
    if metrics.bcr is not None:
        assert f"| Benefit-cost ratio | {metrics.bcr:.2f} |" in document
    else:
        assert "| Benefit-cost ratio | n/a |" in document
    for title, value in (("IRR", metrics.irr), ("MIRR", metrics.mirr)):
        if value is not None:
            assert f"{value * 100:.1f}%" in document, title
    if metrics.avg_dscr is not None:
        assert f"{metrics.avg_dscr:.2f}" in document
    if metrics.min_dscr is not None:
        assert f"{metrics.min_dscr:.2f}" in document
    payback = (
        "beyond horizon" if metrics.payback_month is None else f"month {metrics.payback_month}"
    )
    assert f"| Payback | {payback} |" in document
    assert "| Break-even meat price | n/a |" in document
    if subsidy == "nlm":
        assert metrics.subsidy_estimate_amount is not None
        assert f"Conditional estimate: ₹{metrics.subsidy_estimate_amount:,.0f}" in document
        assert metrics.subsidy_amount == 0.0
    elif subsidy == "nlm-unknown":
        assert metrics.subsidy_estimate_amount is None
        assert "Conditional estimate: Unknown" in document
        assert metrics.subsidy_amount == 0.0


@pytest.mark.parametrize(
    "terms,lo,hi,expected",
    [
        ([(0.0, -0.5), (1.0, 1.0)], 0.5, 2.0, [0.5]),
        ([(2.0, 1.0), (1.0, -2.0), (0.0, 1.0)], 0.1, 2.0, []),
        ([(2.0, 1.0), (1.0, -4.0), (0.0, 3.0)], 0.1, 5.0, [1.0, 3.0]),
    ],
)
def test_actual_callable_decimal_crossings_complete_and_distinguish_touching_and_crossing_roots(
    terms: list[tuple[float, float]],
    lo: float,
    hi: float,
    expected: list[float],
) -> None:
    # This explicitly covers the callable legacy helper, rather than
    # claiming it remains on the current public IRR call path.
    try:
        roots = _crossing_decimal_power_roots(terms, lo, hi)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A supported analytic crossing series must return its native roots: {exc}")
    assert roots == pytest.approx(expected)


def test_native_break_even_solution_completes_and_replays_the_actual_project_cash_flows() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps({"meta": {"horizon_months": 25}, "herd": {"does": 20, "bucks": 1}})
    )
    try:
        price = break_even_meat_price(assumptions)
        assert price is not None and math.isfinite(price) and price > 0
        assumptions.sales.meat_price_per_kg = price
        replay = run_simulation(assumptions, with_break_even=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A supported actual project must complete its break-even appraisal: {exc}")
    assert replay.metrics.npv == pytest.approx(0.0, abs=1.0)


def test_two_year_monthly_alternating_cash_series_retains_its_known_zero_percent_return() -> None:
    flows = [1.0 if month % 2 == 0 else -1.0 for month in range(24)]
    times = [month / 12 for month in range(24)]
    # Paired monthly receipts/payments sum to zero. The alternating24-term
    # polynomial has its sole positive discount-factor root at one, hence0%.
    # No internal term budget, precision, fallback or method is prescribed.
    try:
        roots = irr_roots(flows, times)
        assessment = assess_irr(flows, times)
        result = irr(flows, times)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"This complete monthly cash series has a known native return: {exc}")
    assert roots == pytest.approx([0.0])
    assert assessment.status == "unique" and assessment.roots == pytest.approx([0.0])
    assert result == pytest.approx(0.0)


@pytest.mark.parametrize(
    "terms,expected",
    [
        ([(1.0, 1.0), (0.0, 1.0)], []),
        ([(1.0, 1.0), (0.0, -1.0)], [1.0]),
    ],
)
def test_actual_callable_positive_polynomials_complete_their_exact_root_disposition(
    terms: list[tuple[float, float]],
    expected: list[float],
) -> None:
    try:
        result = _positive_power_roots(terms, 0.0, 2.0)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"An ordinary native linear positive-root calculation must complete: {exc}")
    assert result == pytest.approx(expected)
