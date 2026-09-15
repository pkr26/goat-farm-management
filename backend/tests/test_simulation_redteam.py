"""Red-team regression tests for the simulation engine and its API bounds.

Pins the fixes from audit_reports/2026-09-13 (RT-L8-1/2/4, RT-KL-5):

* RT-L8-1 — a near-zero conception rate scales the planner's doe demand into
  the trillions; the purchase materialization is bounded by pure arithmetic
  BEFORE any event object is built, so the exploit dies as a fast ValueError
  (422 through the API) instead of an OOM.
* RT-L8-2 — schema-valid ``conception_rate=0.0`` and ``sex_ratio_female``
  exactly 0 or 1 made the backward requirement chain divide by zero (500);
  both now raise actionable ValueErrors (422).
* RT-KL-5 — the capacity-busy 429 carries a ``Retry-After`` header like the
  budget 429 does.

Engine-level cases are DB-free (part of the mutation-pure set); the API
cases reuse the async client fixtures.
"""

import time

import httpx
import pytest

from app.simulation.assumptions import MAX_HEAD, HerdEventAssumptions, SimulationAssumptions
from app.simulation.backward_planner import PlannerTarget, build_backward_plan
from app.simulation.planner import (
    MAX_PLAN_EVENTS,
    SaleTarget,
    _purchases_from,
    build_plan_report,
    close_gaps,
)

from .conftest import owner_with_farm

START = "2026-01"


def _anchored(**overrides: object) -> SimulationAssumptions:
    assumptions = SimulationAssumptions()
    assumptions.meta.start_year_month = START
    for key, value in overrides.items():
        path, _, leaf = key.rpartition(".")
        obj: object = assumptions
        for part in path.split("."):
            if part:
                obj = getattr(obj, part)
        setattr(obj, leaf, value)
    return assumptions


# ---------------------------------------------------------------------------
# RT-L8-1: purchase-event materialization bomb
# ---------------------------------------------------------------------------
def test_close_gaps_rejects_explosive_demand_before_materializing() -> None:
    """conception_rate=1e-9 against a 100,000-head shortfall demands >1e12
    does; the chunked events would number ~1e9 against the 500-event cap. The
    rejection is arithmetic and fast — no event list is ever built."""
    assumptions = _anchored(
        **{
            "meta.horizon_months": 240,
            "reproduction.conception_rate": 1e-9,
            "herd.does": 0,
            "herd.bucks": 0,
            "herd.auto_purchase_bucks": False,
        }
    )
    targets = [SaleTarget(month=200, animal_class="male_grower", count=100_000.0)]
    started = time.monotonic()
    with pytest.raises(ValueError, match="purchase events; cap is 500"):
        close_gaps(assumptions, targets)
    assert time.monotonic() - started < 5.0


def test_plan_report_propagates_the_event_cap_rejection() -> None:
    """The public entry point (what /api/planner/plan offloads) raises the
    same fast ValueError — the API maps it to 422, never a 500/OOM."""
    assumptions = _anchored(
        **{
            "meta.horizon_months": 240,
            "reproduction.conception_rate": 1e-9,
            "herd.does": 0,
            "herd.bucks": 0,
            "herd.auto_purchase_bucks": False,
        }
    )
    started = time.monotonic()
    with pytest.raises(ValueError, match="raise conception_rate or lower the shortfall"):
        build_plan_report(
            assumptions,
            [SaleTarget(month=200, animal_class="male_grower", count=100_000.0)],
        )
    assert time.monotonic() - started < 5.0


def test_purchases_from_counts_chunks_before_building_them() -> None:
    """501 MAX_HEAD-sized chunks (one more than the event cap) are rejected
    by arithmetic; a demand that fits is chunked unchanged."""
    with pytest.raises(ValueError, match="purchase events; cap is 500"):
        _purchases_from({1: float(MAX_PLAN_EVENTS + 1) * MAX_HEAD})
    events = _purchases_from({1: 2.5 * MAX_HEAD, 3: MAX_HEAD / 4.0})
    assert [event.month for event in events] == [1, 1, 1, 3]
    assert all(event.count <= MAX_HEAD for event in events)


def test_purchase_budget_counts_the_plans_own_events() -> None:
    """A document already carrying purchase events plus sale targets has less
    headroom: 499 kept purchase events + the sale target consume the whole
    500-event budget, so even one purchase chunk must be refused."""
    assumptions = _anchored(**{"meta.horizon_months": 240})
    assumptions.events = [
        # Months must stay inside the horizon; the count, not the spread, is
        # what the reserved-events arithmetic sees.
        HerdEventAssumptions(
            month=2 + (index % 239), kind="purchase", animal_class="doe", count=1.0
        )
        for index in range(499)
    ]
    targets = [SaleTarget(month=200, animal_class="male_grower", count=100_000.0)]
    with pytest.raises(ValueError, match="purchase events; cap is 500"):
        close_gaps(assumptions, targets)


async def test_planner_api_returns_a_fast_422_for_the_event_bomb(
    client: httpx.AsyncClient,
) -> None:
    """End to end: the explosive plan answers 422 quickly instead of eating
    the worker for minutes (measured pre-fix: >6 min, ~500 GB projected)."""
    headers = await owner_with_farm(client, email="bomb@planner.in")
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assert resp.status_code == 200, resp.text
    assumptions = resp.json()
    assumptions["meta"]["start_year_month"] = START
    assumptions["meta"]["horizon_months"] = 240
    assumptions["reproduction"]["conception_rate"] = 1e-9
    assumptions["herd"].update({"does": 0, "bucks": 0, "auto_purchase_bucks": False})
    document = {
        "assumptions": assumptions,
        "targets": [{"year_month": "2042-08", "animal_class": "male_grower", "count": 100_000.0}],
    }
    started = time.monotonic()
    resp = await client.post("/api/planner/plan", json=document, headers=headers)
    assert resp.status_code == 422, resp.text
    assert "purchase events" in resp.json()["detail"]
    assert time.monotonic() - started < 5.0


