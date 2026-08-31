"""Tests for the explainability layer (app/simulation/explain.py).

Every simulation result carries a complete viability-metric explanation set and a 6-section
narrative report, generated deterministically from the run's own numbers.
Runs use ``with_break_even=False`` unless the metric under test needs the
bisection, to keep the file cheap.
"""

import hashlib
import json
from typing import cast

import pytest

from app.simulation import (
    MetaAssumptions,
    OptimizationCandidate,
    OptimizationResult,
    SensitivityItem,
    SimulationAssumptions,
    SimulationResult,
    run_simulation,
)
from app.simulation.assumptions import HerdEventAssumptions
from app.simulation.explain import (
    _inr,
    _outstanding_at_horizon,
    _pct,
    _ranked,
    _share,
    _year_of,
    build_metric_explanations,
    build_narrative_report,
)

METRIC_KEYS = {
    "project_cost",
    "loan_amount",
    "subsidy_amount",
    "equity",
    "npv",
    "irr",
    "mirr",
    "bcr",
    "avg_dscr",
    "min_dscr",
    "payback_month",
    "break_even_meat_price_per_kg",
    "peak_capacity_head",
    "terminal_value",
    "tax_total",
    "accounting_profit_total",
    "minimum_cash_balance",
    "operating_margin",
}

SECTION_KEYS = {
    "overview",
    "herd_trajectory",
    "revenue_mix",
    "cost_mix",
    "viability_verdict",
    "risks",
}


def _verdict(result: SimulationResult) -> str:
    section = next(s for s in result.narrative_report if s.key == "viability_verdict")
    return str(section.figures["verdict"])


def test_narrative_formatters_hold_exact_public_boundaries() -> None:
    assert _inr(99_999.0) == "₹99,999"
    assert _inr(100_000.0) == "₹1.00 lakh"
    # This value sits on the two-decimal rounding edge, so even a one-rupee
    # drift in the crore divisor changes the public amount.
    assert _inr(10_050_001.0) == "₹1.01 crore"
    assert _inr(-10_000_000.0) == "-₹1.00 crore"
    assert _pct(0.1235) == "12.3%"
    assert _share(1.0, 4.0) == "25.0%"
    assert _share(0.5, 1.0) == "50.0%"
    assert _share(1.0, 0.0) == "—"
    assert _share(1.0, -1.0) == "—"
    assert _year_of(-1) == "project start (month 0)"
    assert _year_of(0) == "project start (month 0)"
    assert _year_of(1) == "month 1 (year 1)"
    assert _year_of(12) == "month 12 (year 1)"
    assert _year_of(13) == "month 13 (year 2)"
    assert (
        _ranked(
            [("feed", 100_000.0), ("labour", 200_000.0), ("vet", 0.0)],
            300_000.0,
        )
        == "labour ₹2.00 lakh (66.7%), feed ₹1.00 lakh (33.3%) and vet ₹0 (0.0%)"
    )
    assert _ranked([("feed", 100.0)], 100.0) == "feed ₹100 (100.0%)"
    assert _ranked([("feed", 60.0), ("vet", 40.0)], 100.0) == (
        "feed ₹60 (60.0%) and vet ₹40 (40.0%)"
    )


def test_every_run_has_all_explanations_and_sections() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert {e.key for e in res.metric_explanations} == METRIC_KEYS
    assert len(res.metric_explanations) == len(METRIC_KEYS)
    for entry in res.metric_explanations:
        assert entry.title.strip()
        assert entry.explanation.strip()
    assert {s.key for s in res.narrative_report} == SECTION_KEYS
    assert len(res.narrative_report) == 6
    for section in res.narrative_report:
        assert section.title.strip()
        assert section.paragraphs
        assert all(p.strip() for p in section.paragraphs)


def test_explanation_figures_match_metrics() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    m = res.metrics
    by_key = {e.key: e.figures for e in res.metric_explanations}
    assert by_key["project_cost"]["project_cost"] == pytest.approx(m.project_cost)
    assert by_key["loan_amount"]["loan_amount"] == pytest.approx(m.loan_amount)
    assert by_key["subsidy_amount"]["subsidy_amount"] == pytest.approx(m.subsidy_amount)
    assert by_key["equity"]["equity"] == pytest.approx(m.equity)
    assert by_key["npv"]["npv"] == pytest.approx(m.npv)
    assert by_key["npv"]["discount_rate_annual"] == pytest.approx(0.12)
    assert by_key["irr"]["irr"] == pytest.approx(m.irr)
    assert by_key["mirr"]["mirr"] == pytest.approx(m.mirr)
    assert by_key["bcr"]["bcr"] == pytest.approx(m.bcr)
    assert by_key["avg_dscr"]["avg_dscr"] == pytest.approx(m.avg_dscr)
    assert by_key["min_dscr"]["min_dscr"] == pytest.approx(m.min_dscr)
    assert by_key["payback_month"]["payback_month"] == m.payback_month
    assert by_key["break_even_meat_price_per_kg"]["assumed_meat_price_per_kg"] == (
        SimulationAssumptions().sales.meat_price_per_kg
    )
    assert by_key["peak_capacity_head"]["capacity_places"] == pytest.approx(m.peak_capacity_head)
    assert by_key["terminal_value"]["terminal_value"] == pytest.approx(m.terminal_value)
    assert by_key["tax_total"]["tax_total"] == pytest.approx(m.tax_total)
    assert by_key["accounting_profit_total"]["accounting_profit_total"] == pytest.approx(
        m.accounting_profit_total
    )
    assert by_key["minimum_cash_balance"]["minimum_cash_balance"] == pytest.approx(
        m.minimum_cash_balance
    )
    assert by_key["operating_margin"]["operating_margin"] == pytest.approx(m.operating_margin)


