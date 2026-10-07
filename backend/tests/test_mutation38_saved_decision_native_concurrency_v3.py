"""Real PostgreSQL row/unique-lock races and current saved-decision semantics.

The alternate writer is an actual native SQL transaction over validated API
rows/documents in the owned migrated database. Server blocking-PID evidence,
not sleep duration, synchronizes races. No ORM/query/clock/result is replaced.
"""

import asyncio
import json
from collections.abc import Iterator
from typing import Literal

import asyncpg
import httpx
import pytest
from sqlalchemy import text

import app.api._run_limits as limits
import app.api.planner as planner_api
import app.api.screening as screening_api
import app.api.simulation as simulation_api
import app.api.team as team_api
from app.db import get_sessionmaker
from app.models import Farm, PlannerPlan, SimulationScenario

from .conftest import TEST_DIRECT_URL, owner_with_farm
from .type_helpers import JsonObject, json_object

Kind = Literal["plan", "scenario"]


@pytest.fixture(autouse=True)
def _clear_owned_test_run_state() -> Iterator[None]:
    limits._run_budget.clear()
    limits._farm_run_locks.clear()
    yield
    limits._run_budget.clear()
    limits._farm_run_locks.clear()


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
    result: JsonObject = {"name": name, "notes": "Reviewed notes", "assumptions": assumptions}
    if kind == "plan":
        result.update(
            start_year_month="2026-01",
            targets=[{"year_month": "2027-01", "animal_class": "male_grower", "count": 1.0}],
        )
    return result


async def _create(client: httpx.AsyncClient, headers: dict[str, str], kind: Kind) -> JsonObject:
    response = await client.post(
        _path(kind), json=await _document(client, headers, kind), headers=headers
    )
    assert response.status_code == 201, response.text
    return json_object(response.json())


async def _server_wait_or_finished(task: asyncio.Task[httpx.Response], holder: int) -> str | None:
    """Observe an actual server dependency on this exact native writer PID."""
    connection = await asyncpg.connect(TEST_DIRECT_URL, timeout=5)
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while not task.done():
            query = await connection.fetchval(
                "SELECT query FROM pg_stat_activity WHERE datname=current_database() "
                "AND $1::int=ANY(pg_blocking_pids(pid)) ORDER BY pid LIMIT 1",
                holder,
                timeout=5,
            )
            if isinstance(query, str):
                return query
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(
                    "No completed request or actual native server dependency observed"
                )
            await asyncio.sleep(0.01)
        return None
    finally:
        await connection.close(timeout=5)


@pytest.mark.parametrize("kind", ["plan", "scenario"])
async def test_saved_decision_read_does_not_acquire_an_unrequested_writer_lock(
    client: httpx.AsyncClient, kind: Kind
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, kind)
    async with get_sessionmaker()() as holder:
        await holder.execute(
            text(f"SELECT id FROM {_table(kind)} WHERE id=:id FOR UPDATE"), {"id": row["id"]}
        )
        pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        task = asyncio.create_task(client.get(f"{_path(kind)}/{row['id']}", headers=headers))
        try:
            blocked = await _server_wait_or_finished(task, pid)
        finally:
            await holder.rollback()
        response = await asyncio.wait_for(task, timeout=10)
    assert response.status_code == 200, response.text
    assert blocked is None


@pytest.mark.parametrize("kind", ["plan", "scenario"])
async def test_locking_fetch_refreshes_real_session_identity_cache(
    client: httpx.AsyncClient, kind: Kind
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, kind)
    async with get_sessionmaker()() as reader:
        cached: PlannerPlan | SimulationScenario
        refreshed: PlannerPlan | SimulationScenario
        if kind == "plan":
            cached = await planner_api._get_plan(
                reader, row["farm_id"], row["id"], for_update=False
            )
        else:
            cached = await simulation_api._get_scenario(
                reader, row["farm_id"], row["id"], for_update=False
            )
        assert cached.revision == 1
        async with get_sessionmaker()() as writer:
            await writer.execute(
                text(f"UPDATE {_table(kind)} SET revision=revision+1, notes=:notes WHERE id=:id"),
                {"notes": "Committed outside this identity cache", "id": row["id"]},
            )
            await writer.commit()
        if kind == "plan":
            refreshed = await planner_api._get_plan(
                reader, row["farm_id"], row["id"], for_update=True
            )
        else:
            refreshed = await simulation_api._get_scenario(
                reader, row["farm_id"], row["id"], for_update=True
            )
        try:
            actual_revision, actual_notes = refreshed.revision, refreshed.notes
        finally:
            await reader.rollback()
    assert actual_revision == 2
    assert actual_notes == "Committed outside this identity cache"


@pytest.mark.parametrize("kind", ["plan", "scenario"])
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_saved_decision_racing_revision_is_checked_after_row_lock(
    client: httpx.AsyncClient, kind: Kind, operation: str
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, kind)
    async with get_sessionmaker()() as holder:
        await holder.execute(
            text(f"SELECT id FROM {_table(kind)} WHERE id=:id FOR UPDATE"), {"id": row["id"]}
        )
        pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        url = f"{_path(kind)}/{row['id']}"
        if operation == "update":
            task = asyncio.create_task(
                client.patch(
                    url,
                    json={"expected_revision": 1, "notes": "Stale tab replacement"},
                    headers=headers,
                )
            )
        else:
            task = asyncio.create_task(
                client.delete(url, params={"expected_revision": 1}, headers=headers)
            )
        try:
            await _server_wait_or_finished(task, pid)
            await holder.execute(
                text(f"UPDATE {_table(kind)} SET revision=revision+1, notes=:notes WHERE id=:id"),
                {"notes": "Accepted racing writer", "id": row["id"]},
            )
            await holder.commit()
        finally:
            await holder.rollback()
        response = await asyncio.wait_for(task, timeout=10)
    assert response.status_code == 409, response.text
    retained = await client.get(url, headers=headers)
    assert retained.status_code == 200, retained.text
    assert retained.json()["revision"] == 2 and retained.json()["notes"] == "Accepted racing writer"


