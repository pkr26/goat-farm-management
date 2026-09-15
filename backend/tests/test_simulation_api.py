"""API tests for the simulation module: breed defaults, herd snapshot,
ad-hoc runs, RBAC, and scenario CRUD / run / compare.

Runs use small horizons (12 months) and tiny Monte Carlo counts to stay fast.
Assumption payloads come from GET /api/simulation/defaults (a complete valid
set) with selected fields overridden — the assumptions schema forbids extra
keys, so this doubles as a contract check on the defaults endpoint.
"""

import asyncio
import math
import threading
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text

import app.api._run_limits as run_limits
import app.api.simulation as simulation_api
from app.api._run_limits import (
    _RUN_BUDGET_UNITS,
    _farm_run_lock,
    _farm_run_locks,
    _global_run_slots,
    _run_budget,
    _RunCostWindow,
    _user_run_locks,
    _with_run_limits,
)
from app.api.simulation import (
    _BREAK_EVEN_PASSES,
    _SENSITIVITY_PASSES,
    _run_cost,
)
from app.core.config import get_settings
from app.db import get_engine, get_sessionmaker
from app.models import (
    Animal,
    BreedingRecord,
    FeedInventory,
    KiddingRecord,
    KidEntry,
    Transaction,
)
from app.schemas.common import MAX_PAGE_OFFSET
from app.simulation import SimulationAssumptions

from .conftest import owner_with_farm, provisioned_worker_login

WORKER_PW = "workerpass123"


@pytest.fixture(autouse=True)
def _reset_in_process_run_state() -> None:
    """Order-robustness: the run budget window and per-farm run locks are
    MODULE-level in-process state. Charged units and held locks leaked
    between tests when the file ran as a whole (every test passed
    individually), so 31 tests failed on stale budgets/locks. Reset the
    existing objects in place — tests import ``_run_budget`` by reference, so
    rebinding the module attribute would disconnect them from the API's
    budget."""
    run_limits._run_budget.clear()
    run_limits._farm_run_locks.clear()
    yield
    run_limits._run_budget.clear()
    run_limits._farm_run_locks.clear()


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
    headers, _user_id = await provisioned_worker_login(client, email, WORKER_PW)
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


async def make_historical_animal(
    client: httpx.AsyncClient,
    headers: dict,
    *,
    tag: str,
    sex: str,
    purchase_price: float | None,
    weight_kg: float,
) -> dict:
    purchased_on = date.today() - timedelta(days=400)
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": tag,
            "sex": sex,
            "source": "PURCHASED",
            "current_bucket": "BREEDING",
            "date_of_birth": (date.today() - timedelta(days=800)).isoformat(),
            "purchase_date": purchased_on.isoformat(),
            "purchase_price": purchase_price,
            "weight_kg": weight_kg,
            "weight_date": date.today().isoformat(),
            "historical_import_reason": "Simulation calibration integration fixture",
        },
        headers=headers,
    )
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
        # CULLED, not SOLD: make_animal's purchased rows sit in QUARANTINE,
        # and quarantined stock cannot be sold into the food chain.
        json={"new_status": "CULLED"},
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
    assert len(body["sensitivity"]) == 9


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

    # A role with no simulation permissions: herd/run/scenario endpoints 403.
    # The /defaults endpoints are global breed reference data (9-9): they only
    # require authentication, not farm permissions.
    no_perms = await worker_headers(client, owner, ["dashboard.view"], "nop@farm.in")
    assert (await client.get("/api/simulation/defaults", headers=no_perms)).status_code == 200
    assert (
        await client.get("/api/simulation/defaults/breeds", headers=no_perms)
    ).status_code == 200
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
    assert created["revision"] == 1

    listing = await client.get("/api/simulation/scenarios", headers=headers)
    assert listing.status_code == 200, listing.text
    assert listing.json() == {
        "items": [created],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }

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
    assert patched.json()["revision"] == 2

    # Compatibility bridge: a v1 client omitting expected_revision can make
    # this first revision-1 update, but cannot silently overwrite it again.
    legacy_retry = await client.patch(
        f"/api/simulation/scenarios/{created['id']}",
        json={"notes": "stale legacy retry"},
        headers=headers,
    )
    assert legacy_retry.status_code == 409, legacy_retry.text

    deleted = await client.delete(f"/api/simulation/scenarios/{created['id']}", headers=headers)
    assert deleted.status_code == 204
    assert deleted.content == b""
    gone = await client.get(f"/api/simulation/scenarios/{created['id']}", headers=headers)
    assert gone.status_code == 404


