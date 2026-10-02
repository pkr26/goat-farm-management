"""Regression tests for the 2026-10-01 audit, track 08 (simulation engine &
concurrency).

Each test cites its finding as "(2026-10-01 audit, 08-…)" and would have
failed against the pre-audit code for the behavioral fixes. A few findings
(M4 boundary pricing, L14 creep-band representative age) are numeric-neutral
hardenings — their tests pin the invariant the audit asked to hold and are
marked as such.
"""

import asyncio
import calendar
from datetime import date
from typing import Any

import httpx
import pytest

import app.api._run_limits as run_limits
import app.api.simulation as simulation_api
from app.api.simulation import _BREAK_EVEN_PASSES, _SENSITIVITY_PASSES, _run_cost
from app.schemas.common import MAX_INT32_ID
from app.services.simulation_calibration import _class_boundary
from app.simulation import MetaAssumptions, SimulationAssumptions, run_monte_carlo, run_simulation
from app.simulation.assumptions import (
    GrowthAssumptions,
    HerdAssumptions,
    ReproductionAssumptions,
)
from app.simulation.daily_ops import (
    AnimalStartSpec,
    DailyOpsInput,
    DailyOpsParams,
    DailyOpsResult,
    DailyOpsResultCapExceeded,
    run_daily_ops,
)
from app.simulation.engine import (
    BREAK_EVEN_BISECTION_STEPS,
    male_weight_at_age,
    weight_at_age,
)
from app.simulation.engine import (
    BREAK_EVEN_PASSES as ENGINE_BREAK_EVEN_PASSES,
)
from app.simulation.feed import DAYS_PER_MONTH
from app.simulation.montecarlo import (
    SENSITIVITY_PARAMETER_COUNT,
    _sensitivity_cases,
)
from app.simulation.montecarlo import (
    SENSITIVITY_PASSES as ENGINE_SENSITIVITY_PASSES,
)
from app.simulation.planner import SaleTarget, _marginal_kids_per_doe
from app.utils import add_months

from .conftest import owner_with_farm


# ---------------------------------------------------------------------------
# 08-H1 — prob_dscr_below_one is nullable exactly like min_dscr
# ---------------------------------------------------------------------------
def test_mc_dscr_probability_is_none_when_no_run_is_measurable() -> None:
    """The audit's repro: a 12-month horizon under the default 12-month
    moratorium has no principal-repaying year, so the deterministic min_dscr
    is None — and every Monte Carlo run of the same shape is unmeasurable
    too. The MC statistic must be None (unmeasurable), never 0.0 (reads as
    "0% chance of a coverage breach" while ₹182,997 of year-1 interest sits
    uncovered by −₹366,850 of EBITDA)."""
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.risk.monte_carlo_runs = 6
    deterministic = run_simulation(a, with_break_even=False)
    assert deterministic.metrics.min_dscr is None
    mc = run_monte_carlo(a)
    assert mc.prob_dscr_below_one is None


def test_mc_dscr_probability_is_a_fraction_when_measurable() -> None:
    a = SimulationAssumptions()  # 120-month horizon: repaying years exist
    a.risk.monte_carlo_runs = 5
    deterministic = run_simulation(a, with_break_even=False)
    assert deterministic.metrics.min_dscr is not None
    mc = run_monte_carlo(a)
    assert mc.prob_dscr_below_one is not None
    assert 0.0 <= mc.prob_dscr_below_one <= 1.0


def test_unmeasurable_mc_dscr_probability_reaches_the_narrative() -> None:
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.risk.monte_carlo_runs = 4
    res = run_simulation(a, with_break_even=False, with_monte_carlo=True)
    risks = next(section for section in res.narrative_report if section.key == "risks")
    assert risks.figures["prob_dscr_below_one"] is None
    assert any("not measurable" in paragraph for paragraph in risks.paragraphs)


