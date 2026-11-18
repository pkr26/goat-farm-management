"""Public team credential flows recheck authority and preserve locked lifecycle facts."""

import asyncio
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import team as team_api
from app.db import get_sessionmaker
from app.main import create_app
from app.models import Farm, FarmMembership, RefreshSession, Role, User
from app.security import decode_access_claims

from .conftest import OWNER_PW, login, login_and_rotate, owner_with_farm
from .test_team_extended import WORKER_PW, add_worker, role_id


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    )


async def _create_worker(
    client: httpx.AsyncClient, owner: dict[str, str], email: str
) -> tuple[int, int]:
    cleaner = await role_id(client, owner, "CLEANER")
    response = await add_worker(client, owner, cleaner, email)
    assert response.status_code == 201, response.text
    return int(response.json()["id"]), int(response.json()["user_id"])


async def _credential_state(
    membership_id: int,
) -> tuple[str, int, bool, str | None, datetime | None]:
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(
                select(
                    User.password_hash,
                    User.token_version,
                    User.must_change_password,
                    FarmMembership.pin_hash,
                    FarmMembership.pin_updated_at,
                )
                .join(FarmMembership, FarmMembership.user_id == User.id)
                .where(FarmMembership.id == membership_id)
            )
        ).one()
        return row[0], row[1], row[2], row[3], row[4]


async def _at_boundary(entered: asyncio.Event, attempt: asyncio.Task[httpx.Response]) -> None:
    ready = asyncio.create_task(entered.wait())
    try:
        await asyncio.wait({ready, attempt}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        assert entered.is_set(), "The public request must reach its real preparation/lock boundary"
    finally:
        ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)


@pytest.mark.parametrize("operation", ["reset-password", "reset-pin"])
@pytest.mark.parametrize("invalidation", ["access-generation", "farm-ownership"])
async def test_credential_reset_reauthorizes_the_owner_after_off_transaction_hashing(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    invalidation: str,
) -> None:
    owner = await owner_with_farm(client, email="reauthorization-owner@farm.in")
    membership_id, _ = await _create_worker(client, owner, "reauthorization-target@farm.in")
    successor_id, _ = await _create_worker(client, owner, "reauthorization-successor@farm.in")
    async with _client() as successor:
        await login_and_rotate(successor, "reauthorization-successor@farm.in", WORKER_PW)
    before = await _credential_state(membership_id)
    entered, resume = asyncio.Event(), asyncio.Event()
    real_hash = team_api._hash_team_password

    async def completed_hash(password: str, *, actor_id: int) -> str:
        result = await real_hash(password, actor_id=actor_id)
        entered.set()
        await resume.wait()
        return result

    monkeypatch.setattr(team_api, "_hash_team_password", completed_hash)
    payload = (
        {"password": "must-not-replace123"} if operation == "reset-password" else {"pin": "9876"}
    )
    async with _client() as contender:
        attempt = asyncio.create_task(
            contender.post(
                f"/api/team/workers/{membership_id}/{operation}", headers=owner, json=payload
            )
        )
        try:
            await _at_boundary(entered, attempt)
            if invalidation == "access-generation":
                changed = await client.post(
                    "/api/auth/change-password",
                    headers=owner,
                    json={"current_password": OWNER_PW, "new_password": "changed-owner-pass123"},
                )
                assert changed.status_code == 200, changed.text
            else:
                changed = await client.post(
                    f"/api/auth/farms/{owner['X-Farm-Id']}/transfer-ownership",
                    headers=owner,
                    json={"membership_id": successor_id, "current_password": OWNER_PW},
                )
                assert changed.status_code == 200, changed.text
            resume.set()
            response = await asyncio.wait_for(attempt, timeout=10)
        finally:
            resume.set()
            if not attempt.done():
                attempt.cancel()
            await asyncio.gather(attempt, return_exceptions=True)
    expected = (
        (401, "Session has been revoked")
        if invalidation == "access-generation"
        else (404, "Farm not found")
    )
    assert (response.status_code, response.json()["detail"]) == expected, response.text
    assert await _credential_state(membership_id) == before