async def test_saved_scenario_run_captures_revision_after_the_native_row_lock(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, "scenario")
    async with get_sessionmaker()() as holder:
        await holder.execute(
            text("SELECT id FROM simulation_scenarios WHERE id=:id FOR UPDATE"), {"id": row["id"]}
        )
        pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        task = asyncio.create_task(
            client.post(f"{_path('scenario')}/{row['id']}/run", headers=headers)
        )
        try:
            await _server_wait_or_finished(task, pid)
            await holder.execute(
                text("UPDATE simulation_scenarios SET revision=revision+1 WHERE id=:id"),
                {"id": row["id"]},
            )
            await holder.commit()
        finally:
            await holder.rollback()
        response = await asyncio.wait_for(task, timeout=10)
    assert response.status_code == 200, response.text
    assert response.json()["executed_scenario_revision"] == 2


@pytest.mark.parametrize("kind", ["plan", "scenario"])
@pytest.mark.parametrize("operation", ["create", "rename"])
async def test_actual_unique_index_race_returns_a_domain_bad_request(
    client: httpx.AsyncClient, kind: Kind, operation: str
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, kind)
    async with get_sessionmaker()() as holder:
        columns = "farm_id,name,notes,assumptions,created_by_id"
        selected = "farm_id,:name,notes,assumptions,created_by_id"
        if kind == "plan":
            columns += ",start_year_month,targets"
            selected += ",start_year_month,targets"
        # Genuine auto-generated ID/default timestamps and all actual FK/JSONB
        # constraints remain active. Only the valid pending name differs.
        await holder.execute(
            text(
                f"INSERT INTO {_table(kind)} ({columns}) SELECT {selected} "
                f"FROM {_table(kind)} WHERE id=:id"
            ),
            {"id": row["id"], "name": "Native pending duplicate"},
        )
        pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        if operation == "create":
            document = await _document(client, headers, kind, "Native pending duplicate")
            task = asyncio.create_task(client.post(_path(kind), json=document, headers=headers))
        else:
            task = asyncio.create_task(
                client.patch(
                    f"{_path(kind)}/{row['id']}",
                    json={"name": "Native pending duplicate", "expected_revision": 1},
                    headers=headers,
                )
            )
        try:
            await _server_wait_or_finished(task, pid)
            await holder.commit()
        finally:
            await holder.rollback()
        response = await asyncio.wait_for(task, timeout=10)
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == f"A {kind} with that name already exists."


async def test_saved_scenario_noop_preserves_its_reviewed_revision(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, "scenario")
    response = await client.patch(
        f"{_path('scenario')}/{row['id']}", json={"expected_revision": 1}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1


async def test_retained_plan_list_accepts_jsonb_valid_legacy_target_validation_failure(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    row = await _create(client, headers, "plan")
    async with get_sessionmaker()() as db:
        await db.execute(
            text("UPDATE planner_plans SET targets=:targets WHERE id=:id"),
            {
                "targets": json.dumps(
                    [{"year_month": "2027-01", "animal_class": "male_grower", "count": -1.0}]
                ),
                "id": row["id"],
            },
        )
        await db.commit()
    response = await client.get(_path("plan"), headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["valid"] is False


@pytest.mark.parametrize("kind", ["plan", "scenario"])
async def test_saved_decision_create_is_independent_of_other_native_workflow_quota_locks(
    client: httpx.AsyncClient, kind: Kind
) -> None:
    headers = await owner_with_farm(client)
    document = await _document(client, headers, kind)
    async with get_sessionmaker()() as holder:
        farm = await holder.get(Farm, int(headers["X-Farm-Id"]))
        assert farm is not None
        if kind == "plan":
            await screening_api._lock_farm_intake(holder, farm.id)
        else:
            await team_api._lock_farm_provisioning(holder, farm)
        pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        task = asyncio.create_task(client.post(_path(kind), json=document, headers=headers))
        try:
            blocked = await _server_wait_or_finished(task, pid)
        finally:
            await holder.rollback()
        response = await asyncio.wait_for(task, timeout=10)
    assert response.status_code == 201, response.text
    assert blocked is None


@pytest.mark.parametrize("kind", ["plan", "scenario"])
async def test_invalid_zero_identifier_is_rejected_before_saved_table_access(
    client: httpx.AsyncClient, kind: Kind
) -> None:
    headers = await owner_with_farm(client)
    async with get_sessionmaker()() as holder:
        # Genuine PostgreSQL maintenance/table lock. An invalid identifier
        # has no resource to fetch and must be rejected before touching this
        # table, even while that table is unavailable for ordinary reads.
        await holder.execute(text(f"LOCK TABLE {_table(kind)} IN ACCESS EXCLUSIVE MODE"))
        pid = (await holder.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        task = asyncio.create_task(client.get(f"{_path(kind)}/0", headers=headers))
        try:
            blocked = await _server_wait_or_finished(task, pid)
        finally:
            await holder.rollback()
        response = await asyncio.wait_for(task, timeout=10)
    assert response.status_code == 404, response.text
    assert blocked is None