def test_project_cost_explanation_figures_sum_to_project_cost() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    entry = next(e for e in res.metric_explanations if e.key == "project_cost")
    figures = entry.figures
    component_sum = sum(
        cast(float, figures[key])
        for key in ("shed_cost", "equipment_cost", "stock_cost", "working_capital")
    )
    assert component_sum == pytest.approx(figures["project_cost"])
    assert component_sum == pytest.approx(res.metrics.project_cost)
    b = res.project_cost_breakdown
    assert figures["shed_cost"] == pytest.approx(b.shed_cost)
    assert figures["working_capital"] == pytest.approx(b.working_capital)
    assert "highest physical headcount reached" in entry.explanation
    assert "projected monthly peak" not in entry.explanation


def test_verdict_not_viable_for_default_run() -> None:
    # The default Osmanabadi unit is NPV-negative at a 12% discount rate.
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert res.metrics.npv <= 0.0
    assert _verdict(res) == "NOT VIABLE"


def _viable_assumptions() -> SimulationAssumptions:
    """A run where every standard check genuinely passes, weakest debt year
    included (a short, half-financed loan against strong early meat sales)."""
    a = SimulationAssumptions()
    a.sales.meat_price_per_kg = 800.0
    a.herd.male_growers = 250  # meat revenue from month 1
    a.finance.loan_term_months = 12
    a.finance.moratorium_months = 6
    a.finance.loan_fraction_of_project_cost = 0.5
    a.finance.working_capital_months = 6
    return a