# ---------------------------------------------------------------------------
# RT-L8-2: backward-planner ZeroDivisionError → 422
# ---------------------------------------------------------------------------
def test_conception_rate_zero_raises_an_actionable_value_error() -> None:
    """conception_rate=0.0 is schema-valid and crashed the requirement chain
    with ZeroDivisionError (a 500); it must refuse with the cause and fix."""
    assumptions = _anchored(
        **{"reproduction.conception_rate": 0.0, "herd.does": 10, "herd.bucks": 1}
    )
    with pytest.raises(ValueError, match=r"conception_rate=0\.0"):
        build_backward_plan(
            assumptions,
            [PlannerTarget(year_month="2027-06", animal_class="male_grower", count=5.0)],
        )


def test_all_female_sex_ratio_with_male_target_raises_value_error() -> None:
    """sex_ratio_female=1.0 yields exactly zero male
    births — the male-share denominator was zero (a 500)."""
    assumptions = _anchored(**{"reproduction.sex_ratio_female": 1.0})
    with pytest.raises(ValueError, match="sex_ratio_female=1"):
        build_backward_plan(
            assumptions,
            [PlannerTarget(year_month="2027-06", animal_class="male_grower", count=5.0)],
        )


def test_all_male_sex_ratio_with_female_target_raises_value_error() -> None:
    """The mirror case: sex_ratio_female=0.0 with a female young-stock
    target."""
    assumptions = _anchored(**{"reproduction.sex_ratio_female": 0.0})
    with pytest.raises(ValueError, match="sex_ratio_female=0"):
        build_backward_plan(
            assumptions,
            [PlannerTarget(year_month="2027-06", animal_class="female_weaner", count=5.0)],
        )


async def test_planner_api_maps_the_zero_denominators_to_422(
    client: httpx.AsyncClient,
) -> None:
    """The three ZeroDivision probes from the report all answer 422 through
    the API, with the actionable message in the detail."""
    headers = await owner_with_farm(client, email="zerodiv@planner.in")
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assert resp.status_code == 200, resp.text
    assumptions = resp.json()
    assumptions["meta"]["start_year_month"] = START
    assumptions["herd"].update({"does": 10, "bucks": 1})
    for mutation, animal_class in (
        ({"reproduction": {"conception_rate": 0.0}}, "male_grower"),
        ({"reproduction": {"sex_ratio_female": 1.0}}, "male_grower"),
        ({"reproduction": {"sex_ratio_female": 0.0}}, "female_weaner"),
    ):
        document = {
            "assumptions": _deep_merged(assumptions, mutation),
            "targets": [{"year_month": "2027-06", "animal_class": animal_class, "count": 5.0}],
        }
        resp = await client.post("/api/planner/plan", json=document, headers=headers)
        assert resp.status_code == 422, (mutation, resp.text)


def _deep_merged(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merged(merged[key], value)
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# RT-L8-4: fodder-yield floor
# ---------------------------------------------------------------------------
def test_fodder_yield_has_an_agronomic_floor() -> None:
    """gt=0.0 admitted 1e-300, which turned the land requirement into 1e301
    acres (or inf). The floor is MIN_FODDER_YIELD_T; the floor itself and
    every sane yield above it still validate at the model boundary (the way
    every API payload and stored document enters)."""
    from pydantic import ValidationError

    from app.simulation.assumptions import MIN_FODDER_YIELD_T

    assert MIN_FODDER_YIELD_T == 0.01

    def _revalidated(yield_t: float) -> SimulationAssumptions:
        mutated = _anchored(**{"feed.fodder_yield_t_dm_per_acre_year": yield_t})
        return SimulationAssumptions.model_validate(mutated.model_dump())

    with pytest.raises(ValidationError, match="fodder_yield_t_dm_per_acre_year"):
        _revalidated(1e-300)
    for sane in (MIN_FODDER_YIELD_T, 0.5, 6.0, 100.0):
        assert _revalidated(sane).feed.fodder_yield_t_dm_per_acre_year == sane


# ---------------------------------------------------------------------------
# RT-KL-5: capacity-busy 429 carries Retry-After
# ---------------------------------------------------------------------------
async def test_capacity_busy_429_carries_retry_after(client: httpx.AsyncClient) -> None:
    """The busy 429 must hint when to come back, like the budget 429 does
    (runs can take ~25 s worst case; an immediate retry just re-429s)."""
    from app.api._run_limits import _farm_run_lock

    headers = await owner_with_farm(client, email="retryafter@ops-sim.in")
    farm_id = int(headers["X-Farm-Id"])
    lock = _farm_run_lock(farm_id)
    await lock.acquire()
    try:
        resp = await client.post(
            "/api/ops-sim/run",
            json={
                "start_date": "2026-09-03",
                "horizon_days": 30,
                "seed": 7,
                "animals": [{"tag": "D1", "sex": "F", "bucket": "BREEDING", "age_months": 18}],
            },
            headers=headers,
        )
        assert resp.status_code == 429, resp.text
        assert "busy" in resp.json()["detail"]
        # Pin the exact documented hint: 5 s clears the per-farm run lock's
        # hold window, so an operator-facing client can schedule the retry.
        assert resp.headers["Retry-After"] == "5"
    finally:
        lock.release()
