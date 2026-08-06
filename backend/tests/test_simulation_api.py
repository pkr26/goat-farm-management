"""API tests for the simulation module: breed defaults, herd snapshot,
ad-hoc runs, RBAC, and scenario CRUD / run / compare.

Runs use small horizons (12 months) and tiny Monte Carlo counts to stay fast.
Assumption payloads come from GET /api/simulation/defaults (a complete valid
set) with selected fields overridden — the assumptions schema forbids extra
keys, so this doubles as a contract check on the defaults endpoint.
"""

from datetime import date, timedelta

import httpx

from .conftest import login, owner_with_farm

WORKER_PW = "workerpass123"


# ---------------------------------------------------------------------------
# Helpers (mirroring test_team_extended patterns)
# ---------------------------------------------------------------------------
async def add_worker(
    client: httpx.AsyncClient, owner: dict, role_id: int, email: str
) -> httpx.Response:
    return await client.post(
        "/api/team/workers",
        json={"email": email, "role_id": role_id, "password": WORKER_PW, "name": "Worker"},
        headers=owner,
    )


async def worker_headers(
    client: httpx.AsyncClient, owner: dict, permissions: list[str], email: str
) -> dict:
    """Worker with a custom role holding exactly `permissions`, on owner's farm."""
    resp = await client.post(
        "/api/team/roles", json={"name": f"Role {email}", "permissions": permissions}, headers=owner
    )
    assert resp.status_code == 201, resp.text
    role_id = resp.json()["id"]
    resp = await add_worker(client, owner, role_id, email)
    assert resp.status_code == 201, resp.text
    headers = await login(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}