def _explanation_digest(result: SimulationResult) -> str:
    payload = json.dumps(
        [entry.model_dump(mode="json") for entry in result.metric_explanations],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _report_digest(result: SimulationResult) -> str:
    payload = json.dumps(
        [section.model_dump(mode="json") for section in result.narrative_report],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def test_metric_narratives_are_stable_across_core_financial_branches() -> None:
    """Bank-facing explanations change only through an explicit contract update."""
    default = run_simulation(SimulationAssumptions(), with_break_even=False)
    viable = run_simulation(_viable_assumptions(), with_break_even=False)
    no_debt_or_terminal = SimulationAssumptions()
    no_debt_or_terminal.finance.loan_fraction_of_project_cost = 0.0
    no_debt_or_terminal.finance.include_terminal_value = False
    no_debt = run_simulation(no_debt_or_terminal, with_break_even=False)
    risk_assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    risk_assumptions.risk.monte_carlo_runs = 10
    risk = run_simulation(
        risk_assumptions,
        with_break_even=False,
        with_monte_carlo=True,
        with_sensitivity=True,
    )

    assert _explanation_digest(default) == (
        "60a3aa25246e7afd7038a8caeb88fd3af53a7f309fb3e7116d0f0cb4870fa8e4"
    )
    assert _explanation_digest(viable) == (
        "301381b376e90a6a82bc18a59ad634ed2be97bf36d7aded656c0669e35da4935"
    )
    assert _explanation_digest(no_debt) == (
        "e47c1b61a789f69cef0d93ecee9fab2f3bd79001343d5e937fb2aac0eee22fb2"
    )
    assert _report_digest(default) == (
        "cf3c9178a5f579e5289da73d1051fef4b05b47eaa62d4f91bbcb5cf4c9a6d184"
    )
    assert _report_digest(viable) == (
        "2724c12a5636c4d118aab351de8c3f0b71ac5bb6d641169530f9cf2fd69ba949"
    )
    assert _report_digest(no_debt) == (
        "29a12c4576a2bcc17246d8b17ee79ccdcd8e3dfaf162c28eecce7d43e213bc64"
    )
    # Sensitivity gained the milk_price tornado bar (dairy support): the risk
    # branch digest changed with that explicit contract update.
    assert _report_digest(risk) == (
        "62077485c82b498621f4823ec26a9d0e883c18cfa72c6c415229d7e6d49b7a7e"
    )


def test_verdict_viable_when_all_checks_pass() -> None:
    a = _viable_assumptions()
    res = run_simulation(a, with_break_even=False)
    m = res.metrics
    # All hard and soft checks pass: npv > 0, bcr >= 1, irr >= discount rate,
    # a real debt year with min_dscr >= 1, payback present.
    assert m.npv > 0.0 and m.bcr is not None and m.bcr >= 1.0
    assert m.irr is not None and m.irr >= a.finance.discount_rate_annual
    assert m.payback_month is not None
    assert m.min_dscr is not None and m.min_dscr >= 1.0
    assert m.additional_working_capital_required == 0.0
    assert _verdict(res) == "VIABLE"
    section = next(s for s in res.narrative_report if s.key == "viability_verdict")
    assert any("All standard checks pass" in p for p in section.paragraphs)


def test_verdict_rejects_a_projected_herd_above_funded_capacity() -> None:
    a = _viable_assumptions()
    a.costs.capacity_basis = "planned"
    a.costs.planned_capacity_head = 1

    res = run_simulation(a, with_break_even=False)

    assert res.metrics.npv > 0.0
    assert res.project_cost_breakdown.projected_peak_head > 1.0
    assert _verdict(res) == "NOT VIABLE"
    section = next(s for s in res.narrative_report if s.key == "viability_verdict")
    assert any("exceeds funded housing and equipment capacity" in p for p in section.paragraphs)


def test_verdict_viable_with_caution_for_borderline_run() -> None:
    # Positive NPV but the weakest debt year cannot cover its repayment
    # (min_dscr in (0, 1)): viable, with a warning.
    a = SimulationAssumptions()
    a.herd.male_growers = 60  # early meat revenue keeps year-1 EBITDA positive
    a.finance.moratorium_months = 0  # full EMI from month 1 squeezes year-1 DSCR
    a.sales.meat_price_per_kg = 700.0
    res = run_simulation(a, with_break_even=False)
    m = res.metrics
    assert m.npv > 0.0 and m.bcr is not None and m.bcr >= 1.0
    assert m.min_dscr is not None and 0.0 < m.min_dscr < 1.0
    assert _verdict(res) == "VIABLE WITH CAUTION"
    section = next(s for s in res.narrative_report if s.key == "viability_verdict")
    assert any("DSCR below 1.0" in p for p in section.paragraphs)


def test_verdict_never_hides_a_negative_weakest_debt_year() -> None:
    """A DSCR at or below zero is strictly worse than 0.5 — the debt year had
    no operating surplus at all. The guard was `0.0 < min_dscr < 1.0`, so
    every negative year fell through it and the report printed "VIABLE — all
    standard checks pass" with no mention of the DSCR."""
    a = SimulationAssumptions()
    a.sales.meat_price_per_kg = 700.0
    res = run_simulation(a, with_break_even=False)
    m = res.metrics
    # Everything else passes; only year 1 cannot service the loan.
    assert m.npv > 0.0 and m.bcr is not None and m.bcr > 1.0
    assert m.irr is not None and m.irr >= a.finance.discount_rate_annual
    assert m.payback_month is not None
    assert m.min_dscr is not None and m.min_dscr < 0.0
    assert m.dscr_per_year[0] < 0.0
    assert _verdict(res) == "VIABLE WITH CAUTION"
    section = next(s for s in res.narrative_report if s.key == "viability_verdict")
    assert any("non-positive DSCR" in p for p in section.paragraphs)
    assert not any("All standard checks pass" in p for p in section.paragraphs)


def test_verdict_holds_exact_viability_boundaries() -> None:
    assumptions = _viable_assumptions()
    result = run_simulation(assumptions, with_break_even=False)

    def section(
        *,
        capacity_places: float | None = None,
        projected_peak_head: float | None = None,
        **metric_updates: float | int | None,
    ):
        breakdown = result.project_cost_breakdown
        if capacity_places is not None or projected_peak_head is not None:
            breakdown = breakdown.model_copy(
                update={
                    "capacity_places": (
                        breakdown.capacity_places if capacity_places is None else capacity_places
                    ),
                    "projected_peak_head": (
                        breakdown.projected_peak_head
                        if projected_peak_head is None
                        else projected_peak_head
                    ),
                }
            )
        changed = result.model_copy(
            update={
                "metrics": result.metrics.model_copy(update=metric_updates),
                "project_cost_breakdown": breakdown,
            }
        )
        return next(
            item
            for item in build_narrative_report(assumptions, changed)
            if item.key == "viability_verdict"
        )

    zero_npv = section(npv=0.0)
    assert zero_npv.figures["verdict"] == "VIABLE WITH CAUTION"
    assert zero_npv.paragraphs[1] == (
        "Watch out: the NPV is exactly zero — the project only just clears the "
        "discount rate, with no margin."
    )

    assert section(npv=0.5).figures["verdict"] == "VIABLE"
    assert section(bcr=1.0).figures["verdict"] == "VIABLE"
    assert section(irr=assumptions.finance.discount_rate_annual).figures["verdict"] == "VIABLE"
    assert section(min_dscr=1.0).figures["verdict"] == "VIABLE"

    zero_dscr = section(min_dscr=0.0)
    assert zero_dscr.paragraphs[1] == (
        "Watch out: the weakest debt year has a non-positive DSCR (no operating surplus "
        "to pay the instalment from)."
    )
    low_dscr = section(min_dscr=0.5)
    assert low_dscr.paragraphs[1] == ("Watch out: the weakest debt year has a DSCR below 1.0.")
    tiny_shortfall = section(additional_working_capital_required=0.5)
    assert tiny_shortfall.paragraphs[1] == (
        "Watch out: the funded working-capital reserve becomes negative and additional "
        "liquidity is needed."
    )

    epsilon_capacity = section(
        capacity_places=1e-9,
        projected_peak_head=2e-9,
    )
    assert epsilon_capacity.figures["verdict"] == "VIABLE"
    half_head_short = section(capacity_places=1.0, projected_peak_head=1.5)
    assert half_head_short.figures["verdict"] == "NOT VIABLE"
    assert (
        "exceeds funded housing and equipment capacity by 0.5 head"
        in (half_head_short.paragraphs[1])
    )

    negative_npv_only = section(npv=-0.5)
    assert negative_npv_only.figures["verdict"] == "NOT VIABLE"


def test_dscr_explanations_never_claim_no_debt_when_debt_years_exist() -> None:
    """Both DSCR explanations used a positivity test as a proxy for "debt
    service exists", so the stock default (year-1 DSCR -6.13 over six real
    debt years) was described as having no debt in the horizon at all — while
    the figures in the same payload said debt_years = 6."""
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    m = res.metrics
    assert m.avg_dscr is not None and m.avg_dscr < 0.0
    assert m.min_dscr is not None and m.min_dscr < 0.0
    by_key = {e.key: e for e in res.metric_explanations}
    avg = by_key["avg_dscr"]
    assert avg.figures["debt_years"] == 6
    assert "No debt service falls inside" not in avg.explanation
    assert f"{m.avg_dscr:.2f}" in avg.explanation
    weakest = by_key["min_dscr"]
    assert "No debt year in the horizon" not in weakest.explanation
    assert f"{m.min_dscr:.2f}" in weakest.explanation
    assert "cannot pay any part of the instalment" in weakest.explanation


def test_dscr_explanations_report_no_debt_only_when_there_is_none() -> None:
    a = SimulationAssumptions()
    a.finance.loan_fraction_of_project_cost = 0.0
    res = run_simulation(a, with_break_even=False)
    assert res.metrics.avg_dscr is None and res.metrics.min_dscr is None
    by_key = {e.key: e for e in res.metric_explanations}
    assert "No debt service falls inside" in by_key["avg_dscr"].explanation
    assert by_key["avg_dscr"].figures["debt_years"] == 0
    assert "No debt year in the horizon" in by_key["min_dscr"].explanation


def test_metric_explanations_cover_undefined_and_dscr_boundary_branches() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    result = run_simulation(assumptions, with_break_even=False)

    def entry(key: str, **metric_updates: float | None):
        changed = result.model_copy(
            update={"metrics": result.metrics.model_copy(update=metric_updates)}
        )
        return next(
            item
            for item in build_metric_explanations(
                assumptions,
                changed,
                break_even_computed=False,
            )
            if item.key == key
        )

    assert entry("mirr", mirr=None).explanation == (
        "MIRR is undefined because this projection does not contain both a negative and a "
        "positive cash flow."
    )
    assert entry("bcr", bcr=None).explanation == (
        "Benefit-cost ratio is undefined because the project has no discounted costs at all "
        "(e.g. a fully subsidised, immediately cash-positive setup)."
    )
    assert entry("min_dscr", min_dscr=0.0).explanation == (
        "The worst single debt year: 0.00. It is not positive, so that year's operating "
        "surplus (EBITDA) was zero or negative — the farm cannot pay any part of the "
        "instalment out of operations and must fund it from the promoter's pocket, a longer "
        "moratorium or a rescheduling."
    )
    assert entry("min_dscr", min_dscr=0.5).explanation == (
        "The worst single debt year: 0.50. That is below 1.0, so the farm cannot cover that "
        "year's repayment from operations and needs a cash buffer or a rescheduling."
    )
    assert entry("operating_margin", operating_margin=None).explanation == (
        "Operating margin is undefined because the projection has no operating revenue."
    )

    without_loss_carryforward = assumptions.model_copy(deep=True)
    without_loss_carryforward.finance.tax_loss_carryforward = False
    tax = next(
        item
        for item in build_metric_explanations(
            without_loss_carryforward,
            result,
            break_even_computed=False,
        )
        if item.key == "tax_total"
    )
    assert tax.explanation == (
        f"The model pays {_inr(result.metrics.tax_total)} of cash tax at an assumed "
        f"{_pct(without_loss_carryforward.finance.income_tax_rate)} rate after interest, "
        "straight-line depreciation and current-period losses only."
    )


def test_metric_explanations_hold_exact_financial_decision_boundaries() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    result = run_simulation(assumptions, with_break_even=False)

    def entry(key: str, **metric_updates: float | None):
        changed = result.model_copy(
            update={"metrics": result.metrics.model_copy(update=metric_updates)}
        )
        return next(
            item
            for item in build_metric_explanations(
                assumptions,
                changed,
                break_even_computed=True,
            )
            if item.key == key
        )

    assert "exactly zero" in entry("npv", npv=0.0).explanation
    assert "positive NPV" in entry("npv", npv=0.5).explanation
    assert (
        "beats the required return"
        in entry("irr", irr=assumptions.finance.discount_rate_annual).explanation
    )
    assert "If this dips below 1.0" in entry("min_dscr", min_dscr=1.0).explanation
    assert (
        "needs at least another"
        in entry(
            "minimum_cash_balance",
            additional_working_capital_required=0.5,
        ).explanation
    )

    tiny_subsidy = entry("subsidy_amount", subsidy_amount=0.5)
    assert tiny_subsidy.explanation.startswith("Capital subsidy at")
    assert "No capital subsidy" not in tiny_subsidy.explanation

    annual_pl = [row.model_copy(update={"debt_service": 0.0}) for row in result.annual_pl]
    annual_pl[0] = annual_pl[0].model_copy(update={"debt_service": 0.5})
    tiny_debt_result = result.model_copy(
        update={
            "annual_pl": annual_pl,
            "metrics": result.metrics.model_copy(update={"avg_dscr": 1.5}),
        }
    )
    average = next(
        item
        for item in build_metric_explanations(
            assumptions,
            tiny_debt_result,
            break_even_computed=True,
        )
        if item.key == "avg_dscr"
    )
    assert average.figures["debt_years"] == 1
    assert "over the 1 year(s) with debt outstanding" in average.explanation


def test_loan_explanation_uses_the_exact_horizon_balance_and_positive_threshold() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    result = run_simulation(assumptions, with_break_even=False)
    assert len(result.amortization) > assumptions.meta.horizon_months + 1

    schedule = list(result.amortization)
    balances = {
        assumptions.meta.horizon_months - 2: 222_222.0,
        assumptions.meta.horizon_months - 1: 111_111.0,
        assumptions.meta.horizon_months + 1: 333_333.0,
    }
    for index, balance in balances.items():
        schedule[index] = schedule[index].model_copy(update={"closing_balance": balance})
    changed = result.model_copy(update={"amortization": schedule})
    loan = next(
        item
        for item in build_metric_explanations(
            assumptions,
            changed,
            break_even_computed=True,
        )
        if item.key == "loan_amount"
    )
    expected = balances[assumptions.meta.horizon_months - 1]
    assert f"{_inr(expected)} still outstanding" in loan.explanation
    assert _inr(balances[assumptions.meta.horizon_months - 2]) not in loan.explanation
    assert _inr(balances[assumptions.meta.horizon_months + 1]) not in loan.explanation

    schedule = list(result.amortization)
    schedule[assumptions.meta.horizon_months - 1] = schedule[
        assumptions.meta.horizon_months - 1
    ].model_copy(update={"closing_balance": 0.5})
    tiny_balance_result = result.model_copy(update={"amortization": schedule})
    tiny_loan = next(
        item
        for item in build_metric_explanations(
            assumptions,
            tiny_balance_result,
            break_even_computed=True,
        )
        if item.key == "loan_amount"
    )
    assert "still outstanding is charged" in tiny_loan.explanation
    assert _outstanding_at_horizon(assumptions, tiny_balance_result) == 0.5


def test_loan_explanation_omits_zero_moratorium_and_reports_one_month() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    result = run_simulation(assumptions, with_break_even=False)

    zero = assumptions.model_copy(deep=True)
    zero.finance.moratorium_months = 0
    zero_text = next(
        item.explanation
        for item in build_metric_explanations(zero, result, break_even_computed=True)
        if item.key == "loan_amount"
    )
    assert "interest-only moratorium" not in zero_text
    assert "annual interest. The 12-month projection ends first" in zero_text

    one = assumptions.model_copy(deep=True)
    one.finance.moratorium_months = 1
    one_text = next(
        item.explanation
        for item in build_metric_explanations(one, result, break_even_computed=True)
        if item.key == "loan_amount"
    )
    assert "interest-only moratorium for the first 1 month(s)" in one_text


def test_break_even_explanation_covers_no_solution_and_margin_boundaries() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    result = run_simulation(assumptions, with_break_even=False)

    def explanation(value: float | None, *, computed: bool = True) -> str:
        changed = result.model_copy(
            update={
                "metrics": result.metrics.model_copy(update={"break_even_meat_price_per_kg": value})
            }
        )
        return next(
            item.explanation
            for item in build_metric_explanations(
                assumptions,
                changed,
                break_even_computed=computed,
            )
            if item.key == "break_even_meat_price_per_kg"
        )

    assert explanation(None) == (
        "Even at the break-even search ceiling the project cannot reach NPV = 0 — the "
        "economics need structural changes (costs, herd size, financing), not just a better "
        "market price."
    )
    defaulted = next(
        item
        for item in build_metric_explanations(assumptions, result)
        if item.key == "break_even_meat_price_per_kg"
    )
    assert defaulted.explanation == explanation(None)
    assert defaulted.figures["safety_margin"] is None
    assert explanation(0.0) == (
        "The project is profitable even with meat revenue at zero — the break-even "
        "meat price is effectively ₹0/kg."
    )
    zero_price_result = result.model_copy(
        update={"metrics": result.metrics.model_copy(update={"break_even_meat_price_per_kg": 0.0})}
    )
    zero_price = next(
        item
        for item in build_metric_explanations(
            assumptions,
            zero_price_result,
            break_even_computed=True,
        )
        if item.key == "break_even_meat_price_per_kg"
    )
    assert zero_price.figures["safety_margin"] == 1.0
    assert explanation(1.0).startswith("Meat can fall to ₹1/kg")
    zero_margin = (
        "Meat can fall to ₹400/kg before the project's NPV turns negative. Your assumed "
        "price is ₹400/kg — a safety margin of 0.0%."
    )
    assert explanation(400.0) == zero_margin
    # The flag distinguishes only the two reasons a missing result is None;
    # it must not hide a result that is already present.
    assert explanation(400.0, computed=False) == zero_margin
    assert explanation(200.0) == (
        "Meat can fall to ₹200/kg before the project's NPV turns negative. Your assumed "
        "price is ₹400/kg — a safety margin of 50.0%."
    )

    one_rupee_assumption = assumptions.model_copy(deep=True)
    one_rupee_assumption.sales.meat_price_per_kg = 1.0
    needs_increase = result.model_copy(
        update={"metrics": result.metrics.model_copy(update={"break_even_meat_price_per_kg": 2.0})}
    )
    below_break_even = next(
        item.explanation
        for item in build_metric_explanations(
            one_rupee_assumption,
            needs_increase,
            break_even_computed=True,
        )
        if item.key == "break_even_meat_price_per_kg"
    )
    assert below_break_even == (
        "Meat must rise to ₹2/kg for the project to break even; your assumed price of "
        "₹1/kg is 100.0% too low."
    )


def test_cost_and_revenue_mix_rank_by_actual_magnitude() -> None:
    """The paragraphs used to hard-code "Feed is the largest" and "Meat sales
    dominate". With a flat ₹10,000/month labour floor, labour outweighs feed
    for any smallholder herd — and the old sentence contradicted the figures
    printed in its own next clause."""
    a = SimulationAssumptions()
    a.herd.does = 5
    a.herd.max_breeding_does = 5
    res = run_simulation(a, with_break_even=False)
    sections = {s.key: s for s in res.narrative_report}
    cost = sections["cost_mix"]
    labour = cast(float, cost.figures["labour_cost"])
    feed = cast(float, cost.figures["feed_cost"])
    assert labour > feed  # the inversion that made the old sentence false
    paragraph = cost.paragraphs[0]
    assert paragraph.index("labour") < paragraph.index("feed")
    assert "Feed is the largest" not in paragraph
    revenue = sections["revenue_mix"]
    order = [
        name
        for name, _ in sorted(
            (
                ("meat sales", cast(float, revenue.figures["meat_revenue"])),
                ("cull sales", cast(float, revenue.figures["cull_revenue"])),
                ("milk", cast(float, revenue.figures["milk_revenue"])),
                ("manure", cast(float, revenue.figures["manure_revenue"])),
            ),
            key=lambda item: item[1],
            reverse=True,
        )
    ]
    positions = [revenue.paragraphs[0].index(name) for name in order]
    assert positions == sorted(positions), revenue.paragraphs[0]


def test_mix_totals_include_nonzero_milk_and_selling_cost() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    result = run_simulation(assumptions, with_break_even=False)
    annual_pl = list(result.annual_pl)
    annual_pl[0] = annual_pl[0].model_copy(update={"milk_revenue": 123.0, "selling_cost": 45.0})
    changed = result.model_copy(update={"annual_pl": annual_pl})
    sections = {section.key: section for section in build_narrative_report(assumptions, changed)}

    revenue = sections["revenue_mix"].figures
    assert revenue["total_revenue"] == pytest.approx(
        cast(float, revenue["meat_revenue"])
        + cast(float, revenue["cull_revenue"])
        + cast(float, revenue["milk_revenue"])
        + cast(float, revenue["manure_revenue"])
    )
    costs = sections["cost_mix"].figures
    assert costs["total_opex"] == pytest.approx(
        cast(float, costs["feed_cost"])
        + cast(float, costs["labour_cost"])
        + cast(float, costs["stock_purchases"])
        + cast(float, costs["selling_cost"])
        + cast(float, costs["vet_cost"])
        + cast(float, costs["insurance_cost"])
        + cast(float, costs["misc_cost"])
    )


def test_risks_section_hint_without_monte_carlo_or_sensitivity() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert res.monte_carlo is None and res.sensitivity is None
    risks = next(s for s in res.narrative_report if s.key == "risks")
    assert any("Run with Monte Carlo and sensitivity" in p for p in risks.paragraphs)
    assert "npv_mean" not in risks.figures
    assert "top_sensitivities" not in risks.figures


def test_risks_section_uses_monte_carlo_and_sensitivity() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=24))
    a.risk.monte_carlo_runs = 10
    res = run_simulation(a, with_break_even=False, with_monte_carlo=True, with_sensitivity=True)
    assert res.monte_carlo is not None and res.sensitivity is not None
    risks = next(s for s in res.narrative_report if s.key == "risks")
    assert any("Monte Carlo" in p for p in risks.paragraphs)
    assert not any("Run with Monte Carlo and sensitivity" in p for p in risks.paragraphs)
    assert risks.figures["mc_runs"] == 10
    assert risks.figures["npv_mean"] == pytest.approx(res.monte_carlo.npv_mean)
    assert risks.figures["npv_p5"] == pytest.approx(res.monte_carlo.npv_p5)
    assert risks.figures["prob_npv_negative"] == pytest.approx(res.monte_carlo.prob_npv_negative)
    assert risks.figures["prob_liquidity_shortfall"] == pytest.approx(
        res.monte_carlo.prob_liquidity_shortfall
    )
    # Sensitivity movers are named; meat price dominates the tornado ranking.
    assert any("move NPV the most" in p for p in risks.paragraphs)
    assert any("meat price" in p for p in risks.paragraphs)
    assert "meat_price" in str(risks.figures["top_sensitivities"])
    # Every mover quotes the perturbation the engine actually ran, never a
    # hard-coded "20%" (sale age moves in months, four cases clamp).
    movers = next(p for p in risks.paragraphs if "move NPV the most" in p)
    assert "20% moves NPV" not in movers
    top = sorted(
        res.sensitivity,
        key=lambda item: max(abs(item.delta_npv_low), abs(item.delta_npv_high)),
        reverse=True,
    )[:3]
    for item in top:
        larger = (
            item.label_low
            if abs(item.delta_npv_low) >= abs(item.delta_npv_high)
            else item.label_high
        )
        assert f"{larger} moves NPV by" in movers