async def test_scenario_update_rejects_a_stale_full_assumption_document(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    created = await create_scenario(client, headers, "Versioned plan", assumptions)

    first_document = {**assumptions, "herd": {**assumptions["herd"], "does": 51}}
    first = await client.patch(
        f"/api/simulation/scenarios/{created['id']}",
        json={"expected_revision": created["revision"], "assumptions": first_document},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == created["revision"] + 1

    stale_document = {**assumptions, "herd": {**assumptions["herd"], "bucks": 99}}
    stale = await client.patch(
        f"/api/simulation/scenarios/{created['id']}",
        json={"expected_revision": created["revision"], "assumptions": stale_document},
        headers=headers,
    )
    assert stale.status_code == 409, stale.text
    assert "changed since" in stale.json()["detail"]

    current = await client.get(f"/api/simulation/scenarios/{created['id']}", headers=headers)
    assert current.status_code == 200
    assert current.json()["assumptions"]["herd"]["does"] == 51
    assert current.json()["assumptions"]["herd"]["bucks"] == assumptions["herd"]["bucks"]


async def test_concurrent_scenario_updates_accept_exactly_one_revision(
    client: httpx.AsyncClient,
) -> None:
    """The row lock makes the revision token an atomic compare-and-swap."""
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    created = await create_scenario(client, headers, "Concurrent edit plan", assumptions)
    path = f"/api/simulation/scenarios/{created['id']}"

    first, second = await asyncio.gather(
        client.patch(
            path,
            json={"expected_revision": created["revision"], "notes": "editor one"},
            headers=headers,
        ),
        client.patch(
            path,
            json={"expected_revision": created["revision"], "notes": "editor two"},
            headers=headers,
        ),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    winner = first if first.status_code == 200 else second
    current = await client.get(path, headers=headers)
    assert current.status_code == 200
    assert current.json()["revision"] == created["revision"] + 1
    assert current.json()["notes"] == winner.json()["notes"]


async def test_scenario_text_rejects_postgres_control_characters(
    client: httpx.AsyncClient,
) -> None:
    """NUL cannot be stored in PostgreSQL text and must be a 422, never a 500."""
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)

    bad_create = await client.post(
        "/api/simulation/scenarios",
        json={"name": "Unsafe\u0000plan", "assumptions": assumptions},
        headers=headers,
    )
    assert bad_create.status_code == 422, bad_create.text

    created = await create_scenario(client, headers, "Safe plan", assumptions)
    bad_update = await client.patch(
        f"/api/simulation/scenarios/{created['id']}",
        json={"notes": "Unsafe\u0000notes"},
        headers=headers,
    )
    assert bad_update.status_code == 422, bad_update.text


async def test_saved_scenario_limit_is_concurrency_safe(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    monkeypatch.setattr(get_settings(), "max_simulation_scenarios_per_farm", 1)

    first, second = await asyncio.gather(
        client.post(
            "/api/simulation/scenarios",
            json={"name": "Capacity A", "assumptions": assumptions},
            headers=headers,
        ),
        client.post(
            "/api/simulation/scenarios",
            json={"name": "Capacity B", "assumptions": assumptions},
            headers=headers,
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    rejected = first if first.status_code == 409 else second
    assert rejected.json()["detail"] == "This farm has reached its saved-scenario limit."
    listing = await client.get("/api/simulation/scenarios", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert len(listing.json()["items"]) == 1


async def test_scenario_listing_is_bounded_paginated_and_deterministic(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    created = [
        await create_scenario(client, headers, f"Plan {index:02d}", assumptions)
        for index in range(23)
    ]

    first = await client.get("/api/simulation/scenarios", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["total"] == 23
    assert first.json()["limit"] == 20
    assert first.json()["offset"] == 0
    assert [item["id"] for item in first.json()["items"]] == [item["id"] for item in created[:20]]
    # Full ScenarioOut rows remain available on every page for load/edit.
    assert first.json()["items"][0]["assumptions"] == assumptions

    second = await client.get(
        "/api/simulation/scenarios",
        params={"limit": 5, "offset": 20},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    assert second.json()["total"] == 23
    assert second.json()["limit"] == 5
    assert second.json()["offset"] == 20
    assert [item["id"] for item in second.json()["items"]] == [item["id"] for item in created[20:]]

    for params in (
        {"limit": 0},
        {"limit": 51},
        {"offset": -1},
        {"offset": MAX_PAGE_OFFSET + 1},
    ):
        rejected = await client.get("/api/simulation/scenarios", params=params, headers=headers)
        assert rejected.status_code == 422, (params, rejected.text)


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
    assert listing.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


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
    # 24 months so meat sales (first at month 11) fall inside the horizon.
    assumptions["meta"]["horizon_months"] = 24
    first = await create_scenario(client, headers, "Plan A", assumptions)
    assumptions["sales"]["meat_price_per_kg"] = 500.0  # above the 400 default
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


# ---------------------------------------------------------------------------
# Scheduled herd events (assumptions.events)
# ---------------------------------------------------------------------------
def purchase_event(month: int = 14) -> dict[str, object]:
    return {"month": month, "kind": "purchase", "animal_class": "doe", "count": 10}


async def test_scenario_events_roundtrip(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 24
    assumptions["events"] = [purchase_event()]

    created = await create_scenario(client, headers, "Events plan", assumptions)
    assert created["assumptions"]["events"] == [purchase_event() | {"price_per_head": None}]

    fetched = await client.get(f"/api/simulation/scenarios/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["assumptions"]["events"][0]["animal_class"] == "doe"

    resp = await client.post(f"/api/simulation/scenarios/{created['id']}/run", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    month14 = body["months"][13]
    assert month14["purchases_head"] >= 10.0  # plus fractional auto-buck top-ups
    # Doe purchases capitalize on the breeding-stock account (model 3.1.0).
    assert month14["purchase_cost"] == 0.0
    assert month14["breeding_stock_capex"] >= 80000.0
    assert any("Purchased 10 doe(s)" in note for note in month14["events"])
    assert body["months"][12]["events"] == []


async def test_scenario_compare_with_events(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 24
    first = await create_scenario(client, headers, "No events", assumptions)
    assumptions["events"] = [purchase_event()]
    second = await create_scenario(client, headers, "Doe purchase", assumptions)

    resp = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": f"{first['id']},{second['id']}"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scenarios"][1]["assumptions"]["events"][0]["kind"] == "purchase"
    base, with_event = body["results"]
    assert any("Purchased" in note for note in with_event["months"][13]["events"])
    # The mid-run purchase is a cost: the event scenario has a lower NPV.
    assert with_event["metrics"]["npv"] < base["metrics"]["npv"]


async def test_run_rejects_invalid_events(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 24

    invalid_events = [
        purchase_event(month=25),  # beyond the horizon
        purchase_event() | {"count": 0},  # count must be > 0
        purchase_event() | {"animal_class": "camel"},  # unknown class
        purchase_event() | {"bogus": 1},  # extra keys forbidden
    ]
    for events in invalid_events:
        payload = assumptions | {"events": [events]}
        resp = await client.post(
            "/api/simulation/run", json={"assumptions": payload}, headers=headers
        )
        assert resp.status_code == 422, events

    # A valid event inside the horizon still runs.
    resp = await client.post(
        "/api/simulation/run",
        json={"assumptions": assumptions | {"events": [purchase_event(month=24)]}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Herd snapshot breed parameter
# ---------------------------------------------------------------------------
async def test_herd_snapshot_breed_param(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "G-13", "F", 400)  # ~13-month-old female

    # Osmanabadi (age at first breeding 12): she counts as a doe.
    resp = await client.get("/api/simulation/herd-snapshot", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["does"] == 1
    assert resp.json()["f_growers"] == 0

    # Jamunapari (age at first breeding 15): still a grower at 13 months.
    resp = await client.get(
        "/api/simulation/herd-snapshot", params={"breed": "jamunapari"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["does"] == 0
    assert resp.json()["f_growers"] == 1
    assert resp.json()["total_head"] == 1


async def test_herd_snapshot_unknown_breed_400(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.get(
        "/api/simulation/herd-snapshot", params={"breed": "merino"}, headers=headers
    )
    assert resp.status_code == 400


async def test_farm_calibration_returns_exact_herd_and_auditable_evidence(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-1", "F", 550)
    await make_animal(client, headers, "D-2", "F", 400)
    await make_animal(client, headers, "B-1", "M", None)
    await make_animal(client, headers, "K-1", "F", 30)
    await make_animal(client, headers, "W-1", "F", 120)
    await make_animal(client, headers, "G-1", "M", 240)

    resp = await client.get(
        "/api/simulation/calibration",
        params={"breed": "osmanabadi", "system": "stall_fed", "lookback_months": 24},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    herd = body["assumptions"]["herd"]
    assert {
        key: herd[key]
        for key in (
            "does",
            "bucks",
            "female_kids",
            "female_weaners",
            "female_growers",
            "male_kids",
            "male_weaners",
            "male_growers",
        )
    } == {
        "does": 2,
        "bucks": 1,
        "female_kids": 1,
        "female_weaners": 1,
        "female_growers": 0,
        "male_kids": 0,
        "male_weaners": 0,
        "male_growers": 1,
    }
    # Current head is an opening balance, not evidence that the farmer intends
    # a permanent two-doe ceiling; preserve the breed preset's expansion plan.
    assert herd["max_breeding_does"] == 50
    evidence = {item["path"]: item for item in body["evidence"]}
    assert evidence["herd.does"]["calibrated_value"] == 2
    assert evidence["herd.does"]["sample_size"] == 6
    assert evidence["herd.does"]["confidence"] == "high"
    assert body["coverage_score"] == pytest.approx(1.0 / 7.0)
    assert body["warnings"]
    SimulationAssumptions.model_validate(body["assumptions"])


async def test_farm_calibration_uses_operational_biology_market_and_cost_records(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    farm_id = int(headers["X-Farm-Id"])
    does = [
        await make_historical_animal(
            client,
            headers,
            tag=f"CAL-D-{index}",
            sex="F",
            purchase_price=9_000.0,
            weight_kg=30.0,
        )
        for index in range(5)
    ]
    buck = await make_historical_animal(
        client,
        headers,
        tag="CAL-B-1",
        sex="M",
        purchase_price=12_000.0,
        weight_kg=35.0,
    )
    sale_animals = [
        await make_historical_animal(
            client,
            headers,
            tag=f"CAL-S-{index}",
            sex="M",
            purchase_price=None,
            weight_kg=30.0,
        )
        for index in range(5)
    ]
    for animal in sale_animals:
        response = await client.post(
            f"/api/animals/{animal['id']}/status",
            json={"new_status": "SOLD", "sale_price": 12_000.0},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    async with get_sessionmaker()() as db:
        for index, doe in enumerate(does):
            bred_on = date.today() - timedelta(days=300 - index * 10)
            breeding = BreedingRecord(
                farm_id=farm_id,
                doe_id=doe["id"],
                buck_id=buck["id"],
                breeding_date=bred_on,
                ultrasound_date=bred_on + timedelta(days=35),
                ultrasound_result_date=bred_on + timedelta(days=35),
                ultrasound_done=True,
                pregnant=True,
                expected_kidding_date=bred_on + timedelta(days=150),
                outcome="CONFIRMED_PREGNANT",
            )
            db.add(breeding)
            await db.flush()
            kidding = KiddingRecord(
                farm_id=farm_id,
                doe_id=doe["id"],
                date=bred_on + timedelta(days=150),
                breeding_record_id=breeding.id,
            )
            db.add(kidding)
            await db.flush()
            db.add_all(
                [
                    KidEntry(
                        farm_id=farm_id,
                        kidding_record_id=kidding.id,
                        sex=sex,
                        birth_weight=3.25,
                        status="ALIVE",
                    )
                    for sex in ("F", "M")
                ]
            )
        inventory = FeedInventory(
            farm_id=farm_id,
            ingredient="Calibration green fodder",
            category="ROUGHAGE_WET",
            unit="kg",
            qty_on_hand=100.0,
            last_purchase_price_per_kg=Decimal("3.00"),
        )
        db.add(inventory)
        await db.flush()
        db.add_all(
            [
                Transaction(
                    farm_id=farm_id,
                    date=date.today(),
                    type="EXPENSE",
                    category="FEED",
                    amount=Decimal("300.00"),
                    source_type="FEED_PURCHASE",
                    source_id=999_001,
                    feed_inventory_id=inventory.id,
                    feed_quantity_kg=Decimal("100.000"),
                    feed_unit_price_per_kg=Decimal("3.00"),
                ),
                Transaction(
                    farm_id=farm_id,
                    date=date.today(),
                    type="EXPENSE",
                    category="LABOUR",
                    amount=Decimal("24000.00"),
                ),
            ]
        )
        await db.commit()

    resp = await client.get(
        "/api/simulation/calibration",
        params={"lookback_months": 24},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assumptions = resp.json()["assumptions"]
    assert assumptions["growth"]["adult_weight_doe_kg"] == pytest.approx(30.0)
    assert assumptions["growth"]["birth_weight_kg"] == pytest.approx(3.25)
    assert assumptions["growth"]["weight_by_age_months"][0] == pytest.approx(3.25)
    assert assumptions["reproduction"]["conception_rate"] == pytest.approx(1.0)
    assert assumptions["reproduction"]["litter_size"] == pytest.approx(2.0)
    assert assumptions["reproduction"]["gestation_months"] == 5
    assert assumptions["reproduction"]["sex_ratio_female"] == pytest.approx(0.5)
    assert assumptions["sales"]["meat_price_per_kg"] == pytest.approx(400.0)
    assert assumptions["herd"]["doe_purchase_price"] == pytest.approx(9_000.0)
    assert assumptions["feed"]["purchased_green_price_per_kg"] == pytest.approx(3.0)
    # Recurring costs are averaged over the ledger history that exists, not
    # over the window the caller asked for. This fixture books ₹24,000 of
    # labour against a ledger barely a year old, so dividing by the full
    # 24-month request understated it — the direction that makes an
    # unviable project look financeable (a 6-month-old farm queried with the
    # default lookback reported a quarter of its real labour bill).
    labour_per_month = assumptions["costs"]["labour_per_month"]
    assert 24_000.0 / 24 < labour_per_month <= 24_000.0
    assert any("ledger history" in warning for warning in resp.json()["warnings"])
    evidence_paths = {item["path"] for item in resp.json()["evidence"]}
    assert {
        "reproduction.conception_rate",
        "reproduction.litter_size",
        "growth.birth_weight_kg",
        "sales.meat_price_per_kg",
        "feed.purchased_green_price_per_kg",
        "costs.labour_per_month",
    } <= evidence_paths


async def test_farm_calibration_known_dob_mortality_exposure_starts_at_purchase(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    purchased_on = date.today() - timedelta(days=60)
    died_on = date.today()
    animals: list[dict] = []
    for index in range(10):
        response = await client.post(
            "/api/animals",
            json={
                "tag_number": f"CAL-EXPOSURE-{index}",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "BREEDING",
                "date_of_birth": (date.today() - timedelta(days=800)).isoformat(),
                "purchase_date": purchased_on.isoformat(),
                "weight_kg": 30.0,
                "weight_date": date.today().isoformat(),
                "historical_import_reason": "Mortality exposure regression fixture",
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        animals.append(response.json())

    response = await client.post(
        f"/api/animals/{animals[0]['id']}/status",
        json={"new_status": "DEAD", "date": died_on.isoformat()},
        headers=headers,
    )
    assert response.status_code == 200, response.text

    response = await client.get(
        "/api/simulation/calibration",
        params={"lookback_months": 24},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    reference_date = date.fromisoformat(body["reference_date"])
    animal_months = (
        9 * (reference_date - purchased_on).days + (died_on - purchased_on).days
    ) / 30.44
    expected = 1.0 - math.exp(-(1.0 / (animal_months / 12.0)))
    evidence = {item["path"]: item for item in body["evidence"]}

    assert evidence["mortality.adult"]["sample_size"] == 10
    assert evidence["mortality.adult"]["calibrated_value"] == pytest.approx(expected)
    assert body["assumptions"]["mortality"]["adult"] == pytest.approx(expected)


async def test_farm_calibration_excludes_kid_deaths_after_preweaning_window(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    farm_id = int(headers["X-Farm-Id"])
    does = [
        await make_historical_animal(
            client,
            headers,
            tag=f"CAL-KID-MORT-{index}",
            sex="F",
            purchase_price=None,
            weight_kg=30.0,
        )
        for index in range(10)
    ]
    buck = await make_historical_animal(
        client,
        headers,
        tag="CAL-KID-MORT-BUCK",
        sex="M",
        purchase_price=None,
        weight_kg=35.0,
    )
    kidding_on = date.today() - timedelta(days=180)

    async with get_sessionmaker()() as db:
        for index, doe in enumerate(does):
            bred_on = kidding_on - timedelta(days=150)
            breeding = BreedingRecord(
                farm_id=farm_id,
                doe_id=doe["id"],
                buck_id=buck["id"],
                breeding_date=bred_on,
                ultrasound_date=bred_on + timedelta(days=35),
                ultrasound_result_date=bred_on + timedelta(days=35),
                ultrasound_done=True,
                pregnant=True,
                expected_kidding_date=kidding_on,
                outcome="CONFIRMED_PREGNANT",
            )
            db.add(breeding)
            await db.flush()
            kidding = KiddingRecord(
                farm_id=farm_id,
                doe_id=doe["id"],
                date=kidding_on,
                breeding_record_id=breeding.id,
            )
            db.add(kidding)
            await db.flush()
            if index == 0:
                status = "DIED"
                mortality_reported_at = kidding_on + timedelta(days=60)
            elif index == 1:
                status = "DIED"
                mortality_reported_at = kidding_on + timedelta(days=120)
            else:
                status = "ALIVE"
                mortality_reported_at = None
            db.add(
                KidEntry(
                    farm_id=farm_id,
                    kidding_record_id=kidding.id,
                    sex="F" if index % 2 == 0 else "M",
                    birth_weight=3.0,
                    status=status,
                    mortality_reported_at=mortality_reported_at,
                )
            )
        await db.commit()

    response = await client.get(
        "/api/simulation/calibration",
        params={"lookback_months": 24},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    evidence = {item["path"]: item for item in body["evidence"]}
    # Whole-phase semantics: the observed fraction is written directly (the
    # old 1-(1-x)^4 annualization inflated a 10% loss into 34.4%).
    assert evidence["mortality.kid_pre_weaning"]["sample_size"] == 10
    assert evidence["mortality.kid_pre_weaning"]["calibrated_value"] == pytest.approx(1.0 / 10.0)
    assert body["assumptions"]["mortality"]["kid_pre_weaning"] == pytest.approx(1.0 / 10.0)


async def test_farm_calibration_validates_inputs_and_cross_domain_permissions(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    viewer = await worker_headers(client, owner, ["simulation.view"], "cal-view@farm.in")
    assert (await client.get("/api/simulation/calibration", headers=viewer)).status_code == 403

    all_views = await worker_headers(
        client,
        owner,
        [
            "simulation.view",
            "animals.view",
            "breeding.view",
            "kidding.view",
            "feeding.view",
            "finance.view",
        ],
        "cal-all@farm.in",
    )
    assert (await client.get("/api/simulation/calibration", headers=all_views)).status_code == 200
    assert (
        await client.get("/api/simulation/calibration", params={"breed": "merino"}, headers=owner)
    ).status_code == 400
    assert (
        await client.get(
            "/api/simulation/calibration", params={"lookback_months": 5}, headers=owner
        )
    ).status_code == 422


async def test_run_can_return_bounded_optimization(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 12
    assumptions["optimization"]["max_candidates"] = 3
    resp = await client.post(
        "/api/simulation/run",
        json={"assumptions": assumptions, "optimization": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_version"] == "3.2.0"
    assert len(body["assumptions_fingerprint"]) == 64
    assert body["optimization"]["evaluated_candidates"] <= 3
    assert body["optimization"]["feasible_candidates"] >= 0


async def test_simulation_cpu_phase_releases_database_checkout(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ad-hoc, stored, and compare runs do not pin auth/DB transactions."""
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 12
    scenario = await create_scenario(client, headers, "Pool release plan", assumptions)
    real_offloaded = simulation_api._run_offloaded

    request_factories = (
        lambda: client.post(
            "/api/simulation/run",
            json={"assumptions": assumptions},
            headers=headers,
        ),
        lambda: client.post(
            f"/api/simulation/scenarios/{scenario['id']}/run",
            headers=headers,
        ),
        lambda: client.get(
            "/api/simulation/scenarios/compare",
            params={"ids": str(scenario["id"])},
            headers=headers,
        ),
    )

    try:
        for request_factory in request_factories:
            started = asyncio.Event()
            release = asyncio.Event()

            async def parked_run(
                assumption_set: SimulationAssumptions,
                monte_carlo: bool,
                sensitivity: bool,
                optimization: bool,
                nouns: object = None,
                *,
                started_signal: asyncio.Event = started,
                release_signal: asyncio.Event = release,
            ):
                started_signal.set()
                await release_signal.wait()
                return await real_offloaded(
                    assumption_set,
                    monte_carlo,
                    sensitivity,
                    optimization,
                    nouns,  # type: ignore[arg-type]
                )

            monkeypatch.setattr(simulation_api, "_run_offloaded", parked_run)
            request = asyncio.create_task(request_factory())
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                assert get_engine().sync_engine.pool.checkedout() == 0
                readiness = await asyncio.wait_for(client.get("/readyz"), timeout=2)
                assert readiness.status_code == 200
            finally:
                release.set()
            response = await asyncio.wait_for(request, timeout=10)
            assert response.status_code == 200, response.text
    finally:
        _run_budget.clear()


# ---------------------------------------------------------------------------
# Per-farm run limiter (9-3): one in-flight run per farm, 429 while busy
# ---------------------------------------------------------------------------
async def test_run_limiter_429_while_run_in_flight(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 12
    farm_id = int(headers["X-Farm-Id"])
    lock = _farm_run_lock(farm_id)
    await lock.acquire()  # simulate an in-flight run for this farm
    try:
        resp = await client.post(
            "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
        )
        assert resp.status_code == 429, resp.text
        resp = await client.get(
            "/api/simulation/scenarios/compare", params={"ids": "1"}, headers=headers
        )
        assert resp.status_code == 429, resp.text
    finally:
        lock.release()
    # Once the lock is free, the same payload runs.
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text


async def test_busy_fast_path_does_not_leak_keyed_run_locks() -> None:
    """The 429 branch raised *before* the try/finally that drops idle keyed
    locks, so every distinct (farm, user) that arrived while the process-wide
    slots were full left two dict entries behind forever — precisely the
    cardinality growth _release_run_lock was written to prevent. The busy path
    is reached with unseen keys exactly when both global slots are taken."""
    keys = range(900_001, 900_051)

    async def never_runs() -> None:  # pragma: no cover - the 429 fires first
        raise AssertionError("operation must not run while capacity is busy")

    async with _global_run_slots, _global_run_slots:  # occupy both slots
        for key in keys:
            with pytest.raises(HTTPException) as exc_info:
                await _with_run_limits(key, key, never_runs)
            assert exc_info.value.status_code == 429
    assert not any(key in _farm_run_locks for key in keys)
    assert not any(key in _user_run_locks for key in keys)


async def test_cancelled_run_retains_capacity_until_native_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw asyncio cancellation abandons AnyIO's await, not its OS thread.

    Releasing the keyed/global guards at that point lets cancel/retry stack
    untracked pure-Python simulations beyond the advertised process ceiling.
    The request unwinds promptly, while native completion retains and later
    releases the admission lease independently.
    """
    farm_id = 910_001
    user_id = 920_001
    started = threading.Event()
    release = threading.Event()

    def parked_run(*_args: object) -> object:
        started.set()
        assert release.wait(timeout=10)
        return object()

    monkeypatch.setattr(simulation_api, "_run", parked_run)
    assumptions = SimulationAssumptions()

    async def operation() -> object:
        return await simulation_api._run_offloaded(
            assumptions,
            False,
            False,
            False,
        )

    request = asyncio.create_task(_with_run_limits(farm_id, user_id, operation))
    try:
        for _ in range(500):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("simulation worker did not start")

        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert _farm_run_locks[farm_id].locked()
        assert _user_run_locks[user_id].locked()
        assert _global_run_slots._value == 1

        async def must_not_run() -> None:  # pragma: no cover - busy guard fires
            raise AssertionError("same principal ran while canceled native work was live")

        with pytest.raises(HTTPException) as exc_info:
            await _with_run_limits(farm_id, user_id, must_not_run)
        assert exc_info.value.status_code == 429
    finally:
        release.set()

    # Completion releases and retires both keyed guards; cancellation must not
    # leave a permanent busy entry after the native worker really stops.
    for _ in range(500):
        if farm_id not in _farm_run_locks and user_id not in _user_run_locks:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("native completion did not release simulation capacity")
    assert _global_run_slots._value == 2


async def test_cancelled_queued_run_releases_lease_and_never_starts_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation may win before AnyIO assigns a worker-thread token."""
    farm_id = 910_002
    user_id = 920_002
    queued = asyncio.Event()
    never_started = asyncio.Event()
    captured: list[tuple[object, tuple[object, ...]]] = []
    engine_calls = 0

    async def parked_thread_submission(function, *args):  # type: ignore[no-untyped-def]
        captured.append((function, args))
        queued.set()
        await never_started.wait()
        return function(*args)

    def counted_run(*_args: object) -> object:
        nonlocal engine_calls
        engine_calls += 1
        return object()

    monkeypatch.setattr(run_limits, "run_in_threadpool", parked_thread_submission)
    monkeypatch.setattr(simulation_api, "_run", counted_run)
    assumptions = SimulationAssumptions()

    async def operation() -> object:
        return await simulation_api._run_offloaded(assumptions, False, False, False)

    request = asyncio.create_task(_with_run_limits(farm_id, user_id, operation))
    await asyncio.wait_for(queued.wait(), timeout=2)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    # The queued job did no native work, so cancellation itself returns every
    # capacity lease instead of waiting forever for a wrapper that may never run.
    assert farm_id not in _farm_run_locks
    assert user_id not in _user_run_locks
    assert _global_run_slots._value == 2

    # Model a thread-limiter callback that was already queued and runs late.
    # Its abandoned-state check must skip _run after the lease is gone.
    assert len(captured) == 1
    function, args = captured[0]
    await asyncio.to_thread(function, *args)  # type: ignore[arg-type]
    assert engine_calls == 0


# ---------------------------------------------------------------------------
# Per-user/per-farm CPU budget: concurrency caps bound parallelism, not rate
# ---------------------------------------------------------------------------
def test_run_cost_prices_horizon_and_monte_carlo() -> None:
    """A request's price must track the CPU it will actually burn, otherwise a
    per-request counter lets the schema-maximal body (240 months x 2,000 Monte
    Carlo runs, ~20 s of pure-Python CPU) cost the same as a 12-month run."""
    a = SimulationAssumptions()  # 120 months, 500 Monte Carlo runs
    base_passes = 1 + _BREAK_EVEN_PASSES
    assert _run_cost(a, False, False) == base_passes * 120
    assert _run_cost(a, True, False) == (base_passes + 500) * 120
    assert _run_cost(a, False, True) == (base_passes + _SENSITIVITY_PASSES) * 120
    assert _run_cost(a, False, False, True) == (base_passes + a.optimization.max_candidates) * 120
    a.meta.horizon_months = 240
    a.risk.monte_carlo_runs = 2000
    # The worst case the schema allows must not fit in the window twice.
    assert _run_cost(a, True, True, True) > _RUN_BUDGET_UNITS / 2
    assert _run_cost(a, True, True, True) <= _RUN_BUDGET_UNITS


def test_run_cost_window_expires_and_keeps_scopes_apart() -> None:
    now = [1000.0]
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=10, clock=lambda: now[0])
    assert not window.is_over_budget("user", 1)
    window.charge("user", 1, 60)
    assert not window.is_over_budget("user", 1)
    window.charge("user", 1, 40)
    assert window.is_over_budget("user", 1)
    assert not window.is_over_budget("farm", 1)  # scopes are independent
    assert not window.is_over_budget("user", 2)  # so are principals
    now[0] += 61.0  # the window slides
    assert not window.is_over_budget("user", 1)


def test_run_cost_window_checks_the_prospective_request_cost() -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=10)
    window.charge("user", 1, 99)
    assert not window.would_exceed_budget("user", 1, 1)
    assert window.would_exceed_budget("user", 1, 2)
    assert window.would_exceed_any((("user", 2, 60), ("user", 2, 41)))


def test_run_cost_window_saturation_admits_new_pair_and_preserves_hot_principal() -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=4)
    window.charge("user", 99, 99)  # one high-cost run => protected
    window.charge("user", 98, 1)
    window.charge("user", 98, 1)  # repeated cheap runs => protected
    window.charge("farm", 900, 1)
    window.charge("farm", 901, 1)

    new_pair = (("user", 1, 1), ("farm", 1, 1))
    assert not window.would_exceed_any(new_pair)
    window.charge_many(new_pair)

    assert len(window._spend) == 4
    assert set(window._spend) == {
        ("user", 99),
        ("user", 98),
        ("user", 1),
        ("farm", 1),
    }
    assert set(window._totals) == set(window._spend)
    assert set(window._cold).isdisjoint(window._protected)
    assert set(window._cold) | set(window._protected) == set(window._spend)
    assert window.would_exceed_budget("user", 99, 2)


def test_run_cost_window_all_protected_saturation_evicts_oldest_deterministically() -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=2)
    for principal in (1, 2):
        window.charge("user", principal, 50)
        window.charge("user", principal, 50)

    window.charge("user", 3, 1)

    assert set(window._spend) == {("user", 2), ("user", 3)}
    assert window.is_over_budget("user", 2)


def test_run_cost_window_probe_refreshes_cold_lru_recency() -> None:
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=2)
    window.charge("user", 1, 1)
    window.charge("user", 2, 1)
    assert not window.would_exceed_budget("user", 1, 1)  # user 1 is now newest

    window.charge("user", 3, 1)

    assert set(window._spend) == {("user", 1), ("user", 3)}


def test_run_cost_window_lazily_expires_saturated_candidates() -> None:
    now = [1000.0]
    window = _RunCostWindow(
        window_seconds=60,
        budget=100,
        max_keys=2,
        clock=lambda: now[0],
    )
    window.charge("user", 1, 50)
    window.charge("user", 1, 50)
    window.charge("farm", 1, 1)
    now[0] += 61.0

    window.charge("user", 2, 1)

    assert ("user", 2) in window._spend
    assert not window.is_over_budget("user", 1)
    assert set(window._spend) == {("user", 2)}


def test_saturated_run_cost_admission_does_constant_bucket_work(monkeypatch) -> None:
    """Operation-count benchmark: saturation work is independent of map size."""
    window = _RunCostWindow(window_seconds=60, budget=100, max_keys=10_000)
    for principal in range(10_000):
        window.charge("user", principal, 1)

    class NonIterableLedger(dict[tuple[str, int], object]):
        def __iter__(self):
            raise AssertionError("saturated admission must not scan the principal ledger")

        def keys(self):
            raise AssertionError("saturated admission must not scan the principal ledger")

    # Membership/get/set/pop remain available, but any accidental whole-map
    # set/dict-view operation fails deterministically instead of relying on a
    # timing threshold in CI.
    window._spend = NonIterableLedger(window._spend)  # type: ignore[assignment]

    prune_calls = 0
    original = window._prune_bucket

    def counted(bucket: tuple[str, int], *, touch: bool) -> int:
        nonlocal prune_calls
        prune_calls += 1
        return original(bucket, touch=touch)

    monkeypatch.setattr(window, "_prune_bucket", counted)
    window.charge_many((("user", 20_001, 1), ("farm", 20_001, 1)))

    assert len(window._spend) == 10_000
    assert len(window._totals) == 10_000
    assert len(window._cold) + len(window._protected) == 10_000
    assert prune_calls == 4  # two requested keys + two LRU eviction candidates


async def test_run_endpoint_key_saturation_does_not_globally_reject_new_farm(
    client: httpx.AsyncClient,
    monkeypatch,
) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 12
    _run_budget.clear()
    monkeypatch.setattr(_run_budget, "_max_keys", 2)
    _run_budget.charge("user", 999_999_991, 1)
    _run_budget.charge("farm", 999_999_991, 1)
    try:
        response = await client.post(
            "/api/simulation/run",
            json={"assumptions": assumptions},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        tracked = set(_run_budget._spend)
        assert len(tracked) == 2
        assert ("farm", int(headers["X-Farm-Id"])) in tracked
        assert sum(scope == "user" for scope, _key in tracked) == 1
        assert ("user", 999_999_991) not in tracked
        assert ("farm", 999_999_991) not in tracked
    finally:
        _run_budget.clear()


async def test_run_endpoint_rejects_a_request_that_would_cross_cpu_budget(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 12
    farm_id = int(headers["X-Farm-Id"])
    cost = _run_cost(SimulationAssumptions.model_validate(assumptions), False, False)
    _run_budget.charge("farm", farm_id, _RUN_BUDGET_UNITS - cost + 1)
    try:
        response = await client.post(
            "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
        )
        assert response.status_code == 429, response.text
        assert "budget" in response.json()["detail"]
    finally:
        _run_budget.clear()


async def test_run_endpoints_429_once_the_cpu_budget_is_spent(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 12
    farm_id = int(headers["X-Farm-Id"])
    created = await create_scenario(client, headers, "Budgeted", assumptions)
    try:
        resp = await client.post(
            "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
        )
        assert resp.status_code == 200, resp.text

        _run_budget.charge("farm", farm_id, _RUN_BUDGET_UNITS)
        for resp in (
            await client.post(
                "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
            ),
            await client.post(f"/api/simulation/scenarios/{created['id']}/run", headers=headers),
            await client.get(
                "/api/simulation/scenarios/compare",
                params={"ids": str(created["id"])},
                headers=headers,
            ),
        ):
            assert resp.status_code == 429, resp.text
            assert "budget" in resp.json()["detail"]
            assert resp.headers["Retry-After"]
    finally:
        _run_budget.clear()
    # With the budget released the same payload runs again.
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 200, resp.text


async def test_run_limiter_is_per_farm(client: httpx.AsyncClient) -> None:
    owner_a = await owner_with_farm(client, email="a@farm.in", farm_name="Farm A")
    owner_b = await owner_with_farm(client, email="b@farm.in", farm_name="Farm B")
    assumptions = await default_assumptions(client, owner_a)
    assumptions["meta"]["horizon_months"] = 12
    lock = _farm_run_lock(int(owner_a["X-Farm-Id"]))
    await lock.acquire()
    try:
        # Farm A is busy; farm B's run is unaffected.
        resp = await client.post(
            "/api/simulation/run", json={"assumptions": assumptions}, headers=owner_b
        )
        assert resp.status_code == 200, resp.text
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Stale scenario rows (9-6): visible-but-disabled in listings, 422 elsewhere
# ---------------------------------------------------------------------------
async def _corrupt_scenario_row(scenario_id: int) -> None:
    """Simulate a row stored under an older/looser assumptions schema."""
    async with get_sessionmaker()() as db:
        await db.execute(
            text("UPDATE simulation_scenarios SET assumptions = :blob WHERE id = :sid"),
            {"blob": '{"bogus": 1}', "sid": scenario_id},
        )
        await db.commit()


async def test_stale_scenario_row_never_500s(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["meta"]["horizon_months"] = 12
    good = await create_scenario(client, headers, "Good", assumptions)
    stale = await create_scenario(client, headers, "Stale", assumptions)
    await _corrupt_scenario_row(stale["id"])

    # Keep the bad row visible so a farmer can understand and repair/delete it;
    # silently dropping stored work made it look as though data had vanished.
    listing = await client.get("/api/simulation/scenarios", headers=headers)
    assert listing.status_code == 200, listing.text
    page = listing.json()
    assert page["total"] == 2
    assert page["limit"] == 20
    assert page["offset"] == 0
    listed = page["items"]
    assert [s["name"] for s in listed] == ["Good", "Stale"]
    assert listed[0]["valid"] is True
    assert listed[0]["assumptions"] is not None
    assert listed[1]["valid"] is False
    assert listed[1]["assumptions"] is None
    assert "current schema" in listed[1]["validation_error"]

    # Get / run / compare surface a clean 422 for the affected scenario.
    resp = await client.get(f"/api/simulation/scenarios/{stale['id']}", headers=headers)
    assert resp.status_code == 422, resp.text
    resp = await client.post(f"/api/simulation/scenarios/{stale['id']}/run", headers=headers)
    assert resp.status_code == 422, resp.text
    resp = await client.get(
        "/api/simulation/scenarios/compare",
        params={"ids": f"{good['id']},{stale['id']}"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    # Update rescues the row with fresh, valid assumptions; delete works too.
    patched = await client.patch(
        f"/api/simulation/scenarios/{stale['id']}",
        json={"assumptions": assumptions},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    deleted = await client.delete(f"/api/simulation/scenarios/{stale['id']}", headers=headers)
    assert deleted.status_code == 204


# ---------------------------------------------------------------------------
# N6: sensitivity mutator must not crash on schema-valid inputs.
# ---------------------------------------------------------------------------
async def test_sensitivity_clamps_kid_pre_weaning_at_ceiling(client: httpx.AsyncClient) -> None:
    """Base kid_pre_weaning=0.9 (schema max). Sensitivity used to multiply by
    1.2 → 1.08 → monthly_mortality_rate(-0.08) → math.pow(negative, 1/12) →
    ValueError → 500. Clamped at 0.9 in the mutator; run must return 200."""
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["mortality"]["kid_pre_weaning"] = 0.9
    assumptions["meta"]["horizon_months"] = 12
    resp = await client.post(
        "/api/simulation/run",
        json={"assumptions": assumptions, "sensitivity": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    # Sensitivity output must still contain the kid_pre_weaning entry
    # (clamped, not skipped).
    assert any(
        item["parameter"] == "kid_pre_weaning_mortality" for item in resp.json()["sensitivity"]
    )


async def test_sensitivity_clamps_litter_size_at_ceiling(client: httpx.AsyncClient) -> None:
    """Base litter_size=4.0 (schema max). Sensitivity used to multiply by
    1.2 → 4.8 (exceeded the biological ceiling). Clamped at 4.0."""
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["reproduction"]["litter_size"] = 4.0
    assumptions["meta"]["horizon_months"] = 12
    resp = await client.post(
        "/api/simulation/run",
        json={"assumptions": assumptions, "sensitivity": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def test_adult_weight_below_yearling_curve_is_422(client: httpx.AsyncClient) -> None:
    """adult_weight_doe_kg < max(weight_by_age_months[:13]) would yield a
    monotonically DECREASING weight curve past age 12 — MED."""
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["growth"]["adult_weight_doe_kg"] = 1.0  # far below the yearling weight
    resp = await client.post(
        "/api/simulation/run", json={"assumptions": assumptions}, headers=headers
    )
    assert resp.status_code == 422, resp.text


async def test_farm_calibration_labour_is_a_per_labourer_wage_not_the_farm_total(
    client: httpx.AsyncClient,
) -> None:
    """The engine charges ``labourers * costs.labour_per_month``, so calibration
    must store a per-head-of-staff wage. Writing the farm's whole monthly bill
    into that field made the engine re-multiply it by the labourer count — a
    4x overstatement on a 300-head farm, invisible in fixtures small enough to
    imply a single labourer."""
    headers = await owner_with_farm(client)
    farm_id = int(headers["X-Farm-Id"])

    # Enough active head that the default threshold (75/labourer) implies > 1.
    head_count = 160
    async with get_sessionmaker()() as db:
        db.add_all(
            [
                Animal(
                    farm_id=farm_id,
                    tag_number=f"LAB-{index:04d}",
                    sex="F",
                    status="ACTIVE",
                    source="PURCHASED",
                    current_bucket="BREEDING",
                    date_of_birth=date.today() - timedelta(days=800),
                    purchase_date=date.today() - timedelta(days=400),
                )
                for index in range(head_count)
            ]
        )
        db.add(
            Transaction(
                farm_id=farm_id,
                date=date.today(),
                type="EXPENSE",
                category="LABOUR",
                amount=Decimal("90000.00"),
            )
        )
        await db.commit()

    resp = await client.get(
        "/api/simulation/calibration", params={"lookback_months": 6}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assumptions = body["assumptions"]

    threshold = assumptions["costs"]["labour_per_head_threshold"]
    labourers = max(1, math.ceil(head_count / threshold))
    assert labourers > 1, "fixture must imply more than one labourer to be meaningful"

    per_labourer = assumptions["costs"]["labour_per_month"]
    # The round trip the engine performs must reproduce the farm's real bill.
    assert per_labourer * labourers == pytest.approx(90_000.0)
    assert per_labourer == pytest.approx(90_000.0 / labourers)

    evidence = {item["path"]: item for item in body["evidence"]}
    assert "labourer" in evidence["costs.labour_per_month"]["method"]
