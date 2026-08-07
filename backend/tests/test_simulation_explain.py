"""Tests for the explainability layer (app/simulation/explain.py).

Every simulation result carries 11 metric explanations and a 6-section
narrative report, generated deterministically from the run's own numbers.
Runs use ``with_break_even=False`` unless the metric under test needs the
bisection, to keep the file cheap.
"""

from typing import cast

import pytest

from app.simulation import MetaAssumptions, SimulationAssumptions, SimulationResult, run_simulation

METRIC_KEYS = {
    "project_cost",
    "loan_amount",
    "subsidy_amount",
    "equity",
    "npv",
    "irr",
    "bcr",
    "avg_dscr",
    "min_dscr",
    "payback_month",
    "break_even_meat_price_per_kg",
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


def test_every_run_has_all_explanations_and_sections() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert {e.key for e in res.metric_explanations} == METRIC_KEYS
    assert len(res.metric_explanations) == 11
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
    assert by_key["bcr"]["bcr"] == pytest.approx(m.bcr)
    assert by_key["avg_dscr"]["avg_dscr"] == pytest.approx(m.avg_dscr)
    assert by_key["min_dscr"]["min_dscr"] == pytest.approx(m.min_dscr)
    assert by_key["payback_month"]["payback_month"] == m.payback_month
    assert by_key["break_even_meat_price_per_kg"]["assumed_meat_price_per_kg"] == 350.0


def test_project_cost_explanation_figures_sum_to_project_cost() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    figures = next(e for e in res.metric_explanations if e.key == "project_cost").figures
    component_sum = sum(
        cast(float, figures[key])
        for key in ("shed_cost", "equipment_cost", "stock_cost", "working_capital")
    )
    assert component_sum == pytest.approx(figures["project_cost"])
    assert component_sum == pytest.approx(res.metrics.project_cost)
    b = res.project_cost_breakdown
    assert figures["shed_cost"] == pytest.approx(b.shed_cost)
    assert figures["working_capital"] == pytest.approx(b.working_capital)


def test_verdict_not_viable_for_default_run() -> None:
    # The default Osmanabadi unit is NPV-negative at a 12% discount rate.
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert res.metrics.npv <= 0.0
    assert _verdict(res) == "NOT VIABLE"


def test_verdict_viable_when_all_checks_pass() -> None:
    a = SimulationAssumptions()
    a.sales.meat_price_per_kg = 500.0
    res = run_simulation(a, with_break_even=False)
    m = res.metrics
    # All hard and soft checks pass: npv > 0, bcr >= 1, irr >= discount rate,
    # min_dscr outside (0, 1), payback present.
    assert m.npv > 0.0 and m.bcr >= 1.0
    assert m.irr is not None and m.irr >= a.finance.discount_rate_annual
    assert m.payback_month is not None
    assert not 0.0 < m.min_dscr < 1.0
    assert _verdict(res) == "VIABLE"


def test_verdict_viable_with_caution_for_borderline_run() -> None:
    # Positive NPV but the weakest debt year cannot cover its repayment
    # (min_dscr in (0, 1)): viable, with a warning.
    a = SimulationAssumptions()
    a.herd.male_growers = 60  # early meat revenue keeps year-1 EBITDA positive
    a.finance.moratorium_months = 0  # full EMI from month 1 squeezes year-1 DSCR
    a.sales.meat_price_per_kg = 500.0
    res = run_simulation(a, with_break_even=False)
    m = res.metrics
    assert m.npv > 0.0 and m.bcr >= 1.0
    assert 0.0 < m.min_dscr < 1.0
    assert _verdict(res) == "VIABLE WITH CAUTION"
    section = next(s for s in res.narrative_report if s.key == "viability_verdict")
    assert any("DSCR below 1.0" in p for p in section.paragraphs)


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
    # Sensitivity movers are named; meat price dominates the tornado ranking.
    assert any("move NPV the most" in p for p in risks.paragraphs)
    assert any("meat price" in p for p in risks.paragraphs)
    assert "meat_price" in str(risks.figures["top_sensitivities"])


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
    assert cost["total_opex"] == pytest.approx(sum(r.total_opex for r in res.annual_pl))


def test_payback_explanation_says_never_covers_when_no_payback() -> None:
    res = run_simulation(SimulationAssumptions(), with_break_even=False)
    assert res.metrics.payback_month is None
    entry = next(e for e in res.metric_explanations if e.key == "payback_month")
    assert "never covers" in entry.explanation
    assert entry.figures["payback_month"] is None
