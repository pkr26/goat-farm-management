"""API tests for the planner module: backward plan, saved-plan CRUD.

The backward-plan payloads anchor ``meta.start_year_month`` explicitly so the
calendar math is deterministic regardless of when the suite runs.
"""

import httpx
import pytest
from sqlalchemy import text

import app.api.planner as planner_api
from app.core.config import get_settings

from .conftest import login, owner_with_farm

WORKER_PW = "workerpass123"
START = "2026-01"


async def worker_headers(
    client: httpx.AsyncClient, owner: dict, permissions: list[str], email: str
) -> dict:
    """Worker with a custom role holding exactly `permissions`, on owner's farm."""
    resp = await client.post(
        "/api/team/roles", json={"name": f"Role {email}", "permissions": permissions}, headers=owner
    )
    assert resp.status_code == 201, resp.text
    role_id = resp.json()["id"]
    resp = await client.post(
        "/api/team/workers",
        json={"email": email, "role_id": role_id, "password": WORKER_PW, "name": "Worker"},
        headers=owner,
    )
    assert resp.status_code == 201, resp.text
    headers = await login(client, email, WORKER_PW)
    return headers | {"X-Farm-Id": owner["X-Farm-Id"]}


async def default_assumptions(client: httpx.AsyncClient, headers: dict) -> dict:
    resp = await client.get("/api/simulation/defaults", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _plan_document(assumptions: dict, targets: list[dict], **extra: object) -> dict:
    assumptions = {
        **assumptions,
        "meta": {**assumptions.get("meta", {}), "start_year_month": START},
    }
    return {"assumptions": assumptions, "targets": targets, **extra}


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------
def test_planner_api_contract_exists() -> None:
    from app.api.planner import router

    paths = {route.path for route in router.routes}
    assert "/api/planner/plan" in paths
    assert "/api/planner/plans" in paths


# Only the backward-plan surface and saved-plan CRUD live here.
async def test_backward_plan_end_to_end(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    document = _plan_document(
        assumptions,
        [{"year_month": "2028-01", "animal_class": "male_grower", "count": 25.0}],
        close_gaps=True,
        risk_runs=5,
    )
    resp = await client.post("/api/planner/plan", json=document, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["start_year_month"] == START
    assert body["targets_echo"] == [
        {"year_month": "2028-01", "animal_class": "male_grower", "count": 25.0}
    ]
    # The nested sale-planner report still answers feasibility and risk.
    assert body["plan"]["before"]["targets"][0]["requested"] == 25.0
    assert body["plan"]["gaps_closed"] is True
    assert body["plan"]["after"]["all_met"] is True
    assert body["plan"]["recommended_purchases"]
    assert len(body["plan"]["probabilities"]) == 1
    assert 0.0 <= body["plan"]["probabilities"][0]["p_full"] <= 1.0
    # The backward layer: stage plan ending at the sale month, dated actions,
    # and a requirement chain per target.
    assert body["stage_plan"][-1]["year_month"] == "2028-01"
    kinds = {action["kind"] for action in body["actions"]}
    assert {"purchase", "breed", "expect_births", "sell"} <= kinds
    chain = body["chains"][0]
    assert chain["year_month"] == "2028-01"
    assert chain["steps"][0]["label"].startswith("breedable")
    assert any("Plan runs 2026-01" in note for note in body["notes"])


async def test_backward_plan_rejects_target_at_or_before_start(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    document = _plan_document(
        assumptions, [{"year_month": "2025-12", "animal_class": "male_grower", "count": 5.0}]
    )
    resp = await client.post("/api/planner/plan", json=document, headers=headers)
    assert resp.status_code == 422
    assert "at or before the plan start" in resp.text


async def test_backward_plan_rejects_target_beyond_twenty_years(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    document = _plan_document(
        assumptions, [{"year_month": "2050-01", "animal_class": "male_grower", "count": 5.0}]
    )
    resp = await client.post("/api/planner/plan", json=document, headers=headers)
    assert resp.status_code == 422
    assert "20-year horizon" in resp.text


async def test_backward_plan_rejects_event_cap_overflow_with_422(
    client: httpx.AsyncClient,
) -> None:
    """460 existing purchases + 50 sale targets exceeds the 500-event cap.

    The planner builds the combined event document inside the offloaded
    worker; that ValidationError must surface as a clean 422, never an
    unhandled 500.
    """
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["events"] = [
        {"month": m, "kind": "purchase", "animal_class": "doe", "count": 1}
        for m in range(1, 61)
        for _ in range(8)  # 480 purchases across the horizon
    ][:460]
    document = _plan_document(
        assumptions,
        [{"year_month": "2029-12", "animal_class": "male_grower", "count": 5.0} for _ in range(50)],
    )
    resp = await client.post("/api/planner/plan", json=document, headers=headers)
    assert resp.status_code == 422, resp.text
    assert "cannot be represented" in resp.text


async def test_backward_plan_survives_schema_max_target_count(client: httpx.AsyncClient) -> None:
    """count=100_000 validates as input; the gap-closer must split its
    purchase into schema-legal chunks instead of crashing mid-iteration."""
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)
    assumptions["herd"]["max_breeding_does"] = 0
    document = _plan_document(
        assumptions,
        [{"year_month": "2028-07", "animal_class": "male_grower", "count": 100_000.0}],
        close_gaps=True,
        risk_runs=0,
    )
    resp = await client.post("/api/planner/plan", json=document, headers=headers)
    assert resp.status_code in (200, 422), resp.text  # never a 500
    if resp.status_code == 200:
        for purchase in resp.json()["plan"]["recommended_purchases"]:
            assert purchase["count"] <= 100_000


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------
async def test_planner_rbac(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    assumptions = await default_assumptions(client, owner)
    document = _plan_document(
        assumptions, [{"year_month": "2028-01", "animal_class": "male_grower", "count": 5.0}]
    )
    plan_body = {
        "name": "RBAC plan",
        "start_year_month": START,
        "targets": document["targets"],
        "assumptions": document["assumptions"],
    }

    no_perms = await worker_headers(client, owner, ["dashboard.view"], "nop@plan.in")
    assert (
        await client.post("/api/planner/plan", json=document, headers=no_perms)
    ).status_code == 403
    assert (await client.get("/api/planner/plans", headers=no_perms)).status_code == 403

    viewer = await worker_headers(client, owner, ["simulation.view"], "view@plan.in")
    assert (
        await client.post("/api/planner/plan", json=document, headers=viewer)
    ).status_code == 200
    assert (await client.get("/api/planner/plans", headers=viewer)).status_code == 200
    assert (
        await client.post("/api/planner/plans", json=plan_body, headers=viewer)
    ).status_code == 403

    manager = await worker_headers(
        client, owner, ["simulation.view", "simulation.manage"], "mgr@plan.in"
    )
    created = await client.post("/api/planner/plans", json=plan_body, headers=manager)
    assert created.status_code == 201, created.text


# ---------------------------------------------------------------------------
# Saved plans CRUD
# ---------------------------------------------------------------------------
async def _create_plan(
    client: httpx.AsyncClient, headers: dict, name: str, assumptions: dict
) -> dict:
    resp = await client.post(
        "/api/planner/plans",
        json={
            "name": name,
            "start_year_month": START,
            "targets": [
                {"year_month": "2028-01", "animal_class": "male_grower", "count": 25.0},
                {"year_month": "2029-01", "animal_class": "doe", "count": 5.0},
            ],
            "assumptions": assumptions,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_planner_plan_crud(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    assumptions = await default_assumptions(client, headers)

    created = await _create_plan(client, headers, "Festival 2028", assumptions)
    assert created["name"] == "Festival 2028"
    assert created["start_year_month"] == START
    assert created["revision"] == 1
    assert [t["year_month"] for t in created["targets"]] == ["2028-01", "2029-01"]
    assert created["assumptions"]["meta"]["start_year_month"] == START
    assert created["valid"] is True

    listing = await client.get("/api/planner/plans", headers=headers)
    assert listing.status_code == 200, listing.text
    assert listing.json() == {"items": [created], "total": 1, "limit": 20, "offset": 0}

    fetched = await client.get(f"/api/planner/plans/{created['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]

    # Update targets; revision bumps.
    updated = await client.patch(
        f"/api/planner/plans/{created['id']}",
        json={
            "expected_revision": 1,
            "targets": [{"year_month": "2028-01", "animal_class": "male_grower", "count": 40.0}],
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert updated.json()["targets"][0]["count"] == 40.0

    # Stale revision → 409.
    stale = await client.patch(
        f"/api/planner/plans/{created['id']}",
        json={"expected_revision": 1, "notes": "stale"},
        headers=headers,
    )
    assert stale.status_code == 409

    # Duplicate name → 400.
    clash = await _create_plan(client, headers, "Other plan", assumptions)
    rename = await client.patch(
        f"/api/planner/plans/{clash['id']}",
        json={"expected_revision": 1, "name": "Festival 2028"},
        headers=headers,
    )
    assert rename.status_code == 400

    deleted = await client.delete(f"/api/planner/plans/{created['id']}", headers=headers)
    assert deleted.status_code == 204
    assert (
        await client.get(f"/api/planner/plans/{created['id']}", headers=headers)
    ).status_code == 404


async def test_planner_plan_lock_namespace_is_unique(client: httpx.AsyncClient) -> None:
    """The plan-quota advisory lock must not share a namespace with any other
    feature's farm lock — a collision serializes unrelated writes across
    features."""
    from app.api.simulation import SCENARIO_QUOTA_LOCK_NAMESPACE
    from app.api.team import TEAM_PROVISIONING_LOCK_NAMESPACE
    from app.services.tasks import MANUAL_TASK_QUEUE_LOCK_NAMESPACE

    taken = {
        MANUAL_TASK_QUEUE_LOCK_NAMESPACE,
        TEAM_PROVISIONING_LOCK_NAMESPACE,
        SCENARIO_QUOTA_LOCK_NAMESPACE,
    }
    assert planner_api.PLAN_QUOTA_LOCK_NAMESPACE not in taken


async def test_planner_plan_rejects_out_of_range_years_at_the_schema(
    client: httpx.AsyncClient,
) -> None:
    """Calendar fields are bounded to the engine's 1900-2200 range at the
    schema, so a dead-on-arrival plan can never be stored (regression: year
    0000/9999 plans passed create, then 422'd on every later read)."""
    owner = await owner_with_farm(client)
    assumptions = await default_assumptions(client, owner)
    for bad_start, bad_target in [("1899-12", "2030-01"), ("2026-01", "9999-12")]:
        resp = await client.post(
            "/api/planner/plans",
            json={
                "name": f"Dead plan {bad_start}",
                "start_year_month": bad_start,
                "targets": [{"year_month": bad_target, "animal_class": "doe", "count": 1.0}],
                "assumptions": assumptions,
            },
            headers=owner,
        )
        assert resp.status_code == 422, resp.text


async def test_planner_plan_patch_answers_422_not_500_on_stale_stored_assumptions(
    client: httpx.AsyncClient,
) -> None:
    """A stored assumptions document that no longer validates must surface as
    422 on PATCH too, not a bare 500 mid-update (regression: the anchor
    re-validation in update_plan was unguarded)."""
    from app.db import get_sessionmaker

    owner = await owner_with_farm(client)
    assumptions = await default_assumptions(client, owner)
    created = await _create_plan(client, owner, "Corrupt me", assumptions)

    # Corrupt the stored document behind the API's back.
    async with get_sessionmaker()() as session:
        await session.execute(
            text("UPDATE planner_plans SET assumptions = :doc WHERE id = :id"),
            {"doc": '{"meta": {"start_year_month": "0000-01"}}', "id": created["id"]},
        )
        await session.commit()

    resp = await client.patch(
        f"/api/planner/plans/{created['id']}",
        json={"expected_revision": 1, "start_year_month": "2026-02"},
        headers=owner,
    )
    assert resp.status_code == 422, resp.text
    assert "no longer validate" in resp.text


async def test_backward_plan_ceiling_rejection_is_not_charged(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target past the 20-year horizon must 422 BEFORE the CPU budget is
    charged — a doomed request spends no admission units (regression: the
    ceiling check lived inside the offloaded engine call)."""
    owner = await owner_with_farm(client)
    assumptions = await default_assumptions(client, owner)
    charged = []
    monkeypatch.setattr(planner_api, "_charge_run_budget", lambda *a: charged.append(a))
    document = _plan_document(
        assumptions, [{"year_month": "2050-01", "animal_class": "male_grower", "count": 5.0}]
    )
    resp = await client.post("/api/planner/plan", json=document, headers=owner)
    assert resp.status_code == 422
    assert "20-year horizon" in resp.text
    assert charged == [], "a rejected plan must not charge the CPU budget"


async def test_backward_plan_accepts_duplicate_targets_same_month_and_class(
    client: httpx.AsyncClient,
) -> None:
    """Three identical targets are three sales: the fills, echo and chains
    stay index-aligned through the engine's queue-based fill matching."""
    owner = await owner_with_farm(client)
    assumptions = await default_assumptions(client, owner)
    document = _plan_document(
        assumptions,
        [
            {"year_month": "2028-01", "animal_class": "male_grower", "count": 5.0},
            {"year_month": "2028-01", "animal_class": "male_grower", "count": 5.0},
            {"year_month": "2028-01", "animal_class": "male_grower", "count": 5.0},
        ],
    )
    resp = await client.post("/api/planner/plan", json=document, headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["targets_echo"]) == 3
    assert len(body["plan"]["before"]["targets"]) == 3
    assert [fill["requested"] for fill in body["plan"]["before"]["targets"]] == [5.0, 5.0, 5.0]
    assert len(body["chains"]) == 3


async def test_planner_plan_farm_scoping(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    assumptions = await default_assumptions(client, owner)
    created = await _create_plan(client, owner, "Mine", assumptions)

    # A second farm's owner must not see or touch it: unknown and foreign ids
    # both 404, so the response leaks no existence information.
    other = await owner_with_farm(client, email="other-owner@farm.in")
    assert (
        await client.get(f"/api/planner/plans/{created['id']}", headers=other)
    ).status_code == 404
    assert (
        await client.patch(
            f"/api/planner/plans/{created['id']}",
            json={"expected_revision": 1, "notes": "hijack"},
            headers=other,
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/planner/plans/{created['id']}", headers=other)
    ).status_code == 404
    listing = await client.get("/api/planner/plans", headers=other)
    assert listing.json()["total"] == 0


async def test_planner_plan_quota(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client)
    assumptions = await default_assumptions(client, owner)
    monkeypatch.setattr(get_settings(), "max_planner_plans_per_farm", 1)
    await _create_plan(client, owner, "Only one", assumptions)
    resp = await client.post(
        "/api/planner/plans",
        json={
            "name": "Two",
            "start_year_month": START,
            "targets": [{"year_month": "2028-01", "animal_class": "doe", "count": 1.0}],
            "assumptions": assumptions,
        },
        headers=owner,
    )
    assert resp.status_code == 409
    assert "saved-plan limit" in resp.text