def test_sensitivity_ties_deterministically_prefer_the_low_scenario() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    result = run_simulation(assumptions, with_break_even=False)
    tied = SensitivityItem(
        parameter="tied_input",
        delta_npv_low=-100.0,
        delta_npv_high=100.0,
        label_low="low case",
        label_high="high case",
    )
    changed = result.model_copy(update={"sensitivity": [tied]})
    risks = next(
        section
        for section in build_narrative_report(assumptions, changed)
        if section.key == "risks"
    )
    assert risks.paragraphs[-1] == (
        "The assumptions that move NPV the most: tied input (low case moves NPV by -₹100)."
    )


def test_revenue_and_cost_mix_totals_match_annual_pl() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    sections = {s.key: s.figures for s in res.narrative_report}
    revenue = sections["revenue_mix"]
    assert revenue["meat_revenue"] == pytest.approx(sum(r.meat_revenue for r in res.annual_pl))
    assert revenue["cull_revenue"] == pytest.approx(sum(r.cull_revenue for r in res.annual_pl))
    assert revenue["milk_revenue"] == pytest.approx(sum(r.milk_revenue for r in res.annual_pl))
    assert revenue["manure_revenue"] == pytest.approx(sum(r.manure_revenue for r in res.annual_pl))
    assert revenue["total_revenue"] == pytest.approx(sum(r.total_revenue for r in res.annual_pl))
    cost = sections["cost_mix"]
    assert cost["feed_cost"] == pytest.approx(sum(r.feed_cost for r in res.annual_pl))
    assert cost["labour_cost"] == pytest.approx(sum(r.labour_cost for r in res.annual_pl))
    assert cost["stock_purchases"] == pytest.approx(sum(r.stock_purchases for r in res.annual_pl))
    assert cost["selling_cost"] == pytest.approx(sum(r.selling_cost for r in res.annual_pl))
    assert cost["total_opex"] == pytest.approx(sum(r.total_opex for r in res.annual_pl))