async def test_password_reset_rechecks_membership_eligibility_after_hashing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="reset-eligibility-owner@farm.in")
    membership_id, _ = await _create_worker(client, owner, "reset-eligibility-worker@farm.in")
    before = await _credential_state(membership_id)
    entered, resume = asyncio.Event(), asyncio.Event()
    real_hash = team_api._hash_team_password

    async def completed_hash(password: str, *, actor_id: int) -> str:
        result = await real_hash(password, actor_id=actor_id)
        entered.set()
        await resume.wait()
        return result

    monkeypatch.setattr(team_api, "_hash_team_password", completed_hash)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as contender:
        attempt = asyncio.create_task(
            contender.post(
                f"/api/team/workers/{membership_id}/reset-password",
                headers=owner,
                json={"password": "must-not-replace123"},
            )
        )
        try:
            await _at_boundary(entered, attempt)
            inactive = await client.put(
                f"/api/team/workers/{membership_id}/status",
                headers=owner,
                json={"is_active": False},
            )
            assert inactive.status_code == 200, inactive.text
            resume.set()
            response = await asyncio.wait_for(attempt, timeout=10)
        finally:
            resume.set()
            if not attempt.done():
                attempt.cancel()
            await asyncio.gather(attempt, return_exceptions=True)
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Reactivate this membership before resetting the password."
    assert await _credential_state(membership_id) == before


async def test_password_reset_cannot_rewrite_an_account_deleted_after_the_membership_lock(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="reset-tombstone-owner@farm.in")
    membership_id, worker_id = await _create_worker(client, owner, "reset-tombstone-worker@farm.in")
    entered, resume = asyncio.Event(), asyncio.Event()
    real_membership = team_api._get_membership

    async def locked_membership(
        db: AsyncSession,
        farm: Farm,
        target_id: int,
        *,
        for_update: bool = False,
        no_key_update: bool = False,
    ) -> FarmMembership:
        membership = await real_membership(
            db, farm, target_id, for_update=for_update, no_key_update=no_key_update
        )
        if for_update and target_id == membership_id:
            entered.set()
            await resume.wait()
        return membership

    async with _client() as worker_client, _client() as contender:
        worker = await login_and_rotate(worker_client, "reset-tombstone-worker@farm.in", WORKER_PW)
        monkeypatch.setattr(team_api, "_get_membership", locked_membership)
        attempt = asyncio.create_task(
            contender.post(
                f"/api/team/workers/{membership_id}/reset-password",
                headers=owner,
                json={"password": "must-not-replace123"},
            )
        )
        try:
            await _at_boundary(entered, attempt)
            deleted = await worker_client.request(
                "DELETE",
                "/api/auth/account",
                headers=worker,
                json={"current_password": WORKER_PW + "!r1"},
            )
            assert deleted.status_code == 204, deleted.text
            tombstone = await _credential_state(membership_id)
            resume.set()
            response = await asyncio.wait_for(attempt, timeout=10)
        finally:
            resume.set()
            if not attempt.done():
                attempt.cancel()
            await asyncio.gather(attempt, return_exceptions=True)
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Worker account no longer exists"
    assert await _credential_state(membership_id) == tombstone
    async with get_sessionmaker()() as db:
        account = await db.get(User, worker_id)
        assert account is not None and account.deleted_at is not None


async def test_pin_rotation_revokes_both_session_scopes_once_even_when_the_response_is_replayed(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="pin-revocation-owner@farm.in")
    membership_id, worker_id = await _create_worker(client, owner, "pin-revocation-worker@farm.in")
    async with _client() as password_client, _client() as pin_client:
        await login_and_rotate(password_client, "pin-revocation-worker@farm.in", WORKER_PW)
        initial_pin = await client.post(
            f"/api/team/workers/{membership_id}/reset-pin", headers=owner, json={"pin": "4321"}
        )
        assert initial_pin.status_code == 200, initial_pin.text
        password = await login(password_client, "pin-revocation-worker@farm.in", WORKER_PW + "!r1")
        pin_login = await pin_client.post(
            "/api/auth/worker-login",
            json={
                "farm_id": int(owner["X-Farm-Id"]),
                "membership_id": membership_id,
                "pin": "4321",
            },
        )
        assert pin_login.status_code == 200, pin_login.text
        pin = {"Authorization": f"Bearer {pin_login.json()['access_token']}"}
        claims = [
            decode_access_claims(headers["Authorization"].removeprefix("Bearer "))
            for headers in (password, pin)
        ]
        assert all(claim is not None for claim in claims)
        families = {claim.family_id for claim in claims if claim is not None}
        assert len(families) == 2
        before = await _credential_state(membership_id)

        headers = owner | {"Idempotency-Key": "pin-rotation-once"}
        rotated = await client.post(
            f"/api/team/workers/{membership_id}/reset-pin", headers=headers, json={"pin": "9876"}
        )
        assert rotated.status_code == 200, rotated.text
        rotated_state = await _credential_state(membership_id)
        assert rotated_state[1] > before[1], (
            "A credential change must advance the access generation"
        )
        replayed = await client.post(
            f"/api/team/workers/{membership_id}/reset-pin", headers=headers, json={"pin": "9876"}
        )
        assert (
            replayed.status_code == 200 and replayed.headers.get("Idempotency-Replayed") == "true"
        )
        after = await _credential_state(membership_id)
        assert after == rotated_state, (
            "Replaying a committed reset must not rotate the credential again"
        )
        assert after[0] == before[0], "PIN rotation must preserve the independently owned password"
        async with get_sessionmaker()() as db:
            sessions = list(
                (
                    await db.execute(
                        select(RefreshSession).where(
                            RefreshSession.user_id == worker_id,
                            RefreshSession.family_id.in_(families),
                        )
                    )
                ).scalars()
            )
            assert {session.family_id for session in sessions} == families
            assert all(session.revoked_at is not None for session in sessions)
        for browser, bearer in ((password_client, password), (pin_client, pin)):
            assert (await browser.get("/api/auth/me", headers=bearer)).status_code == 401
            assert (await browser.post("/api/auth/refresh")).status_code == 401
        accepted = await pin_client.post(
            "/api/auth/worker-login",
            json={
                "farm_id": int(owner["X-Farm-Id"]),
                "membership_id": membership_id,
                "pin": "9876",
            },
        )
        assert accepted.status_code == 200, accepted.text


