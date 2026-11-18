"""Supported team callers retain authorization, credential and rollback boundaries."""

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import team as team_api
from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import FarmMembership, IdempotencyRecord, Role, SecurityEvent, User

from .conftest import owner_with_farm
from .settings_helpers import settings_from_input
from .test_team_extended import create_custom_role, team_manager_headers
from .test_team_reauthorization_and_revocation_contracts import (
    _at_boundary,
    _client,
    _credential_state,
)


async def _worker(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    role: int,
    email: str,
    *,
    pin: str | None = None,
) -> int:
    credential = {"pin": pin} if pin is not None else {"password": "workerpass123"}
    response = await client.post(
        "/api/team/workers",
        headers=owner,
        json={"name": "Contract worker", "email": email, "role_id": role} | credential,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def _audit_count(event: str) -> int:
    async with get_sessionmaker()() as db:
        return int(
            await db.scalar(
                select(func.count()).select_from(SecurityEvent).where(SecurityEvent.event == event)
            )
            or 0
        )


async def _no_idempotent_result(operation: str) -> None:
    async with get_sessionmaker()() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.operation == operation)
            )
            == 0
        )


async def _no_account(email: str) -> None:
    async with get_sessionmaker()() as db:
        assert (
            await db.scalar(select(func.count()).select_from(User).where(User.email == email)) == 0
        )


