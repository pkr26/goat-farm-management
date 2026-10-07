"""Native HTTP execution flags, compare admission/charges, and overflow recovery."""

import json
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import text

import app.api._run_limits as limits
import app.api.simulation as simulation_api
from app.db import get_sessionmaker
from app.simulation.assumptions import SimulationAssumptions

from .conftest import owner_with_farm
from .type_helpers import JsonObject, json_object


@pytest.fixture(autouse=True)
def _clear_owned_test_run_state() -> Iterator[None]:
    """Same transparent state cleanup as the existing simulation API tests."""
    limits._run_budget.clear()
    limits._farm_run_locks.clear()
    yield
    limits._run_budget.clear()
    limits._farm_run_locks.clear()


async def _assumptions(client: httpx.AsyncClient, headers: dict[str, str]) -> JsonObject:
    response = await client.get("/api/simulation/defaults", headers=headers)
    assert response.status_code == 200, response.text
    document = json_object(response.json())
    document["meta"] = {"horizon_months": 12, "start_year_month": "2026-01"}
    document["risk"]["monte_carlo_runs"] = 2
    document["optimization"]["max_candidates"] = 1
    return document


async def _scenario(
    client: httpx.AsyncClient, headers: dict[str, str], assumptions: JsonObject, name: str
) -> JsonObject:
    response = await client.post(
        "/api/simulation/scenarios",
        json={"name": name, "assumptions": assumptions},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return json_object(response.json())


async def test_scenario_compare_spends_only_the_native_deterministic_request_cost(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    assumptions = await _assumptions(client, headers)
    first = await _scenario(client, headers, assumptions, "Deterministic A")
    second = await _scenario(client, headers, assumptions, "Deterministic B")
    response = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": f"{first['id']},{second['id']},{first['id']}"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = json_object(response.json())
    assert [row["id"] for row in body["scenarios"]] == [first["id"], second["id"]]
    assert len(body["results"]) == 2
    assert all(
        result["monte_carlo"] is None
        and result["sensitivity"] is None
        and result["optimization"] is None
        for result in body["results"]
    )
    # Read the genuine ledger produced by this actual HTTP request. The same
    # unchanged production pricing function prices the two requests that ran;
    # omitted analysis must not be charged, including on a deduplicated ID.
    actual = SimulationAssumptions.model_validate_json(json.dumps(assumptions))
    deterministic_cost = simulation_api._run_cost(actual, False, False, False)
    assert limits._run_budget._totals[("farm", first["farm_id"])] == 2 * deterministic_cost


async def test_saved_scenario_default_run_omits_every_optional_analysis(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    row = await _scenario(client, headers, await _assumptions(client, headers), "Default run")
    response = await client.post(f"/api/simulation/scenarios/{row['id']}/run", headers=headers)
    assert response.status_code == 200, response.text
    result = json_object(response.json())
    assert result["monte_carlo"] is None
    assert result["sensitivity"] is None
    assert result["optimization"] is None
    assert result["executed_scenario_revision"] == row["revision"]


async def test_compare_admits_five_distinct_scenarios_and_rejects_six(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    assumptions = await _assumptions(client, headers)
    rows = [await _scenario(client, headers, assumptions, f"Compare {index}") for index in range(6)]
    response = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": ",".join(str(row["id"]) for row in rows[:5])},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["results"]) == 5
    response = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": ",".join(str(row["id"]) for row in rows)},
        headers=headers,
    )
    assert response.status_code == 400, response.text


async def test_compare_rejects_zero_as_invalid_input_before_resource_lookup(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    response = await client.get(
        "/api/simulation/scenarios/compare", params={"ids": "0"}, headers=headers
    )
    assert response.status_code == 400, response.text


async def test_compare_accepts_the_last_natively_generated_postgresql_identifier(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    async with get_sessionmaker()() as db:
        await db.execute(
            text("SELECT setval(pg_get_serial_sequence(:table, 'id'), 2147483646, true)"),
            {"table": "simulation_scenarios"},
        )
        await db.commit()
    row = await _scenario(client, headers, await _assumptions(client, headers), "Integer boundary")
    assert row["id"] == 2147483647
    response = await client.get(
        "/api/simulation/scenarios/compare", params={"ids": str(row["id"])}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["scenarios"][0]["id"] == row["id"]


@pytest.mark.parametrize("fraction", ["green_dm_pct", "dry_dm_pct", "concentrate_dm_pct"])
async def test_valid_subnormal_dry_matter_fraction_has_a_recoverable_overflow_response(
    client: httpx.AsyncClient, fraction: str
) -> None:
    headers = await owner_with_farm(client)
    assumptions = await _assumptions(client, headers)
    assumptions["feed"][fraction] = 5e-324
    # This is a genuine JSON-admitted positive finite fraction. Actual engine
    # division produces nonfinite output; no engine/model/result is replaced.
    admitted = SimulationAssumptions.model_validate_json(json.dumps(assumptions))
    assert getattr(admitted.feed, fraction) > 0.0
    response = await client.post(
        "/api/simulation/run",
        json={
            "assumptions": assumptions,
            "monte_carlo": False,
            "sensitivity": False,
            "optimization": False,
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "These inputs produce non-finite results."