def test_fodder_deficit_narrative_matches_engine_costing() -> None:
    """A land-balance deficit is purchased and disclosed at the market price."""
    assumptions = SimulationAssumptions()
    result = run_simulation(assumptions, with_break_even=False)
    assert result.feed_summary.fodder_deficit_months > 0

    cost_mix = next(section for section in result.narrative_report if section.key == "cost_mix")
    land_paragraph = cost_mix.paragraphs[1]

    purchased = sum(result.feed_summary.annual_purchased_green_kg)
    assert purchased > 0.0
    assert f"{purchased:,.0f}" in land_paragraph
    assert "bought at the configured market price" in land_paragraph
    assert "on-farm supply is costed separately" in land_paragraph


def test_fodder_narrative_distinguishes_zero_and_one_deficit_month() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    result = run_simulation(assumptions, with_break_even=False)

    no_deficit = result.model_copy(
        update={
            "feed_summary": result.feed_summary.model_copy(
                update={
                    "fodder_deficit_months": 0,
                    "annual_purchased_green_kg": [0.0],
                }
            )
        }
    )
    no_deficit_costs = next(
        section
        for section in build_narrative_report(assumptions, no_deficit)
        if section.key == "cost_mix"
    )
    assert no_deficit_costs.paragraphs[1].endswith("acre(s) on average.")
    assert "falls short" not in no_deficit_costs.paragraphs[1]

    one_deficit = result.model_copy(
        update={"feed_summary": result.feed_summary.model_copy(update={"fodder_deficit_months": 1})}
    )
    one_deficit_costs = next(
        section
        for section in build_narrative_report(assumptions, one_deficit)
        if section.key == "cost_mix"
    )
    assert "falls short in 1 month(s)" in one_deficit_costs.paragraphs[1]
    assert "bought at the configured market price" in one_deficit_costs.paragraphs[1]