async def _blocked_or_finished(
    attempt: asyncio.Task[httpx.Response], holder_pid: int, prepared: asyncio.Event | None = None
) -> bool:
    async with asyncio.timeout(5):
        while not attempt.done():
            if prepared is not None and prepared.is_set():
                return False
            async with get_sessionmaker()() as db:
                blocked = await db.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND :holder = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"holder": holder_pid},
                )
            if blocked:
                return True
            await asyncio.sleep(0.01)
    return False


async def test_role_reassignment_pins_the_destination_until_membership_commit(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="role-pin-owner@farm.in")
    membership_id, _ = await _create_worker(client, owner, "role-pin-worker@farm.in")
    created = await client.post(
        "/api/team/roles",
        headers=owner,
        json={"name": "Destination", "permissions": ["tasks.view"]},
    )
    assert created.status_code == 201, created.text
    destination = int(created.json()["id"])
    entered, resume = asyncio.Event(), asyncio.Event()
    holder_pid: int | None = None
    real_role = team_api._get_role

    async def pinned_role(
        db: AsyncSession,
        farm: Farm,
        target_id: int,
        *,
        for_update: bool = False,
        no_key_update: bool = False,
        for_share: bool = False,
    ) -> Role:
        nonlocal holder_pid
        role = await real_role(
            db,
            farm,
            target_id,
            for_update=for_update,
            no_key_update=no_key_update,
            for_share=for_share,
        )
        if target_id == destination and not for_update:
            holder_pid = await db.scalar(text("SELECT pg_backend_pid()"))
            entered.set()
            await resume.wait()
        return role

    monkeypatch.setattr(team_api, "_get_role", pinned_role)
    async with _client() as mover, _client() as remover:
        assignment = asyncio.create_task(
            mover.post(
                f"/api/team/workers/{membership_id}/role",
                headers=owner,
                json={"role_id": destination},
            )
        )
        deletion: asyncio.Task[httpx.Response] | None = None
        try:
            await _at_boundary(entered, assignment)
            assert holder_pid is not None
            deletion = asyncio.create_task(
                remover.delete(f"/api/team/roles/{destination}", headers=owner)
            )
            blocked = await _blocked_or_finished(deletion, holder_pid)
            resume.set()
            assigned, deleted = await asyncio.wait_for(
                asyncio.gather(assignment, deletion), timeout=10
            )
        finally:
            resume.set()
            pending = [task for task in (assignment, deletion) if task is not None]
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    assert blocked, "Deletion must wait for the assignment's actual PostgreSQL role lock"
    assert assigned.status_code == 200, assigned.text
    assert deleted.status_code == 400, deleted.text
    assert deleted.json()["detail"] == "Role still has workers assigned — reassign them first."
    async with get_sessionmaker()() as db:
        membership = await db.get(FarmMembership, membership_id)
        role = await db.get(Role, destination)
        assert membership is not None and membership.role_id == destination
        assert role is not None and role.deleted_at is None


