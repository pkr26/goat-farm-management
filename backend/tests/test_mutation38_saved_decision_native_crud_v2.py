"""Saved-decision CRUD, retained-document recovery, and detached run snapshots.

Every row is produced by the native API. Retained legacy JSON is written to
the real JSONB columns in the owned migrated PostgreSQL fixture. No ORM getter,
engine, response, identifier, or validation implementation is replaced.
"""

import json
from typing import Literal

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text

import app.api.planner as planner_api
import app.api.simulation as simulation_api
from app.db import get_sessionmaker
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation

from .conftest import owner_with_farm
from .type_helpers import JsonObject, json_object

Kind = Literal["plan", "scenario"]


def _path(kind: Kind) -> str:
    return "/api/planner/plans" if kind == "plan" else "/api/simulation/scenarios"


def _table(kind: Kind) -> str:
    return "planner_plans" if kind == "plan" else "simulation_scenarios"


async def _document(
    client: httpx.AsyncClient, headers: dict[str, str], kind: Kind, name: str = "Reviewed A"
) -> JsonObject:
    defaults = await client.get("/api/simulation/defaults", headers=headers)
    assert defaults.status_code == 200, defaults.text
    assumptions = json_object(defaults.json())
    assumptions["meta"] = {"horizon_months": 12, "start_year_month": "2026-01"}
    assumptions["risk"]["monte_carlo_runs"] = 2
    assumptions["optimization"]["max_candidates"] = 1
    result: JsonObject = {"name": name, "notes": "Reviewed notes", "assumptions": assumptions}
    if kind == "plan":
        result.update(
            start_year_month="2026-01",
            targets=[{"year_month": "2027-01", "animal_class": "male_grower", "count": 1.0}],
        )
    return result


async def _create(
    client: httpx.AsyncClient, headers: dict[str, str], kind: Kind, name: str = "Reviewed A"
) -> JsonObject:
    response = await client.post(
        _path(kind), json=await _document(client, headers, kind, name), headers=headers
    )
    assert response.status_code == 201, response.text
    return json_object(response.json())


@pytest.mark.parametrize("kind", ["plan", "scenario"])
async def test_saved_decision_whitespace_name_has_bad_request_status(
    client: httpx.AsyncClient, kind: Kind
) -> None:
    headers = await owner_with_farm(client)
    document = await _document(client, headers, kind, " \t ")
    rejected = await client.post(_path(kind), json=document, headers=headers)
    assert rejected.status_code == 400, rejected.text
    row = await _create(client, headers, kind)
    rejected = await client.patch(
        f"{_path(kind)}/{row['id']}",
        json={"expected_revision": row["revision"], "name": " \t "},
        headers=headers,
    )
    assert rejected.status_code == 400, rejected.text
    retained = await client.get(f"{_path(kind)}/{row['id']}", headers=headers)
    assert retained.status_code == 200, retained.text
    assert retained.json()["revision"] == row["revision"]
    assert retained.json()["name"] == row["name"]


@pytest.mark.parametrize("kind", ["plan", "scenario"])
async def test_saved_decision_name_preflight_detects_the_first_native_row(
    client: httpx.AsyncClient, kind: Kind
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, kind)
    status: int | None = None
    async with get_sessionmaker()() as db:
        try:
            if kind == "plan":
                await planner_api._check_name_free(db, row["farm_id"], row["name"])
            else:
                await simulation_api._check_name_free(db, row["farm_id"], row["name"])
        except HTTPException as exc:
            status = exc.status_code
        await db.rollback()
    assert status == 400
    duplicate = await client.post(
        _path(kind), json=await _document(client, headers, kind, row["name"]), headers=headers
    )
    assert duplicate.status_code == 400, duplicate.text