async def test_run_endpoint_serializes_unmeasurable_dscr_probability_as_null(
    client: httpx.AsyncClient,
) -> None:
    """End-to-end through the router schema: the wire contract carries null,
    and the measurable shape still carries a number."""
    headers = await owner_with_farm(client)
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assert resp.status_code == 200, resp.text
    assumptions = resp.json()
    assumptions["meta"]["horizon_months"] = 12
    assumptions["risk"]["monte_carlo_runs"] = 6
    resp = await client.post(
        "/api/simulation/run",
        json={"assumptions": assumptions, "monte_carlo": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["monte_carlo"]["prob_dscr_below_one"] is None

    assumptions["meta"]["horizon_months"] = 24
    resp = await client.post(
        "/api/simulation/run",
        json={"assumptions": assumptions, "monte_carlo": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["monte_carlo"]["prob_dscr_below_one"], float)


# ---------------------------------------------------------------------------
# 08-H2 — compare must not hold admission leases across request-scoped DB reads
# ---------------------------------------------------------------------------
async def _make_scenario(
    client: httpx.AsyncClient, headers: dict[str, str], name: str, assumptions: dict[str, Any]
) -> int:
    resp = await client.post(
        "/api/simulation/scenarios",
        json={"name": name, "assumptions": assumptions},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _default_assumptions(
    client: httpx.AsyncClient, headers: dict[str, str], horizon: int = 12
) -> dict[str, Any]:
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assert resp.status_code == 200, resp.text
    assumptions: dict[str, Any] = resp.json()
    assumptions["meta"]["horizon_months"] = horizon
    return assumptions


async def test_compare_holds_no_admission_leases_across_scenario_reads(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scenario fetch/validate phase of compare is request-scoped DB
    work. Parking inside it must leave every admission lease free — before
    the fix, one of only two process-wide simulation slots (plus the farm and
    user locks) was held across the reads."""
    headers = await owner_with_farm(client)
    assumptions = await _default_assumptions(client, headers)
    scenario_id = await _make_scenario(client, headers, "Lease scope plan", assumptions)
    farm_id = int(headers["X-Farm-Id"])
    real_get_scenario = simulation_api._get_scenario
    started = asyncio.Event()
    release = asyncio.Event()

    async def parked_get_scenario(
        db: object, farm: int, scenario_id_arg: int, **kwargs: object
    ) -> object:
        started.set()
        await release.wait()
        return await real_get_scenario(
            db,  # type: ignore[arg-type]
            farm,
            scenario_id_arg,
            **kwargs,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(simulation_api, "_get_scenario", parked_get_scenario)
    request = asyncio.create_task(
        client.get(
            "/api/simulation/scenarios/compare",
            params={"ids": str(scenario_id)},
            headers=headers,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        farm_lock = run_limits._farm_run_locks.get(farm_id)
        assert farm_lock is None or not farm_lock.locked()
        assert not run_limits._global_run_slots.locked()
        # Live proof, not just lock introspection: a concurrent simulation
        # admission is served while the compare is still reading scenarios.
        other = await client.post(
            "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
        )
        assert other.status_code == 200, other.text
    finally:
        release.set()
    response = await asyncio.wait_for(request, timeout=20)
    assert response.status_code == 200, response.text


async def test_compare_results_still_match_a_direct_scenario_run(
    client: httpx.AsyncClient,
) -> None:
    """The refactor is result-preserving: the compare payload's run equals a
    direct /scenarios/{id}/ run of the same assumptions, byte for byte at the
    metric level."""
    headers = await owner_with_farm(client)
    assumptions = await _default_assumptions(client, headers)
    scenario_id = await _make_scenario(client, headers, "Compare parity plan", assumptions)
    compare = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": str(scenario_id)},
        headers=headers,
    )
    direct = await client.post(f"/api/simulation/scenarios/{scenario_id}/run", headers=headers)
    assert compare.status_code == direct.status_code == 200
    compared = compare.json()["results"][0]
    single = direct.json()
    assert compared["metrics"]["npv"] == single["metrics"]["npv"]
    assert compared["metrics"] == single["metrics"]
    assert compared["assumptions_fingerprint"] == single["assumptions_fingerprint"]


# ---------------------------------------------------------------------------
# 08-M3 — daily-ops weaned stock keeps the monthly engine's class hazards
# ---------------------------------------------------------------------------
def _single_head_result(
    spec: AnimalStartSpec, params: DailyOpsParams, horizon_days: int = 7
) -> DailyOpsResult:
    return run_daily_ops(
        DailyOpsInput(
            start_date=date(2026, 1, 1),
            horizon_days=horizon_days,
            seed=2026,
            animals=[spec],
            params=params,
        )
    )


def test_weaned_kid_faces_weaner_phase_hazard_not_adult() -> None:
    weaner = AnimalStartSpec(tag="W-1", sex="M", bucket="MALE_KIDS", age_months=4)
    # Adult mortality maxed, weaner phase zero: the weaned kid must SURVIVE.
    # Before the fix it faced the adult hazard and died on day one.
    survived = _single_head_result(
        weaner, DailyOpsParams(adult_annual_mortality=1.0, kid_post_weaning_mortality=0.0)
    )
    assert survived.journeys[0].exit_kind != "DEAD"
    # Weaner phase maxed, adult zero: it must die.
    died = _single_head_result(
        weaner, DailyOpsParams(adult_annual_mortality=0.0, kid_post_weaning_mortality=1.0)
    )
    assert died.journeys[0].exit_kind == "DEAD"
    assert "Weaner loss" in died.journeys[0].exit_reason


def test_grower_pen_stock_faces_grower_hazard_not_adult() -> None:
    grower = AnimalStartSpec(tag="G-1", sex="M", bucket="MALE_KIDS", age_months=8)
    survived = _single_head_result(
        grower, DailyOpsParams(adult_annual_mortality=1.0, grower_annual_mortality=0.0)
    )
    assert survived.journeys[0].exit_kind != "DEAD"
    died = _single_head_result(
        grower, DailyOpsParams(adult_annual_mortality=0.0, grower_annual_mortality=1.0)
    )
    assert died.journeys[0].exit_kind == "DEAD"
    assert "Grower mortality" in died.journeys[0].exit_reason


def test_dependent_kids_and_adults_keep_their_own_hazards() -> None:
    kid = AnimalStartSpec(tag="K-1", sex="F", bucket="RECOVERY", age_months=1, dependent_kid=True)
    died = _single_head_result(kid, DailyOpsParams(kid_pre_weaning_mortality=1.0))
    assert died.journeys[0].exit_kind == "DEAD"
    assert "Pre-weaning kid loss" in died.journeys[0].exit_reason
    doe = AnimalStartSpec(tag="D-1", sex="F", bucket="BREEDING", age_months=18)
    adult_died = _single_head_result(doe, DailyOpsParams(adult_annual_mortality=1.0))
    assert adult_died.journeys[0].exit_kind == "DEAD"
    assert "Adult mortality" in adult_died.journeys[0].exit_reason


# ---------------------------------------------------------------------------
# 08-M4 — boundary grower event sales priced at the graduation weight
# ---------------------------------------------------------------------------
def test_boundary_grower_event_sale_priced_at_graduation_weight() -> None:
    """Invariant pin (numeric-neutral today): with afb == 6 / sale_age == 6
    the empty grower chain leaves boundary stock, and an event sale of that
    stock must price at weight_at_age(graduation age) — the same weight the
    purchase path and month-1 graduation use. The fallback is derived from
    the chain's emptiness now, so the mid-class placement age can never
    price boundary stock even if the two formulas diverge later."""
    from app.simulation.assumptions import HerdEventAssumptions
    from app.simulation.engine import run_simulation as _run
    from app.simulation.market import meat_price_for_month

    female_boundary = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        reproduction=ReproductionAssumptions(age_at_first_breeding_months=6),
        events=[
            HerdEventAssumptions(month=1, kind="purchase", animal_class="female_grower", count=2),
            HerdEventAssumptions(month=1, kind="sale", animal_class="female_grower", count=2),
        ],
    )
    res = _run(female_boundary, with_break_even=False)
    fill = next(f for f in res.months[0].event_fills if f.kind == "sale")
    g = female_boundary.growth
    expected = weight_at_age(6, g, g.adult_weight_doe_kg) * meat_price_for_month(
        female_boundary.sales, simulation_month=1, calendar_month=8, shock_multiplier=1.0
    )
    assert fill.price_per_head == pytest.approx(expected)

    male_boundary = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        growth=GrowthAssumptions(sale_age_months=6),
        events=[
            HerdEventAssumptions(month=1, kind="purchase", animal_class="male_grower", count=2),
            HerdEventAssumptions(month=1, kind="sale", animal_class="male_grower", count=2),
        ],
    )
    res = _run(male_boundary, with_break_even=False)
    fill = next(f for f in res.months[0].event_fills if f.kind == "sale")
    g = male_boundary.growth
    expected = male_weight_at_age(6, g, g.adult_weight_buck_kg) * meat_price_for_month(
        male_boundary.sales, simulation_month=1, calendar_month=8, shock_multiplier=1.0
    )
    assert fill.price_per_head == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 08-M5 — calibration class windows in one 30.44-day convention
# ---------------------------------------------------------------------------
def test_class_boundaries_share_the_exposure_day_convention() -> None:
    """Class windows and the animal-month denominator must agree: the old
    calendar add_months boundaries made a "3-month" class window 89-92 days
    long depending on the birth month, while the denominator assumed
    3 x 30.44 = 91.32-day months — a per-class annualized mortality bias of
    up to ~±2%."""
    month_ends = [date(2026, month, calendar.monthrange(2026, month)[1]) for month in range(1, 13)]
    # The old, mixed convention really did vary (documents the bug).
    calendar_widths = {(add_months(d, 6) - add_months(d, 3)).days for d in month_ends}
    assert len(calendar_widths) > 1
    # The new boundaries are dob + round(age x DAYS_PER_MONTH) for every dob,
    # so every full class window has one width in the denominator's own
    # convention.
    assert all(
        (_class_boundary(d, age) - d).days == round(age * DAYS_PER_MONTH)
        for d in month_ends
        for age in (3, 6, 12)
    )
    widths = {(_class_boundary(d, 6) - _class_boundary(d, 3)).days for d in month_ends}
    assert widths == {round(6 * DAYS_PER_MONTH) - round(3 * DAYS_PER_MONTH)}


# ---------------------------------------------------------------------------
# 08-M6 — hard output-side cap on the ops-sim result's standing head-days
# ---------------------------------------------------------------------------
def test_result_head_days_cap_aborts_the_run() -> None:
    payload = DailyOpsInput(
        start_date=date(2026, 1, 1),
        horizon_days=30,
        seed=2026,
        animals=[
            AnimalStartSpec(tag="D-1", sex="F", bucket="BREEDING", age_months=18),
            AnimalStartSpec(tag="F-1", sex="F", bucket="FOUNDATION", age_months=20),
        ],
        params=DailyOpsParams(),
    )
    with pytest.raises(DailyOpsResultCapExceeded) as exc_info:
        run_daily_ops(payload, max_result_head_days=30)  # 2 head x 30 days = 60
    assert "Narrow the herd or the horizon" in str(exc_info.value)
    # The same input runs clean without an enforced cap, and a generous cap
    # never rejects an ordinary run.
    assert run_daily_ops(payload).horizon_days == 30
    assert run_daily_ops(payload, max_result_head_days=100_000).horizon_days == 30


# ---------------------------------------------------------------------------
# 08-M7 — default preset framed as stress-conservative (doc-only fix)
# ---------------------------------------------------------------------------
def test_default_preset_documented_as_stress_conservative() -> None:
    import app.simulation.assumptions as assumptions_module
    import app.simulation.defaults as defaults_module

    module_doc = assumptions_module.__doc__ or ""
    preset_doc = defaults_module.osmanabadi.__doc__ or ""
    assert "NABARD-style run" not in module_doc
    assert "stress-conservative" in module_doc
    assert "stress-conservative" in preset_doc
    # The business numbers are deliberately untouched (owner decision
    # deferred): the unit is still the 50+2 Osmanabadi shape.
    base = SimulationAssumptions()
    assert (base.herd.does, base.herd.bucks) == (50, 2)
    assert base.meta.horizon_months == 120


# ---------------------------------------------------------------------------
# 08-L9 — planner marginal-doe mirror charges each month's risk exactly once
# ---------------------------------------------------------------------------
def test_marginal_doe_charges_each_months_risk_exactly_once() -> None:
    """Value-parity golden across the service-month attrition reordering:
    the corrected structure (attrition at the pop, fragments inserted after
    the sweep) reproduces the pre-audit expected mass to the last ulp, while
    the audit's literal patch (multiply at the pop, sweep unchanged) would
    double-charge every service month and land ~1.7% below this value."""
    a = SimulationAssumptions()
    target = SaleTarget(month=18, animal_class="male_grower", count=10)
    assert _marginal_kids_per_doe(a, target, 3) == pytest.approx(0.6112422284002808, abs=1e-12)
    far = SaleTarget(month=30, animal_class="female_weaner", count=5)
    assert _marginal_kids_per_doe(a, far, 1) == pytest.approx(0.35051584412599857, abs=1e-15)


# ---------------------------------------------------------------------------
# 08-L13 — admission pass counts derived from the engine
# ---------------------------------------------------------------------------
def test_run_cost_pass_counts_derive_from_the_engine() -> None:
    assert ENGINE_BREAK_EVEN_PASSES == 2 + BREAK_EVEN_BISECTION_STEPS
    assert ENGINE_SENSITIVITY_PASSES == 1 + 2 * SENSITIVITY_PARAMETER_COUNT
    assert len(_sensitivity_cases()) == SENSITIVITY_PARAMETER_COUNT
    assert _BREAK_EVEN_PASSES == ENGINE_BREAK_EVEN_PASSES
    assert _SENSITIVITY_PASSES == ENGINE_SENSITIVITY_PASSES
    a = SimulationAssumptions(meta=MetaAssumptions(horizon_months=12))
    a.risk.monte_carlo_runs = 5
    expected = (1 + ENGINE_BREAK_EVEN_PASSES + 5 + ENGINE_SENSITIVITY_PASSES) * 12
    assert _run_cost(a, monte_carlo=True, sensitivity=True) == expected


# ---------------------------------------------------------------------------
# 08-L14 — creep bands priced at a representative (mean) member age
# ---------------------------------------------------------------------------
def test_creep_band_kg_is_band_constant_for_mixed_ages() -> None:
    """Invariant pin (numeric-neutral today: creep_daily_kg is constant
    inside a band, so any in-band representative age prices the same kg).
    The whole creep window must produce ONE band line whose daily total is
    heads x the band allowance — the mean-age representative keeps this true
    even if the allowance ever becomes a continuous ramp."""
    payload = DailyOpsInput(
        start_date=date(2026, 1, 1),
        horizon_days=7,
        seed=2026,
        animals=[
            AnimalStartSpec(
                tag="K-A", sex="F", bucket="RECOVERY", age_months=1, dependent_kid=True
            ),
            AnimalStartSpec(
                tag="K-B", sex="F", bucket="RECOVERY", age_months=1, dependent_kid=True
            ),
        ],
        params=DailyOpsParams(kid_pre_weaning_mortality=0.0),
    )
    result = run_daily_ops(payload)
    creep_lines = [
        line for record in result.days for line in record.feeding if line.recipe == "CREEP"
    ]
    assert creep_lines
    assert {line.heads for line in creep_lines} == {2}
    for line in creep_lines:
        assert line.kg_per_head > 0.0
        assert line.daily_kg == pytest.approx(2 * line.kg_per_head, rel=1e-6)


# ---------------------------------------------------------------------------
# 08-L8 — empty-herd cultivation cost (companion to the engine test)
# ---------------------------------------------------------------------------
def test_empty_herd_books_no_cultivation_cost_but_full_grazing_still_does() -> None:
    """The skip is keyed on the PRE-grazing dry-matter requirement, so only a
    herd that consumes nothing at all skips the crop cost; a fully-grazing
    herd with animals still pays for what it grows."""
    from app.simulation.assumptions import FeedAssumptions
    from app.simulation.engine import _run_core

    empty = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=0, bucks=0, auto_purchase_bucks=False),
    )
    res = run_simulation(empty, with_break_even=False)
    assert all(row.feed_cost == 0.0 for row in res.months)

    grazing = SimulationAssumptions(
        meta=MetaAssumptions(horizon_months=12),
        herd=HerdAssumptions(does=2, bucks=0, auto_purchase_bucks=False),
        feed=FeedAssumptions(
            grazing_dm_fraction=1.0,
            green_price_per_kg=2.0,
            dry_price_per_kg=0.0,
            concentrate_price_per_kg=0.0,
            annual_feed_price_growth_rate=0.0,
        ),
    )
    core = _run_core(grazing)
    acres = grazing.feed.cultivated_fodder_acres
    grown_dm = acres * grazing.feed.fodder_yield_t_dm_per_acre_year * 1000.0 / 12.0
    grown_as_fed = grown_dm / grazing.feed.green_dm_pct
    assert core.months[0].feed_cost == pytest.approx(grown_as_fed * grazing.feed.green_price_per_kg)


async def test_scenario_id_bounds_still_guard_compare(client: httpx.AsyncClient) -> None:
    """Unrelated to a finding, but the compare refactor moved the id checks:
    they must still reject out-of-range ids before anything runs."""
    headers = await owner_with_farm(client)
    resp = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": str(MAX_INT32_ID + 1)},
        headers=headers,
    )
    assert resp.status_code == 400
