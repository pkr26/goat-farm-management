"""Simulation input-fuzz regression tests.

Every known crash vector must now fail at validation (422) or as a
documented 400 — never a 500: a bare-string ``meta.start_year_month``
crashed ``int(split("-")[1])``, NaN/inf numerics poisoned runs until JSON
serialization exploded, unbounded herd counts overflowed float arithmetic,
degenerate zero-flow runs emitted ``bcr = inf`` / a bogus bisection-floor
``irr``, and ``RiskVariable`` spreads that didn't bracket the base multiplier
made ``rng.triangular`` draw garbage. The compare endpoint dedupes and caps
ids, and a zero-flow run serializes with null ``bcr``/``irr``.

Follow-up wave: finite-but-huge magnitudes (``1e308`` prices/weights) passed
the NaN/inf guards and overflowed derived math to NaN — every money, weight,
volume and spread input now has a domain cap, lists are length-capped, and
the run endpoint rejects any computed non-finite result with a clean 422.
"""

import copy
import json
import math
from collections.abc import Callable

import httpx

from .conftest import owner_with_farm


async def default_assumptions(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    body["meta"]["horizon_months"] = 12  # keep fuzz runs fast
    return body


async def post_run_raw(
    client: httpx.AsyncClient, assumptions: dict, headers: dict
) -> httpx.Response:
    """POST /run with a manually serialized body: httpx's `json=` refuses
    non-finite floats, but Python's json.dumps emits the non-standard
    `NaN`/`Infinity` tokens verbatim — exactly what an adversary sends."""
    return await client.post(
        "/api/simulation/run",
        content=json.dumps({"assumptions": assumptions}).encode(),
        headers=headers | {"content-type": "application/json"},
    )


async def create_scenario(
    client: httpx.AsyncClient, headers: dict, name: str, assumptions: dict
) -> dict:
    resp = await client.post(
        "/api/simulation/scenarios",
        json={"name": name, "assumptions": assumptions},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# start_year_month: strict YYYY-MM, real month
# ---------------------------------------------------------------------------
async def test_start_year_month_garbage_is_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ("2026", "abc-def", "2026-13", "2026-00", "2026-8", "2026-08-01"):
        assumptions = await default_assumptions(client, headers)
        assumptions["meta"]["start_year_month"] = bad
        resp = await client.post(
            "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
        )
        assert resp.status_code == 422, bad
    # control: a real YYYY-MM still runs
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["start_year_month"] = "2027-01"
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["months"][0]["calendar_month"] == 1


# ---------------------------------------------------------------------------
# NaN / inf numerics (scalar fields, list elements, event payloads, risk)
# ---------------------------------------------------------------------------
async def test_nonfinite_numerics_are_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    defaults = await default_assumptions(client, headers)

    def poisoned(mutate: Callable[[dict], None]) -> dict:
        assumptions = copy.deepcopy(defaults)
        mutate(assumptions)
        return assumptions

    cases: list[Callable[[dict], None]] = [
        lambda a: a["growth"].__setitem__("birth_weight_kg", math.nan),
        lambda a: a["growth"]["weight_by_age_months"].__setitem__(3, math.inf),
        lambda a: a["sales"].__setitem__("meat_price_per_kg", math.inf),
        lambda a: a["herd"].__setitem__("doe_purchase_price", -math.inf),
        lambda a: a["feed"].__setitem__("concentrate_price_per_kg", math.nan),
        lambda a: a["finance"].__setitem__("interest_rate_annual", math.inf),
        lambda a: a["risk"]["meat_price"].__setitem__("low", math.nan),
        lambda a: a["events"].append(
            {"month": 1, "kind": "purchase", "animal_class": "doe", "count": math.nan}
        ),
        lambda a: a["events"].append(
            {
                "month": 1,
                "kind": "sale",
                "animal_class": "doe",
                "count": 1,
                "price_per_head": math.inf,
            }
        ),
    ]
    for case in cases:
        resp = await post_run_raw(client, poisoned(case), headers)
        assert resp.status_code == 422, case
    # control: the un-poisoned payload runs
    resp = await post_run_raw(client, copy.deepcopy(defaults), headers)
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Head counts are capped (10**308 used to OverflowError mid-run)
# ---------------------------------------------------------------------------
async def test_herd_counts_are_capped(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for group, field, value in [
        ("herd", "does", 10**9),
        ("herd", "female_growers", 100_001),
        ("herd", "max_breeding_does", 10**12),
        # This denominator is converted to float by the engine.  Arbitrary-size
        # Pydantic integers used to pass validation and raise OverflowError.
        ("costs", "labour_per_head_threshold", 10**1000),
    ]:
        assumptions = await default_assumptions(client, headers)
        assumptions[group][field] = value
        resp = await client.post(
            "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
        )
        assert resp.status_code == 422, (group, field)
    # 1e308 arrives as a float and overflows any int cap the same way
    assumptions = await default_assumptions(client, headers)
    assumptions["herd"]["does"] = 1e308
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422
    # herd-event counts are capped too
    assumptions = await default_assumptions(client, headers)
    assumptions["events"] = [
        {"month": 1, "kind": "purchase", "animal_class": "doe", "count": 10**9}
    ]
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422
    # control: a big-but-legitimate herd still runs
    assumptions = await default_assumptions(client, headers)
    assumptions["herd"]["does"] = 500
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Magnitude caps: finite-but-huge values are 422 (a 1e308 price used to pass
# validation, overflow derived math to NaN, and crash JSON serialization → 500)
# ---------------------------------------------------------------------------
async def test_extreme_magnitudes_are_422(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    cases: list[tuple[str, str, float]] = [
        ("sales", "meat_price_per_kg", 1e308),  # the exact crash vector
        ("sales", "milk_price_per_litre", 1e308),
        ("sales", "lactation_milk_litres", 1e308),
        ("herd", "doe_purchase_price", 1e308),
        ("costs", "labour_per_month", 1e308),
        ("feed", "concentrate_price_per_kg", 1e15),  # past the ₹1e9 cap
        ("finance", "initial_stock_cost", 2e9),
        ("growth", "adult_weight_doe_kg", 1e308),
    ]
    for group, field, value in cases:
        assumptions = await default_assumptions(client, headers)
        assumptions[group][field] = value
        resp = await client.post(
            "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
        )
        assert resp.status_code == 422, (group, field)
    # weight-table elements, event prices and risk multipliers are capped too
    assumptions = await default_assumptions(client, headers)
    assumptions["growth"]["weight_by_age_months"][3] = 1e308
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422
    assumptions = await default_assumptions(client, headers)
    assumptions["events"] = [
        {
            "month": 1,
            "kind": "sale",
            "animal_class": "doe",
            "count": 1,
            "price_per_head": 1e308,
        }
    ]
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422
    assumptions = await default_assumptions(client, headers)
    assumptions["risk"]["meat_price"] = {"low": 0.5, "high": 1e308}
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422
    # boundary: exactly the ₹1e9 money cap still validates and runs
    assumptions = await default_assumptions(client, headers)
    assumptions["sales"]["meat_price_per_kg"] = 1e9
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text


async def test_derived_overflow_is_a_clean_422_never_500(client: httpx.AsyncClient) -> None:
    """Defense in depth past the input caps: a near-zero fodder yield passes
    validation (gt=0) but makes land_requirement = green_DM / (yield x 1000)
    overflow to inf. The run must answer 422, not 500 on JSON serialization."""
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["feed"]["fodder_yield_t_dm_per_acre_year"] = 1e-320
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    # RT-L8-4: the sub-floor yield is rejected by the schema floor itself.
    assert resp.status_code == 422, resp.text
    assert "greater than or equal to 0.01" in resp.text
    # The derived-overflow defense still holds for a floor-valid yield driven
    # to overflow by the rest of the inputs: 422, never a serialization 500.
    assumptions = await default_assumptions(client, headers)
    assumptions["feed"]["fodder_yield_t_dm_per_acre_year"] = 0.01
    assumptions["herd"]["does"] = 100000
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code in (200, 422), resp.text
    if resp.status_code == 422:
        assert "non-finite" in resp.text


# ---------------------------------------------------------------------------
# List inputs are bounded (huge tables/payloads are a DoS vector)
# ---------------------------------------------------------------------------
async def test_simulation_lists_are_capped(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["growth"]["weight_by_age_months"] = [10.0] * 14
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422
    assumptions = await default_assumptions(client, headers)
    # The curve has one value for each exact age 0..12; accepting longer
    # tables used to bypass the adult-weight branch in the engine.
    assumptions["growth"]["weight_by_age_months"] = [10.0] * 13  # at the cap: runs
    assumptions["growth"]["birth_weight_kg"] = 10.0
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assumptions = await default_assumptions(client, headers)
    assumptions["events"] = [
        {"month": 1, "kind": "purchase", "animal_class": "doe", "count": 1}
    ] * 501
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422
    assumptions = await default_assumptions(client, headers)
    assumptions["events"] = [
        {"month": 1, "kind": "purchase", "animal_class": "doe", "count": 1}
    ] * 500  # at the cap: runs
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text


async def test_risk_variable_must_bracket_mode(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    for bad in ({"low": 1.1, "high": 1.2}, {"low": 0.5, "high": 0.9}):
        assumptions = await default_assumptions(client, headers)
        assumptions["risk"]["meat_price"] = bad
        resp = await client.post(
            "/api/simulation/run",
            json={"assumptions": assumptions, "monte_carlo": True},
            headers=headers,
        )
        assert resp.status_code == 422, bad
    # control: a degenerate-but-valid point spread (low == high == 1) runs
    assumptions = await default_assumptions(client, headers)
    assumptions["risk"]["meat_price"] = {"low": 1.0, "high": 1.0}
    assumptions["risk"]["monte_carlo_runs"] = 3
    resp = await client.post(
        "/api/simulation/run",
        json={"assumptions": assumptions, "monte_carlo": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Degenerate zero-flow run: 200 with null bcr/irr (never inf/−0.99, never 500)
# ---------------------------------------------------------------------------
async def test_zero_flow_run_serializes_null_bcr_irr(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["herd"] |= {
        "does": 0,
        "bucks": 0,
        "max_breeding_does": 0,
        "auto_purchase_bucks": False,
    }
    assumptions["costs"] |= {
        "vet_per_animal_per_year": 0,
        "labour_per_month": 0,
        "misc_overhead_per_month": 0,
        "shed_cost_per_animal_place": 0,
        "equipment_cost_per_animal": 0,
    }
    assumptions["finance"] |= {"loan_fraction_of_project_cost": 0, "working_capital_months": 0}
    # The default now grows fodder (3 acres): cultivation is charged on what
    # is GROWN, so a zero-flow run must zero the acreage too.
    assumptions["feed"] |= {"cultivated_fodder_acres": 0}
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    metrics = resp.json()["metrics"]
    assert metrics["project_cost"] == 0
    assert metrics["npv"] == 0
    assert metrics["bcr"] is None  # no outflows — undefined, not inf
    assert metrics["irr"] is None  # all-zero flows — undefined, not the −0.99 floor


# ---------------------------------------------------------------------------
# Compare: duplicate ids collapse, more than 5 distinct ids is a 400
# ---------------------------------------------------------------------------
async def test_compare_dedupes_and_caps_ids(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    first = await create_scenario(client, headers, "Plan A", assumptions)

    resp = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": f"{first['id']},{first['id']},{first['id']}"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["scenarios"]) == 1  # the duplicate ids ran once
    assert len(body["results"]) == 1

    ids = [first["id"]]
    for i in range(2, 7):
        created = await create_scenario(client, headers, f"Plan {i}", assumptions)
        ids.append(created["id"])
    resp = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": ",".join(str(i) for i in ids)},  # 6 distinct ids
        headers=headers,
    )
    assert resp.status_code == 400
    # 5 distinct ids (one repeated) still compares
    five = [*ids[:5], ids[0]]
    resp = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": ",".join(str(i) for i in five)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["results"]) == 5
    # garbage stays a 400
    resp = await client.get(
        "/api/simulation/scenarios/compare", params={"ids": "a,b"}, headers=headers
    )
    assert resp.status_code == 400
    resp = await client.get(
        "/api/simulation/scenarios/compare", params={"ids": ""}, headers=headers
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# A small Monte Carlo run still returns sane aggregates
# ---------------------------------------------------------------------------
async def test_small_monte_carlo_run_is_sane(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["risk"]["monte_carlo_runs"] = 5
    resp = await client.post(
        "/api/simulation/run",
        json={"assumptions": assumptions, "monte_carlo": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    mc = resp.json()["monte_carlo"]
    assert mc["runs"] == 5
    assert len(mc["herd_percentiles"]["p50"]) == 12
    assert len(mc["cash_percentiles"]["p95"]) == 12
    assert sum(mc["npv_histogram_counts"]) == 5
    assert len(mc["npv_histogram_edges"]) == 21
    assert math.isfinite(mc["npv_mean"]) and math.isfinite(mc["npv_std"])
    assert 0.0 <= mc["prob_npv_negative"] <= 1.0