def test_overview_lists_each_single_youngstock_cohort() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    result = run_simulation(assumptions, with_break_even=False)
    cases = [
        ("male_kids", "plus 1 kids, 0 weaners and 0 growers"),
        ("female_weaners", "plus 0 kids, 1 weaners and 0 growers"),
        ("male_weaners", "plus 0 kids, 1 weaners and 0 growers"),
        ("female_growers", "plus 0 kids, 0 weaners and 1 growers"),
    ]

    for field, expected in cases:
        changed = assumptions.model_copy(deep=True)
        setattr(changed.herd, field, 1)
        overview = next(
            section
            for section in build_narrative_report(changed, result)
            if section.key == "overview"
        )
        assert expected in overview.paragraphs[0]


def test_overview_joins_purchase_and_sale_event_summaries() -> None:
    assumptions = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        events=[
            HerdEventAssumptions(
                month=2,
                kind="purchase",
                animal_class="female_kid",
                count=2,
            ),
            HerdEventAssumptions(
                month=3,
                kind="sale",
                animal_class="doe",
                count=1,
            ),
        ],
    )
    result = run_simulation(assumptions, with_break_even=False)
    overview = next(item for item in result.narrative_report if item.key == "overview")

    assert (
        "You have scheduled 2 herd event(s) along the way — buy 2 head and sell 1 head."
        in overview.paragraphs[0]
    )


