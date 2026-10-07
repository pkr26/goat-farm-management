"""Normal JSON planner bounds and genuine API admission/month pricing.

Native fixtures create actual owners/farms in PostgreSQL. Plans run through the
unchanged backward engine and CPU ledger. The existing ledger's declared clear
hook isolates tests; no helper, clock, ledger counter or report is substituted.
"""

import copy
import json
import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.api import _run_limits
from app.schemas.planner import BackwardPlanIn

from .conftest import owner_with_farm
from .test_planner_api import default_assumptions
from .type_helpers import JsonObject


@pytest.fixture(autouse=True)
def native_admission_isolation() -> Iterator[None]:
    _run_limits._run_budget.clear()
    _run_limits._farm_run_locks.clear()
    yield
    _run_limits._run_budget.clear()
    _run_limits._farm_run_locks.clear()


def _record(case: str, facts: JsonObject) -> None:
    receipt = os.environ.get("MUTATION_RECEIPT_PATH")
    if receipt:
        path = Path(receipt).with_suffix(".planner-boundaries.json")
        previous = json.loads(path.read_text()) if path.exists() else {}
        previous[case] = facts
        path.write_text(json.dumps(previous, indent=2) + "\n")


def _json_admitted(document: JsonObject) -> tuple[bool, str]:
    try:
        BackwardPlanIn.model_validate_json(json.dumps(document))
    except ValidationError as error:
        return False, str(error)
    return True, ""


async def _document(client: httpx.AsyncClient, owner: dict[str, str]) -> JsonObject:
    assumptions = await default_assumptions(client, owner)
    assumptions["meta"]["start_year_month"] = "2026-01"
    assumptions["meta"]["horizon_months"] = 12
    return {
        "assumptions": assumptions,
        "targets": [{"year_month": "2026-12", "animal_class": "doe", "count": 1.0}],
        "close_gaps": False,
        "risk_runs": 0,
    }


@pytest.mark.parametrize("boundary", ["target-count", "target-list", "risk-runs"])
async def test_normal_json_planner_boundaries(client: httpx.AsyncClient, boundary: str) -> None:
    owner = await owner_with_farm(client, email="planner-json-boundary@farm.in")
    document = await _document(client, owner)
    accepted: list[JsonObject] = []
    rejected: list[JsonObject] = []
    if boundary == "target-count":
        maximum = copy.deepcopy(document)
        maximum["targets"][0]["count"] = 100_000.0
        excessive = copy.deepcopy(maximum)
        excessive["targets"][0]["count"] = 100_000.5
        accepted = [maximum]
        rejected = [excessive]
    elif boundary == "target-list":
        maximum = copy.deepcopy(document)
        maximum["targets"] *= 50
        excessive = copy.deepcopy(maximum)
        excessive["targets"].append(copy.deepcopy(document["targets"][0]))
        empty = copy.deepcopy(document)
        empty["targets"] = []
        accepted = [document, maximum]
        rejected = [empty, excessive]
    else:
        maximum = copy.deepcopy(document)
        maximum["risk_runs"] = 500
        excessive = copy.deepcopy(document)
        excessive["risk_runs"] = 501
        negative = copy.deepcopy(document)
        negative["risk_runs"] = -1
        accepted = [document, maximum]
        rejected = [negative, excessive]
    admitted = [_json_admitted(body) for body in accepted]
    refused = [_json_admitted(body) for body in rejected]
    _record(
        "normal-json-" + boundary,
        {
            "accepted_documents": accepted,
            "rejected_documents": rejected,
            "actual_json_model_admission": admitted,
            "actual_json_model_refusal": refused,
            "scope": "actual public transport DTO with standard JSON, not engine output",
        },
    )
    assert all(result[0] for result in admitted), admitted
    assert all(not result[0] for result in refused), refused


async def test_omitted_risk_replays_are_the_same_zero_risk_plan(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="planner-zero-risk@farm.in")
    document = await _document(client, owner)
    document.pop("risk_runs")
    response = await client.post("/api/planner/plan", json=document, headers=owner)
    spent = _run_limits._run_budget._spent("farm", int(owner["X-Farm-Id"]))
    _record(
        "omitted-risk",
        {
            "request": document,
            "status": response.status_code,
            "wire": response.text,
            "actual_farm_spend": spent,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan"]["probabilities"] is None, (
        "Omitting optional risk replays must keep the documented deterministic-only plan"
    )
    assert spent == 2 * response.json()["horizon_months"], (
        "Omitting risk replays must charge only the two deterministic engine passes"
    )


async def test_final_twenty_year_month_runs_and_prices_its_full_horizon(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="planner-final-month@farm.in")
    document = await _document(client, owner)
    document["targets"][0]["year_month"] = "2045-12"
    response = await client.post("/api/planner/plan", json=document, headers=owner)
    spent = _run_limits._run_budget._spent("farm", int(owner["X-Farm-Id"]))
    _record(
        "final-month",
        {
            "request": document,
            "status": response.status_code,
            "wire": response.text,
            "actual_farm_spend": spent,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["horizon_months"] == 240
    assert body["stage_plan"][-1]["year_month"] == "2045-12"
    assert spent == 2 * body["horizon_months"], (
        "The two deterministic planner passes must charge the actual full report horizon"
    )


async def test_first_month_beyond_twenty_years_rejects_before_native_charge(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="planner-beyond-final-month@farm.in")
    document = await _document(client, owner)
    document["targets"][0]["year_month"] = "2046-01"
    response = await client.post("/api/planner/plan", json=document, headers=owner)
    spent = _run_limits._run_budget._spent("farm", int(owner["X-Farm-Id"]))
    _record(
        "beyond-final-month",
        {
            "request": document,
            "status": response.status_code,
            "wire": response.text,
            "actual_farm_spend": spent,
        },
    )
    assert response.status_code == 422, response.text
    assert "20-year horizon" in response.text
    assert spent == 0, "A plan rejected beyond the horizon must spend no CPU admission units"


@pytest.mark.parametrize("risk_runs", [0, 2])
async def test_native_price_matches_deterministic_and_requested_risk_passes(
    client: httpx.AsyncClient, risk_runs: int
) -> None:
    owner = await owner_with_farm(client, email="planner-native-month-price@farm.in")
    document = await _document(client, owner)
    document["risk_runs"] = risk_runs
    response = await client.post("/api/planner/plan", json=document, headers=owner)
    spent = _run_limits._run_budget._spent("farm", int(owner["X-Farm-Id"]))
    _record(
        "native-price-" + str(risk_runs),
        {
            "request": document,
            "status": response.status_code,
            "wire": response.text,
            "actual_farm_spend": spent,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["horizon_months"] == 12
    assert spent == (2 + risk_runs) * response.json()["horizon_months"], (
        "Risk replays add engine passes to the same actual monthly horizon"
    )


async def test_valid_tiny_dm_fraction_returns_nonfinite_plan_as_422(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="planner-nonfinite-valid-input@farm.in")
    document = await _document(client, owner)
    document["assumptions"]["feed"]["green_dm_pct"] = 5e-324
    admitted = _json_admitted(document)
    assert admitted[0], admitted
    response = await client.post("/api/planner/plan", json=document, headers=owner)
    _record(
        "nonfinite",
        {
            "request": document,
            "actual_valid_json_admission": admitted,
            "status": response.status_code,
            "wire": response.text,
        },
    )
    assert response.status_code == 422, (
        "A schema-valid plan with non-finite derived resources must be a client-input 422",
        response.status_code,
        response.text,
    )
    assert response.json()["detail"] == "These inputs produce non-finite results."