@pytest.mark.parametrize("kind", ["plan", "scenario"])
async def test_saved_decision_names_are_scoped_and_self_rename_is_allowed(
    client: httpx.AsyncClient, kind: Kind
) -> None:
    first = await owner_with_farm(client, email="first-decision@farm.in")
    second = await owner_with_farm(client, email="second-decision@farm.in")
    a = await _create(client, first, kind, "Shared farm name")
    b = await _create(client, second, kind, "Shared farm name")
    assert a["farm_id"] != b["farm_id"]
    response = await client.patch(
        f"{_path(kind)}/{a['id']}", json={"name": a["name"], "expected_revision": 1}, headers=first
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    assert response.json()["farm_id"] == a["farm_id"]


@pytest.mark.parametrize("kind", ["plan", "scenario"])
async def test_saved_decision_legacy_revision_bridge_allows_exactly_one_write(
    client: httpx.AsyncClient, kind: Kind
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, kind)
    response = await client.patch(
        f"{_path(kind)}/{row['id']}", json={"notes": "First reviewed replacement"}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    response = await client.patch(
        f"{_path(kind)}/{row['id']}", json={"notes": "Stale replacement"}, headers=headers
    )
    assert response.status_code == 409, response.text


async def test_saved_plan_noop_preserves_reviewed_revision(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, "plan")
    response = await client.patch(
        f"{_path('plan')}/{row['id']}", json={"expected_revision": 1}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    assert response.json()["updated_at"] == row["updated_at"]


@pytest.mark.parametrize("field", ["name", "notes", "targets", "assumptions", "anchor"])
async def test_saved_plan_each_changed_document_advances_revision(
    client: httpx.AsyncClient, field: str
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, "plan")
    payload: JsonObject = {"expected_revision": 1}
    if field == "name":
        payload["name"] = "Reviewed B"
    elif field == "notes":
        payload["notes"] = "New reviewed note"
    elif field == "targets":
        payload["targets"] = [
            {"year_month": "2027-02", "animal_class": "male_grower", "count": 2.0}
        ]
    elif field == "anchor":
        payload["start_year_month"] = "2026-02"
    else:
        assumptions = json_object(row["assumptions"])
        assumptions["herd"]["does"] = 49
        assumptions["meta"]["start_year_month"] = "2027-05"
        payload["assumptions"] = assumptions
    response = await client.patch(f"{_path('plan')}/{row['id']}", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    saved = json_object(response.json())
    assert saved["revision"] == 2
    assert saved["assumptions"]["meta"]["start_year_month"] == saved["start_year_month"]
    if field != "anchor":
        assert saved["start_year_month"] == "2026-01"
    else:
        assert saved["start_year_month"] == "2026-02"
    if field == "assumptions":
        assert saved["assumptions"]["herd"]["does"] == 49


@pytest.mark.parametrize(
    "corruption", ["targets-json", "assumptions-json", "anchor", "empty-targets"]
)
async def test_retained_plan_is_listed_recoverably_and_rejected_for_detail(
    client: httpx.AsyncClient, corruption: str
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, "plan")
    columns = {
        "targets-json": (
            "targets",
            '[{"year_month":"2027-01","animal_class":"male_grower","count":-1.0}]',
        ),
        "assumptions-json": ("assumptions", '{"herd":{"does":-1}}'),
        "anchor": ("start_year_month", "2026-02"),
        "empty-targets": ("targets", "[]"),
    }
    column, value = columns[corruption]
    async with get_sessionmaker()() as db:
        await db.execute(
            text(f"UPDATE planner_plans SET {column}=:value WHERE id=:id"),
            {"value": value, "id": row["id"]},
        )
        await db.commit()
    detail = await client.get(f"{_path('plan')}/{row['id']}", headers=headers)
    assert detail.status_code == 422, detail.text
    listed = await client.get(_path("plan"), headers=headers)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1
    retained = listed.json()["items"][0]
    assert retained["valid"] is False
    assert retained["targets"] is None and retained["assumptions"] is None
    assert retained["validation_error"]
    updated = await client.patch(
        f"{_path('plan')}/{row['id']}",
        json={"notes": "Legacy row reviewed", "expected_revision": 1},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["valid"] is False and updated.json()["revision"] == 2


async def test_retained_scenario_notes_write_returns_committed_invalid_state(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, "scenario")
    async with get_sessionmaker()() as db:
        await db.execute(
            text("UPDATE simulation_scenarios SET assumptions=:value WHERE id=:id"),
            {"id": row["id"], "value": '{"herd":{"does":-1}}'},
        )
        await db.commit()
    response = await client.patch(
        f"{_path('scenario')}/{row['id']}",
        json={"notes": "Legacy row reviewed", "expected_revision": 1},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2 and response.json()["valid"] is False
    assert response.json()["validation_error"]


def test_plan_anchor_normalization_preserves_the_callers_validated_document() -> None:
    document = json.loads(SimulationAssumptions().model_dump_json())
    document["meta"]["start_year_month"] = "2026-01"
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(document))
    original = assumptions.model_dump_json()
    serialized = planner_api._assumptions_json_at_anchor(assumptions, "2027-03")
    normalized = SimulationAssumptions.model_validate_json(serialized)
    assert normalized.meta.start_year_month == "2027-03"
    assert assumptions.model_dump_json() == original


def test_executed_run_assumptions_are_a_detached_document_snapshot() -> None:
    document = json.loads(SimulationAssumptions().model_dump_json())
    document["meta"] = {"horizon_months": 12, "start_year_month": "2026-01"}
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(document))
    result = run_simulation(
        assumptions, with_monte_carlo=False, with_sensitivity=False, with_optimization=False
    )
    assert result.executed_assumptions is not None
    retained = result.executed_assumptions.model_dump_json()
    assumptions.herd.does = 49
    assert result.executed_assumptions.model_dump_json() == retained


@pytest.mark.parametrize("kind", ["plan", "scenario"])
async def test_last_postgresql_generated_decision_identifier_is_readable(
    client: httpx.AsyncClient, kind: Kind
) -> None:
    headers = await owner_with_farm(client)
    # Advance only the genuine sequence in this owned, freshly migrated fixture.
    # The API/INSERT produces INT_MAX naturally; no stored ID is rewritten.
    async with get_sessionmaker()() as db:
        await db.execute(
            text("SELECT setval(pg_get_serial_sequence(:table, 'id'), 2147483646, true)"),
            {"table": _table(kind)},
        )
        await db.commit()
    row = await _create(client, headers, kind)
    assert row["id"] == 2147483647
    response = await client.get(f"{_path(kind)}/{row['id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == row["id"]
