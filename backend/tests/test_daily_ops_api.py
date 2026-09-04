"""API tests for the daily operations simulation endpoint.

Contract, RBAC, the goat-only guard, herd-coherence rejections and the
opt-in Markdown ledger — mirroring the planner API suite's shape.
"""

import httpx

from .conftest import create_farm, login, owner_with_farm, register

WORKER_PW = "workerpass123"


def _toy_herd() -> list[dict]:
    does = [
        {"tag": f"D{i}", "sex": "F", "bucket": "BREEDING", "age_months": 18} for i in range(1, 3)
    ]
    return [
        *does,
        {"tag": "B1", "sex": "M", "bucket": "BREEDING", "age_months": 24},
        {"tag": "MK1", "sex": "M", "bucket": "MALE_KIDS", "age_months": 7},
    ]


def _run_document(**extra: object) -> dict:
    document: dict[str, object] = {
        "start_date": "2026-09-03",
        "horizon_days": 30,
        "seed": 7,
        "animals": _toy_herd(),
    }
    document.update(extra)
    return document


def test_ops_sim_api_contract_exists() -> None:
    from app.api.ops_simulation import router

    paths = {route.path for route in router.routes}
    assert "/api/ops-sim/run" in paths


async def test_run_returns_day_by_day_simulation(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post("/api/ops-sim/run", json=_run_document(), headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    result = body["result"]
    assert result["model_version"]
    assert result["start_date"] == "2026-09-03"
    assert result["horizon_days"] == 30
    assert result["head_start"] == 4
    assert len(result["days"]) == 30
    assert body["ledger"] is None  # opt-in
    day1 = result["days"][0]
    assert day1["date"] == "2026-09-03"
    assert any(t["category"] == "FEED" for t in day1["tasks"])
    assert any(t["category"] == "CLEANING" for t in day1["tasks"])
    assert {(row["building"], row["heads"]) for row in day1["occupancy"]} == {
        ("BREEDING", 3),
        ("MALE_KIDS", 1),
    }
    assert result["totals"]["services"] >= 1


async def test_run_include_ledger_opt_in(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/ops-sim/run",
        json=_run_document(include_ledger=True),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    ledger = resp.json()["ledger"]
    assert ledger.startswith("# Buckets & Tasks — daily operations ledger")
    assert "## Day 1 — 2026-09-03" in ledger
    assert "## Transition matrix" in ledger


async def test_run_rejects_dairy_farm(client: httpx.AsyncClient) -> None:
    owner = await register(client, "dairyowner@ops-sim.in")
    headers = await create_farm(client, owner, "Dairy Ops Farm", farm_type="BUFFALO_DAIRY")
    resp = await client.post("/api/ops-sim/run", json=_run_document(), headers=headers)
    assert resp.status_code == 422
    assert "goat farms only" in resp.json()["detail"].lower()


async def test_run_rejects_incoherent_herd(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="incoherent@ops-sim.in")
    document = _run_document(
        animals=[
            {"tag": "D1", "sex": "F", "bucket": "BREEDING", "age_months": 18},
            {"tag": "D1", "sex": "F", "bucket": "BREEDING", "age_months": 18},
        ]
    )
    resp = await client.post("/api/ops-sim/run", json=document, headers=headers)
    assert resp.status_code == 422
    assert "unique" in str(resp.json()["detail"])


async def test_run_rejects_strict_and_bounded_fields(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client, email="bounds@ops-sim.in")
    # Float horizon (strict int) and out-of-range values all 422 before a run.
    for document in (
        _run_document(horizon_days=30.5),
        _run_document(horizon_days=3),
        _run_document(horizon_days=400),
        _run_document(start_date="1999-12-31"),
    ):
        resp = await client.post("/api/ops-sim/run", json=document, headers=headers)
        assert resp.status_code == 422, document


async def test_run_requires_authentication(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/ops-sim/run", json=_run_document())
    assert resp.status_code == 401


async def _worker_with_role(
    client: httpx.AsyncClient, owner: dict, email: str, permissions: list[str]
) -> dict:
    resp = await client.post(
        "/api/team/roles",
        json={"name": f"Role {email}", "permissions": permissions},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    role_id = resp.json()["id"]
    resp = await client.post(
        "/api/team/workers",
        json={
            "email": email,
            "role_id": role_id,
            "password": WORKER_PW,
            "name": "Worker",
        },
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    headers = await login(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}


async def test_run_requires_simulation_view_permission(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="rbac-owner@ops-sim.in")
    headers = await _worker_with_role(client, owner, "nosim@ops-sim.in", ["dashboard.view"])
    resp = await client.post("/api/ops-sim/run", json=_run_document(), headers=headers)
    assert resp.status_code == 403


async def test_run_allows_simulation_view_permission(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="rbac-ok@ops-sim.in")
    headers = await _worker_with_role(client, owner, "simviewer@ops-sim.in", ["simulation.view"])
    resp = await client.post("/api/ops-sim/run", json=_run_document(), headers=headers)
    assert resp.status_code == 200, resp.text