async def default_assumptions(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


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


async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    sex: str,
    dob_days: int | None,
) -> dict:
    payload: dict = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": "BREEDING",
    }
    if dob_days is not None:
        payload["date_of_birth"] = (date.today() - timedelta(days=dob_days)).isoformat()
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Defaults endpoints
# ---------------------------------------------------------------------------
async def test_defaults_known_breed(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assert assumptions["meta"]["horizon_months"] == 120
    assert assumptions["herd"]["does"] == 50
    resp = await client.get("/api/simulation/defaults", params={"breed": "sirohi"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["sales"]["lactation_milk_litres"] == 110.0
    resp = await client.get(
        "/api/simulation/defaults",
        params={"breed": "osmanabadi", "system": "semi_intensive"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["feed"]["grazing_dm_fraction"] == 0.3


async def test_defaults_unknown_breed_400(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/simulation/defaults", params={"breed": "merino"}, headers=headers)
    assert resp.status_code == 400


async def test_defaults_breeds_list(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get("/api/simulation/defaults/breeds", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["breeds"]) == {
        "osmanabadi",
        "sirohi",
        "barbari",
        "jamunapari",
        "beetal",
        "black_bengal",
        "boer_cross",
    }
    assert body["systems"] == ["stall_fed", "semi_intensive"]


# ---------------------------------------------------------------------------
# Herd snapshot
# ---------------------------------------------------------------------------
async def test_herd_snapshot_groups_active_animals(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-1", "F", 550)  # ~18 m -> doe
    await make_animal(client, headers, "D-2", "F", 400)  # ~13 m -> doe
    await make_animal(client, headers, "B-1", "M", None)  # unknown age -> buck
    await make_animal(client, headers, "K-1", "F", 30)  # 1 m -> f_kid
    await make_animal(client, headers, "W-1", "F", 120)  # ~4 m -> f_weaner
    await make_animal(client, headers, "G-1", "M", 240)  # ~8 m -> m_grower
    sold = await make_animal(client, headers, "S-1", "F", 550)
    resp = await client.post(
        f"/api/animals/{sold['id']}/status",
        json={"new_status": "SOLD", "sale_price": 9000},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get("/api/simulation/herd-snapshot", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "does": 2,
        "bucks": 1,
        "f_kids": 1,
        "f_weaners": 1,
        "f_growers": 0,
        "m_kids": 0,
        "m_weaners": 0,
        "m_growers": 1,
        "total_head": 6,
    }


# ---------------------------------------------------------------------------
# Ad-hoc run
# ---------------------------------------------------------------------------
async def test_run_roundtrip(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 12
    assumptions["risk"]["monte_carlo_runs"] = 10
    resp = await client.post(
        "/api/simulation/run",
        json={"assumptions": assumptions, "monte_carlo": True, "sensitivity": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["months"]) == 12
    assert body["metrics"]["project_cost"] > 0.0
    assert body["monte_carlo"]["runs"] == 10
    assert len(body["sensitivity"]) == 8


async def test_run_rejects_extra_assumption_field(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["bogus"] = 1  # assumptions schema forbids extra keys
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------
async def test_simulation_rbac(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    assumptions = await default_assumptions(client, owner)
    assumptions["meta"]["horizon_months"] = 12

    # A role with no simulation permissions: everything 403s.
    no_perms = await worker_headers(client, owner, ["dashboard.view"], "nop@farm.in")
    assert (await client.get("/api/simulation/defaults", headers=no_perms)).status_code == 403
    assert (await client.get("/api/simulation/herd-snapshot", headers=no_perms)).status_code == 403
    run_resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=no_perms
    )
    assert run_resp.status_code == 403
    assert (await client.get("/api/simulation/scenarios", headers=no_perms)).status_code == 403

    # simulation.view runs and reads but cannot manage scenarios.
    viewer = await worker_headers(client, owner, ["simulation.view"], "view@farm.in")
    assert (await client.get("/api/simulation/defaults", headers=viewer)).status_code == 200
    run_resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=viewer
    )
    assert run_resp.status_code == 200
    create_resp = await client.post(
        "/api/simulation/scenarios",
        json={"name": "Viewer scenario", "assumptions": assumptions},
        headers=viewer,
    )
    assert create_resp.status_code == 403

    # simulation.manage can create (manage implies the view checks pass too is
    # NOT assumed: manage roles need view for reads, so give both here).
    manager = await worker_headers(
        client, owner, ["simulation.view", "simulation.manage"], "mgr@farm.in"
    )
    create_resp = await client.post(
        "/api/simulation/scenarios",
        json={"name": "Manager scenario", "assumptions": assumptions},
        headers=manager,
    )
    assert create_resp.status_code == 201


# ---------------------------------------------------------------------------
# Scenario CRUD / run / compare
# ---------------------------------------------------------------------------
async def test_scenario_crud(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 12

    created = await create_scenario(client, headers, "Base plan", assumptions)
    assert created["name"] == "Base plan"
    assert created["assumptions"]["meta"]["horizon_months"] == 12
    assert created["created_at"] and created["updated_at"]

    listing = await client.get("/api/simulation/scenarios", headers=headers)
    assert [s["name"] for s in listing.json()] == ["Base plan"]

    fetched = await client.get(f"/api/simulation/scenarios/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]

    # Name uniqueness per farm.
    clash = await client.post(
        "/api/simulation/scenarios",
        json={"name": "Base plan", "assumptions": assumptions},
        headers=headers,
    )
    assert clash.status_code == 400

    patched = await client.patch(
        f"/api/simulation/scenarios/{created['id']}",
        json={"name": "Base plan v2", "notes": "updated"},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Base plan v2"
    assert patched.json()["notes"] == "updated"

    deleted = await client.delete(f"/api/simulation/scenarios/{created['id']}", headers=headers)
    assert deleted.status_code == 204
    gone = await client.get(f"/api/simulation/scenarios/{created['id']}", headers=headers)
    assert gone.status_code == 404


async def test_scenario_cross_farm_404(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Farm A")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Farm B")
    assumptions = await default_assumptions(client, owner_a)
    created = await create_scenario(client, owner_a, "A plan", assumptions)

    # Farm B cannot see, run, patch or delete Farm A's scenario.
    assert (
        await client.get(f"/api/simulation/scenarios/{created['id']}", headers=owner_b)
    ).status_code == 404
    assert (
        await client.post(f"/api/simulation/scenarios/{created['id']}/run", headers=owner_b)
    ).status_code == 404
    assert (
        await client.patch(
            f"/api/simulation/scenarios/{created['id']}",
            json={"notes": "hijack"},
            headers=owner_b,
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/simulation/scenarios/{created['id']}", headers=owner_b)
    ).status_code == 404
    # And it does not leak into Farm B's list.
    listing = await client.get("/api/simulation/scenarios", headers=owner_b)
    assert listing.json() == []


async def test_scenario_run(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 12
    created = await create_scenario(client, headers, "Runnable", assumptions)

    resp = await client.post(f"/api/simulation/scenarios/{created['id']}/run", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["months"]) == 12
    assert body["monte_carlo"] is None

    resp = await client.post(
        f"/api/simulation/scenarios/{created['id']}/run",
        params={"monte_carlo": True},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["monte_carlo"]["runs"] == 500


async def test_scenario_compare(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    # 24 months so meat sales (first at month 13) fall inside the horizon.
    assumptions["meta"]["horizon_months"] = 24
    first = await create_scenario(client, headers, "Plan A", assumptions)
    assumptions["sales"]["meat_price_per_kg"] = 400.0
    second = await create_scenario(client, headers, "Plan B", assumptions)

    resp = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": f"{first['id']},{second['id']}"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["name"] for s in body["scenarios"]] == ["Plan A", "Plan B"]
    assert len(body["results"]) == 2
    assert all(len(r["months"]) == 24 for r in body["results"])
    # Higher meat price -> higher NPV for Plan B.
    assert body["results"][1]["metrics"]["npv"] > body["results"][0]["metrics"]["npv"]