@pytest.mark.parametrize("operation", ["role", "status"])
async def test_transferred_owner_cannot_reassign_or_deactivate_their_retained_membership(
    client: httpx.AsyncClient, operation: str
) -> None:
    owner = await owner_with_farm(client, email="retained-owner-predecessor@farm.in")
    membership_id, successor_id = await _create_worker(client, owner, "retained-owner@farm.in")
    async with _client() as successor:
        await login_and_rotate(successor, "retained-owner@farm.in", WORKER_PW)
        transferred = await client.post(
            f"/api/auth/farms/{owner['X-Farm-Id']}/transfer-ownership",
            headers=owner,
            json={"membership_id": membership_id, "current_password": OWNER_PW},
        )
        assert transferred.status_code == 200, transferred.text
        # Transfer revokes the successor's prior sessions. Reauthenticate as
        # the new owner instead of injecting an owner ID or bypassing auth.
        new_owner = await login(successor, "retained-owner@farm.in", WORKER_PW + "!r1")
        new_owner["X-Farm-Id"] = owner["X-Farm-Id"]
        mover = await role_id(successor, new_owner, "MOVER")
        if operation == "role":
            response = await successor.post(
                f"/api/team/workers/{membership_id}/role",
                headers=new_owner,
                json={"role_id": mover},
            )
            expected = "You cannot change your own role."
        else:
            response = await successor.put(
                f"/api/team/workers/{membership_id}/status",
                headers=new_owner,
                json={"is_active": False},
            )
            expected = "You cannot deactivate your own membership."
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == expected
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        membership = await db.get(FarmMembership, membership_id)
        assert farm is not None and farm.owner_id == successor_id
        assert membership is not None and membership.is_active is True
        assert membership.role_id != mover


async def test_concurrent_pin_resets_for_a_transferred_owner_serialize_before_mutation(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    predecessor = await owner_with_farm(client, email="self-pin-predecessor@farm.in")
    membership_id, actor_id = await _create_worker(client, predecessor, "self-pin-owner@farm.in")
    async with _client() as successor:
        await login_and_rotate(successor, "self-pin-owner@farm.in", WORKER_PW)
        transferred = await client.post(
            f"/api/auth/farms/{predecessor['X-Farm-Id']}/transfer-ownership",
            headers=predecessor,
            json={"membership_id": membership_id, "current_password": OWNER_PW},
        )
        assert transferred.status_code == 200, transferred.text
        owner = await login(successor, "self-pin-owner@farm.in", WORKER_PW + "!r1")
    owner["X-Farm-Id"] = predecessor["X-Farm-Id"]
    before = await _credential_state(membership_id)
    first_prepared, second_prepared = asyncio.Event(), asyncio.Event()
    release_first, release_second = asyncio.Event(), asyncio.Event()
    pids: list[int] = []
    real_reauthorize = team_api._reauthorize_prepared_owner

    async def prepared_owner(db: AsyncSession, **kwargs: Any) -> tuple[User, Farm]:
        # Both requests perform actual Argon work and authorization. The seam
        # only parks them at the dangerous transaction boundary.
        pid = await db.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        position = len(pids)
        pids.append(pid)
        user, farm = await real_reauthorize(db, **kwargs)
        if position == 0:
            first_prepared.set()
            await release_first.wait()
        else:
            second_prepared.set()
            await release_second.wait()
        return user, farm

    monkeypatch.setattr(team_api, "_reauthorize_prepared_owner", prepared_owner)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
            base_url="http://test",
        ) as first_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
            base_url="http://test",
        ) as second_client,
    ):
        first = asyncio.create_task(
            first_client.post(
                f"/api/team/workers/{membership_id}/reset-pin", headers=owner, json={"pin": "4321"}
            )
        )
        second: asyncio.Task[httpx.Response] | None = None
        try:
            await _at_boundary(first_prepared, first)
            second = asyncio.create_task(
                second_client.post(
                    f"/api/team/workers/{membership_id}/reset-pin",
                    headers=owner,
                    json={"pin": "9876"},
                )
            )
            waits_before_preparation = await _blocked_or_finished(second, pids[0], second_prepared)
            release_first.set()
            if not waits_before_preparation:
                # A shared actor pin lets both requests prepare. Prove the
                # first is now waiting on the second's real User lock before
                # allowing the inverse membership wait to proceed.
                assert second_prepared.is_set()
                assert await _blocked_or_finished(first, pids[1])
            release_second.set()
            first_response, second_response = await asyncio.wait_for(
                asyncio.gather(first, second), timeout=10
            )
        finally:
            release_first.set()
            release_second.set()
            pending = [task for task in (first, second) if task is not None]
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 401, second_response.text
    assert second_response.json()["detail"] == "Session has been revoked"
    after = await _credential_state(membership_id)
    assert after[1] > before[1]
    async with get_sessionmaker()() as db:
        user = await db.get(User, actor_id)
        assert user is not None and user.deleted_at is None