def test_optimizer_report_never_labels_an_infeasible_plan_recommended() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.optimization.max_candidates = 3
    a.optimization.maximum_project_cost = 0.0
    result = run_simulation(a, with_break_even=False, with_optimization=True)
    assert result.optimization is not None and result.optimization.recommended is None
    section = next(item for item in result.narrative_report if item.key == "optimization")
    assert section.paragraphs == [
        "None of the 3 tested plans met every configured financing, liquidity, capacity and "
        "DSCR constraint. No plan is labelled as recommended; revise the constraints or "
        "economics before acting."
    ]
    assert section.figures == {
        "objective": "balanced",
        "evaluated_candidates": 3.0,
        "feasible_candidates": 0.0,
    }


def test_optimizer_report_exposes_the_complete_recommended_plan() -> None:
    assumptions = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    result = run_simulation(assumptions, with_break_even=False)
    recommended = OptimizationCandidate(
        rank=1,
        starting_does=42,
        starting_bucks=2,
        max_breeding_does=60,
        sale_age_months=10,
        female_retention_fraction=0.65,
        loan_fraction=0.40,
        project_cost=900_000.0,
        capacity_places=80.0,
        projected_peak_head=70.0,
        npv=250_000.0,
        irr=0.20,
        min_dscr=1.35,
        minimum_cash_balance=10_000.0,
        funding_gap=0.0,
        feasible=True,
    )
    result = result.model_copy(
        update={
            "optimization": OptimizationResult(
                objective="npv",
                evaluated_candidates=7,
                feasible_candidates=2,
                baseline=recommended.model_copy(update={"rank": 0}),
                recommended=recommended,
            )
        }
    )

    section = next(
        item for item in build_narrative_report(assumptions, result) if item.key == "optimization"
    )

    assert section.title == "Decision optimization"
    assert section.paragraphs == [
        "Under the npv objective, the highest-ranked feasible plan starts with 42 does and "
        "2 bucks, targets 60 breeding does, sells at 10 months, retains 65.0% of eligible "
        "females and uses 40.0% debt. Its NPV is ₹2.50 lakh with a minimum DSCR of 1.35."
    ]
    assert section.figures == {
        "objective": "npv",
        "evaluated_candidates": 7.0,
        "feasible_candidates": 2.0,
        "recommended_npv": 250_000.0,
        "recommended_starting_does": 42.0,
        "recommended_min_dscr": 1.35,
    }

    no_debt_recommendation = result.model_copy(
        update={
            "optimization": result.optimization.model_copy(
                update={"recommended": recommended.model_copy(update={"min_dscr": None})}
            )
        }
    )
    no_debt_section = next(
        item
        for item in build_narrative_report(assumptions, no_debt_recommendation)
        if item.key == "optimization"
    )
    assert no_debt_section.title == "Decision optimization"
    assert no_debt_section.paragraphs[0].endswith("with a minimum DSCR of N/A.")


def test_payback_explanation_says_never_covers_when_no_payback() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert res.metrics.payback_month is None
    entry = next(e for e in res.metric_explanations if e.key == "payback_month")
    assert "never covers" in entry.explanation
    assert entry.figures["payback_month"] is None