@pytest.mark.parametrize(
    "existing_preferences", [False, True], ids=["new-destination", "saved-destination"]
)
async def test_delegated_manager_cannot_write_notification_destinations(
    client: httpx.AsyncClient, existing_preferences: bool
) -> None:
    owner = await owner_with_farm(client, email="destination-owner@farm.in")
    role = await create_custom_role(client, owner, "Recipient", ["tasks.view"])
    membership = await _worker(client, owner, int(role["id"]), "destination-worker@farm.in")
    manager, _ = await team_manager_headers(client, owner, extra_perms=["tasks.view"])
    endpoint = f"/api/team/workers/{membership}/notifications"
    if existing_preferences:
        saved = await client.put(
            endpoint,
            headers=owner,
            json={"phone": "+919888877777", "daily_digest": True, "verified": True},
        )
        assert saved.status_code == 200, saved.text
    before = await client.get(endpoint, headers=owner)
    assert before.status_code == 200
    assert (await client.get(endpoint, headers=manager)).json() == before.json()
    credentials = await _credential_state(membership)
    events = await _audit_count("team.worker.notification_prefs")

    denied = await client.put(
        endpoint,
        headers=manager,
        json={
            "phone": "+919111122222",
            "daily_digest": False,
            "verified": False,
            "screening_flags": True,
            "movement_restriction": True,
        },
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"].startswith("Only the farm owner")
    after = await client.get(endpoint, headers=owner)
    assert after.status_code == 200 and after.json() == before.json()
    assert await _audit_count("team.worker.notification_prefs") == events
    assert await _credential_state(membership) == credentials
    assert (await client.get("/api/team", headers=manager)).status_code == 200
    assert (await client.get("/api/team", headers=owner)).status_code == 200


@pytest.fixture
def six_digit_pin_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    current = get_settings()
    validated = settings_from_input(Settings, current.model_dump(), worker_pin_min_length=6)
    monkeypatch.setattr(current, "worker_pin_min_length", validated.worker_pin_min_length)


@pytest.mark.parametrize("pin", ["0123", "01234"], ids=["four-digits", "five-digits"])
async def test_configured_pin_floor_rejects_short_worker_creation_without_a_saved_result(
    client: httpx.AsyncClient, six_digit_pin_floor: None, pin: str
) -> None:
    owner = await owner_with_farm(client, email="pin-floor-create-owner@farm.in")
    role = await create_custom_role(client, owner, "Tablet reader", ["tasks.view"])
    email = "pin-floor-create-worker@farm.in"
    headers = owner | {"Idempotency-Key": "pin-floor-create"}
    payload = {"name": "Tablet worker", "email": email, "role_id": role["id"], "pin": pin}
    denied = await client.post("/api/team/workers", headers=headers, json=payload)
    assert denied.status_code == 400, denied.text
    assert denied.json()["detail"] == "A worker PIN must be at least 6 digits on this deployment"
    await _no_account(email)
    await _no_idempotent_result("team.workers.create")
    assert await _audit_count("team.worker.create") == 0
    accepted = await client.post(
        "/api/team/workers", headers=headers, json=payload | {"pin": "012345"}
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["pin_set"] is True
    assert accepted.headers.get("Idempotency-Replayed") != "true"
    async with _client() as tablet:
        signed_in = await tablet.post(
            "/api/auth/worker-login",
            json={
                "farm_id": int(owner["X-Farm-Id"]),
                "membership_id": accepted.json()["id"],
                "pin": "012345",
            },
        )
        assert signed_in.status_code == 200, signed_in.text
    assert (await client.get("/api/team", headers=owner)).status_code == 200


@pytest.mark.parametrize("pin", ["0123", "01234"], ids=["four-digits", "five-digits"])
async def test_configured_pin_floor_preserves_the_old_credential_and_session_on_rejection(
    client: httpx.AsyncClient, six_digit_pin_floor: None, pin: str
) -> None:
    owner = await owner_with_farm(client, email="pin-floor-reset-owner@farm.in")
    role = await create_custom_role(client, owner, "Tablet reader", ["tasks.view"])
    membership = await _worker(
        client, owner, int(role["id"]), "pin-floor-reset-worker@farm.in", pin="012345"
    )
    endpoint = f"/api/team/workers/{membership}/reset-pin"
    before = await _credential_state(membership)
    async with _client() as tablet:
        signed_in = await tablet.post(
            "/api/auth/worker-login",
            json={"farm_id": int(owner["X-Farm-Id"]), "membership_id": membership, "pin": "012345"},
        )
        assert signed_in.status_code == 200, signed_in.text
        bearer = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
        headers = owner | {"Idempotency-Key": "pin-floor-reset"}
        denied = await client.post(endpoint, headers=headers, json={"pin": pin})
        assert denied.status_code == 400, denied.text
        assert (
            denied.json()["detail"] == "A worker PIN must be at least 6 digits on this deployment"
        )
        assert await _credential_state(membership) == before
        await _no_idempotent_result("team.workers.reset-pin")
        assert await _audit_count("team.worker.pin_reset") == 0
        assert (await tablet.get("/api/auth/me", headers=bearer)).status_code == 200
        assert (await tablet.post("/api/auth/refresh")).status_code == 200
        accepted = await client.post(endpoint, headers=headers, json={"pin": "543210"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.headers.get("Idempotency-Replayed") != "true"
        assert (await tablet.get("/api/auth/me", headers=bearer)).status_code == 401
        signed_in_again = await tablet.post(
            "/api/auth/worker-login",
            json={"farm_id": int(owner["X-Farm-Id"]), "membership_id": membership, "pin": "543210"},
        )
        assert signed_in_again.status_code == 200, signed_in_again.text


@pytest.mark.parametrize("credential", ["password", "pin"])
async def test_worker_creation_rechecks_a_role_deleted_during_credential_preparation(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, credential: str
) -> None:
    owner = await owner_with_farm(client, email="prepared-role-owner@farm.in")
    destination = await create_custom_role(client, owner, "Temporary destination", ["tasks.view"])
    fallback = await create_custom_role(client, owner, "Valid destination", ["tasks.view"])
    email = "prepared-role-worker@farm.in"
    payload = {
        "name": "Prepared worker",
        "email": email,
        "role_id": destination["id"],
        credential: "workerpass123" if credential == "password" else "012345",
    }
    entered, resume = asyncio.Event(), asyncio.Event()
    real_hash = team_api._hash_team_password

    async def prepared_hash(password: str, *, actor_id: int) -> str:
        result = await real_hash(password, actor_id=actor_id)
        if not entered.is_set():
            entered.set()
            await resume.wait()
        return result

    monkeypatch.setattr(team_api, "_hash_team_password", prepared_hash)
    headers = owner | {"Idempotency-Key": "prepared-role-create"}
    async with _client() as contender:
        attempt = asyncio.create_task(
            contender.post("/api/team/workers", headers=headers, json=payload)
        )
        try:
            await _at_boundary(entered, attempt)
            deleted = await client.delete(f"/api/team/roles/{destination['id']}", headers=owner)
            assert deleted.status_code == 204, deleted.text
            resume.set()
            denied = await asyncio.wait_for(attempt, timeout=10)
        finally:
            resume.set()
            if not attempt.done():
                attempt.cancel()
            await asyncio.gather(attempt, return_exceptions=True)
    assert denied.status_code == 400, denied.text
    assert denied.json()["detail"] == "Pick a valid role."
    await _no_account(email)
    await _no_idempotent_result("team.workers.create")
    assert await _audit_count("team.worker.create") == 0
    async with get_sessionmaker()() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(FarmMembership)
                .where(FarmMembership.role_id == destination["id"])
            )
            == 0
        )
    accepted = await client.post(
        "/api/team/workers", headers=headers, json=payload | {"role_id": fallback["id"]}
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.headers.get("Idempotency-Replayed") != "true"


@pytest.mark.parametrize("destination", ["absent", "other-farm"])
async def test_delegated_role_change_translates_invalid_destinations_without_mutation(
    client: httpx.AsyncClient, destination: str
) -> None:
    owner = await owner_with_farm(client, email="delegated-role-owner@farm.in")
    role = await create_custom_role(client, owner, "Ordinary reader", ["tasks.view"])
    membership = await _worker(client, owner, int(role["id"]), "delegated-role-worker@farm.in")
    manager, _ = await team_manager_headers(client, owner, extra_perms=["tasks.view"])
    if destination == "other-farm":
        foreign = await owner_with_farm(client, email="foreign-role-owner@farm.in")
        foreign_role = await create_custom_role(client, foreign, "Foreign reader", ["tasks.view"])
        target = int(foreign_role["id"])
    else:
        target = 2_147_483_647  # legal int4 path value, absent from the fresh test database
    before = await _credential_state(membership)
    assert (await client.get("/api/team", headers=manager)).status_code == 200
    denied = await client.post(
        f"/api/team/workers/{membership}/role", headers=manager, json={"role_id": target}
    )
    assert denied.status_code == 400, denied.text
    assert denied.json()["detail"] == "Pick a valid role."
    assert await _credential_state(membership) == before
    assert await _audit_count("team.worker.role_change") == 0
    async with get_sessionmaker()() as db:
        member = await db.get(FarmMembership, membership)
        current_role = await db.get(Role, int(role["id"]))
        assert member is not None and member.is_active and member.role_id == role["id"]
        assert current_role is not None and current_role.revision == role["revision"]
    assert (await client.get("/api/team", headers=manager)).status_code == 200
    assert (await client.get("/api/team", headers=owner)).status_code == 200


@pytest.mark.parametrize("competitor", ["rename", "create"])
async def test_concurrent_role_name_claims_roll_back_the_real_unique_constraint_loser(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, competitor: str
) -> None:
    owner = await owner_with_farm(client, email="role-name-owner@farm.in")
    first_role = await create_custom_role(client, owner, "First source", ["tasks.view"])
    second_role = await create_custom_role(client, owner, "Second source", ["tasks.view"])
    destination = "Exactly one live role"
    before_create = await _audit_count("team.role.create")
    before_update = await _audit_count("team.role.update")
    prepared: set[str] = set()
    both = asyncio.Event()
    first_stage = f"rename-{first_role['id']}"
    other_stage = "create" if competitor == "create" else f"rename-{second_role['id']}"
    release = {first_stage: asyncio.Event(), other_stage: asyncio.Event()}
    real_commit, real_flush = AsyncSession.commit, AsyncSession.flush

    async def ready(stage: str) -> None:
        prepared.add(stage)
        if len(prepared) == 2:
            both.set()
        await release[stage].wait()

    async def commit(db: AsyncSession) -> None:
        for obj in db.dirty:
            if isinstance(obj, Role) and obj.name == destination:
                await ready(f"rename-{obj.id}")
                break
        await real_commit(db)

    async def flush(db: AsyncSession, objects: Sequence[Any] | None = None) -> None:
        if any(isinstance(obj, Role) and obj.name == destination for obj in db.new):
            await ready("create")
        await real_flush(db, objects)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    monkeypatch.setattr(AsyncSession, "flush", flush)
    payload = {"name": destination, "permissions": ["tasks.view"], "expected_revision": 1}
    async with _client() as first_client, _client() as other_client:
        first = asyncio.create_task(
            first_client.put(f"/api/team/roles/{first_role['id']}", headers=owner, json=payload)
        )
        other = asyncio.create_task(
            other_client.put(f"/api/team/roles/{second_role['id']}", headers=owner, json=payload)
            if competitor == "rename"
            else other_client.post(
                "/api/team/roles",
                headers=owner,
                json={"name": destination, "permissions": ["tasks.view"]},
            )
        )
        waiter = asyncio.create_task(both.wait())
        try:
            await asyncio.wait(
                {waiter, first, other}, timeout=10, return_when=asyncio.FIRST_COMPLETED
            )
            assert both.is_set(), "Both real mutations must complete their nonconflicting preflight"
            # Both real preflights observed no collision. Commit the rename
            # before releasing the competing write, so PostgreSQL itself
            # deterministically rejects the stale name claim at flush/commit.
            release[first_stage].set()
            first_response = await asyncio.wait_for(first, timeout=10)
            release[other_stage].set()
            other_response = await asyncio.wait_for(other, timeout=10)
        finally:
            for gate in release.values():
                gate.set()
            waiter.cancel()
            for task in (first, other):
                if not task.done():
                    task.cancel()
            await asyncio.gather(waiter, first, other, return_exceptions=True)
    assert first_response.status_code == 200, first_response.text
    assert other_response.status_code == 400, other_response.text
    assert other_response.json()["detail"] == (
        "A role with that name already exists."
        if competitor == "create"
        else "Name is required and must be unique on this farm."
    )
    async with get_sessionmaker()() as db:
        roles = list(
            (
                await db.execute(
                    select(Role).where(
                        Role.farm_id == int(owner["X-Farm-Id"]), Role.deleted_at.is_(None)
                    )
                )
            ).scalars()
        )
        assert sum(role.name == destination for role in roles) == 1
        for original in (first_role, second_role):
            persisted = next(role for role in roles if role.id == original["id"])
            if persisted.name != destination:
                assert (persisted.name, persisted.revision) == (
                    original["name"],
                    original["revision"],
                )
    assert await _audit_count("team.role.create") == before_create
    assert await _audit_count("team.role.update") == before_update + 1
    assert (await client.get("/api/team", headers=owner)).status_code == 200
    assert (await client.get("/api/auth/me", headers=owner)).status_code == 200
