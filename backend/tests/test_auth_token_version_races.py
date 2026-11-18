"""Global token-revocation serialization regressions."""

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import HTTPException, Response
from sqlalchemy import event, func, select, text
from starlette.requests import Request

import app.api.auth as auth_api
from app.api.auth import create_farm
from app.db import get_engine, get_sessionmaker
from app.main import create_app
from app.models import Farm, FarmMembership, RefreshSession, User
from app.schemas.auth import FarmCreateIn
from app.security import verify_password_async as real_verify_password_async
from app.security import verify_password_with_work_async as real_verify_password_with_work_async

from .conftest import login_and_rotate, owner_with_farm
from .type_helpers import json_object

WORKER_PASSWORD = "workerpass123"


async def _wait_for_lock_waiters(minimum: int, timeout_seconds: float = 10.0) -> None:
    for _ in range(int(timeout_seconds / 0.01)):
        async with get_sessionmaker()() as db:
            blocked = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() "
                        "AND wait_event_type = 'Lock'"
                    )
                )
            ).scalar_one()
        if blocked >= minimum:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {minimum} lock waiters, saw fewer")


async def _provision_worker(
    client: httpx.AsyncClient,
    owner: dict[str, str],
    email: str,
) -> dict[str, Any]:
    team = await client.get("/api/team", headers=owner)
    assert team.status_code == 200, team.text
    role_id = next(role["id"] for role in team.json()["roles"] if role["code"] == "CLEANER")
    created = await client.post(
        "/api/team/workers",
        json={
            "email": email,
            "name": "Token race worker",
            "password": WORKER_PASSWORD,
            "role_id": role_id,
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    return json_object(created.json())


async def test_password_reset_winning_rejects_stale_create_farm_principal(
    client: httpx.AsyncClient,
) -> None:
    """A request authorized before reset cannot acquire ownership afterward.

    Keeping the first session's User in its identity map models the exact
    in-flight dependency snapshot. The route's locked populate-existing read
    must compare against that pre-await version, not the refreshed object.
    """
    owner = await owner_with_farm(
        client,
        email="reset-race-owner@farm.in",
        farm_name="Reset Race Farm",
    )
    membership = await _provision_worker(client, owner, "reset-race-worker@farm.in")
    worker_id = membership["user_id"]

    async with get_sessionmaker()() as stale_request_db:
        stale_principal = await stale_request_db.get(User, worker_id)
        assert stale_principal is not None
        authenticated_version = stale_principal.token_version

        reset = await client.post(
            f"/api/team/workers/{membership['id']}/reset-password",
            json={"password": "replacement-pass-123"},
            headers=owner,
        )
        assert reset.status_code == 200, reset.text
        # The in-flight request still holds the version CurrentUser approved.
        assert stale_principal.token_version == authenticated_version

        with pytest.raises(HTTPException) as exc_info:
            await create_farm(
                FarmCreateIn(name="Must Not Exist"),
                Response(),
                Request(
                    {"type": "http", "method": "POST", "path": "/api/auth/farms", "headers": []}
                ),
                stale_request_db,
                stale_principal,
                idempotency_key="stale-create-farm-key",
            )
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Session is no longer valid"
        await stale_request_db.rollback()

    async with get_sessionmaker()() as db:
        owned = (
            await db.execute(
                select(func.count()).select_from(Farm).where(Farm.owner_id == worker_id)
            )
        ).scalar_one()
    assert owned == 0


async def test_farm_creation_winning_makes_owner_reset_ineligible(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(
        client,
        email="affiliation-owner@farm.in",
        farm_name="Affiliation Farm",
    )
    membership = await _provision_worker(client, owner, "affiliation-worker@farm.in")
    worker_headers = await login_and_rotate(client, "affiliation-worker@farm.in", WORKER_PASSWORD)
    created = await client.post(
        "/api/auth/farms",
        json={"name": "Worker-owned Farm"},
        headers=worker_headers,
    )
    assert created.status_code == 201, created.text

    reset = await client.post(
        f"/api/team/workers/{membership['id']}/reset-password",
        json={"password": "must-not-be-applied"},
        headers=owner,
    )
    assert reset.status_code == 400
    assert reset.json()["detail"] == "This account must use self-service password recovery."


async def test_unsafe_worker_authorization_uses_reset_safe_lock_order(
    client: httpx.AsyncClient,
) -> None:
    """A worker mutation must never invert owner-reset's target lock order."""
    owner = await owner_with_farm(
        client,
        email="auth-order-owner@farm.in",
        farm_name="Authorization Order Farm",
    )
    email = "auth-order-worker@farm.in"
    membership = await _provision_worker(client, owner, email)
    worker_headers = await login_and_rotate(client, email, WORKER_PASSWORD)
    worker_headers = worker_headers | {"X-Farm-Id": owner["X-Farm-Id"]}
    task = await client.post(
        "/api/tasks",
        json={
            "title": "Lock-order duty",
            "due_date": "2026-08-08",
            "category": "CLEANING",
            "assigned_user_id": membership["user_id"],
        },
        headers=owner,
    )
    assert task.status_code == 201, task.text

    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.lower().split())
        if "for share" in normalized:
            statements.append(normalized)

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        completed = await client.post(
            f"/api/tasks/{task.json()['id']}/complete",
            headers=worker_headers,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
    assert completed.status_code == 200, completed.text

    membership_lock = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("select farm_memberships.")
    )
    user_lock = next(
        index for index, statement in enumerate(statements) if statement.startswith("select users.")
    )
    role_lock = next(
        index for index, statement in enumerate(statements) if statement.startswith("select roles.")
    )
    assert membership_lock < user_lock < role_lock, statements


@pytest.mark.parametrize("operation", ["login", "change-password", "account-delete"])
async def test_reset_winning_during_argon_rejects_exact_stale_snapshot(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    owner = await owner_with_farm(
        client,
        email=f"{operation}-race-owner@farm.in",
        farm_name="Password Snapshot Race Farm",
    )
    email = f"{operation}-race-worker@farm.in"
    membership = await _provision_worker(client, owner, email)
    worker_id = membership["user_id"]
    worker_headers = await login_and_rotate(client, email, WORKER_PASSWORD)

    started = asyncio.Event()
    release = asyncio.Event()

    async def stalled_verify(password: str, stored: str) -> tuple[bool, bool]:
        started.set()
        await release.wait()
        return await real_verify_password_async(password, stored)

    async def stalled_verify_with_work(password: str, stored: str) -> tuple[bool, bool, bool]:
        started.set()
        await release.wait()
        return await real_verify_password_with_work_async(password, stored)

    monkeypatch.setattr(auth_api, "verify_password_async", stalled_verify)
    monkeypatch.setattr(auth_api, "verify_password_with_work_async", stalled_verify_with_work)
    if operation == "login":
        method = "POST"
        path = "/api/auth/login"
        payload = {"email": email, "password": WORKER_PASSWORD}
        headers = None
    elif operation == "change-password":
        method = "POST"
        path = "/api/auth/change-password"
        payload = {
            "current_password": WORKER_PASSWORD + "!r1",
            "new_password": "stale-change-must-not-land",
        }
        headers = worker_headers
    else:
        method = "DELETE"
        path = "/api/auth/account"
        payload = {"current_password": WORKER_PASSWORD + "!r1"}
        headers = worker_headers

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://test",
    ) as contender:
        attempt = asyncio.create_task(
            contender.request(method, path, json=payload, headers=headers)
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            reset = await client.post(
                f"/api/team/workers/{membership['id']}/reset-password",
                json={"password": "reset-wins-pass123"},
                headers=owner,
            )
            assert reset.status_code == 200, reset.text
        finally:
            release.set()
        stale_response = await attempt

    assert stale_response.status_code == 401, stale_response.text
    expected_detail = (
        "Invalid email or password." if operation == "login" else "Session is no longer valid"
    )
    assert stale_response.json()["detail"] == expected_detail
    async with get_sessionmaker()() as db:
        worker = await db.get(User, worker_id)
        sessions = (
            await db.execute(
                select(func.count())
                .select_from(RefreshSession)
                .where(
                    RefreshSession.user_id == worker_id,
                    RefreshSession.revoked_at.is_(None),
                )
            )
        ).scalar_one()
    assert worker is not None and worker.deleted_at is None
    assert sessions == 0


@pytest.mark.parametrize("lifecycle", ["status", "role"])
async def test_account_tombstone_serializes_before_roster_mutation(
    client: httpx.AsyncClient,
    lifecycle: str,
) -> None:
    """Roster edits revalidate a target deleted after the real Membership read."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api import team as team_api

    owner = await owner_with_farm(
        client,
        email=f"{lifecycle}-tombstone-owner@farm.in",
        farm_name="Roster Tombstone Race Farm",
    )
    email = f"{lifecycle}-tombstone-worker@farm.in"
    membership = await _provision_worker(client, owner, email)
    worker_id = membership["user_id"]
    worker_headers = await login_and_rotate(client, email, WORKER_PASSWORD)

    if lifecycle == "status":
        deactivated = await client.put(
            f"/api/team/workers/{membership['id']}/status",
            json={"is_active": False},
            headers=owner,
        )
        assert deactivated.status_code == 200, deactivated.text
        path = f"/api/team/workers/{membership['id']}/status"
        payload = {"is_active": True}
        expected_role_id = membership["role_id"]
    else:
        team = await client.get("/api/team", headers=owner)
        assert team.status_code == 200, team.text
        replacement_role_id = next(
            role["id"] for role in team.json()["roles"] if role["code"] == "VET"
        )
        path = f"/api/team/workers/{membership['id']}/role"
        payload = {"role_id": replacement_role_id}
        expected_role_id = membership["role_id"]

    entered, resume = asyncio.Event(), asyncio.Event()
    real_membership = team_api._get_membership

    async def after_real_membership_read(
        db: AsyncSession,
        farm: Farm,
        target_id: int,
        *,
        for_update: bool = False,
        no_key_update: bool = False,
    ) -> FarmMembership:
        membership_row = await real_membership(
            db, farm, target_id, for_update=for_update, no_key_update=no_key_update
        )
        if for_update and target_id == membership["id"]:
            # The real query has locked Membership and loaded the live User.
            # Public deletion intentionally does not lock retained Memberships.
            entered.set()
            await resume.wait()
        return membership_row

    early_response: httpx.Response | None = None
    deletion_response: httpx.Response | None = None
    roster_response: httpx.Response | None = None
    schedule_error: BaseException | None = None
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(team_api, "_get_membership", after_real_membership_read)
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
            ) as delete_client,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
            ) as roster_client,
        ):
            roster = asyncio.create_task(
                roster_client.request(
                    "PUT" if lifecycle == "status" else "POST",
                    path,
                    json=payload,
                    headers=owner,
                )
            )
            observed = asyncio.create_task(entered.wait())
            owned_requests = [roster]
            try:
                done, _ = await asyncio.wait(
                    {observed, roster}, timeout=5, return_when=asyncio.FIRST_COMPLETED
                )
                if roster in done:
                    early_response = await roster
                elif observed in done:
                    await observed
                    deletion = asyncio.create_task(
                        delete_client.request(
                            "DELETE",
                            "/api/auth/account",
                            json={"current_password": WORKER_PASSWORD + "!r1"},
                            headers=worker_headers,
                        )
                    )
                    owned_requests.append(deletion)
                    async with asyncio.timeout(15):
                        deletion_response = await deletion
                    resume.set()
                    async with asyncio.timeout(10):
                        roster_response = await roster
                else:
                    raise TimeoutError("The owned roster request did not finish or reach its read")
            except BaseException as exc:
                schedule_error = exc
            finally:
                resume.set()
                if not observed.done():
                    observed.cancel()
                pending = set(owned_requests)
                try:
                    _, pending = await asyncio.wait(owned_requests, timeout=10)
                finally:
                    for request in pending:
                        if not request.done():
                            request.cancel()
                    # Drain every actual actor before clients and patch close.
                    # The final cancellation gather has no local bound; the
                    # external phase budget remains the hard bound.
                    outcomes = await asyncio.gather(*owned_requests, return_exceptions=True)
                    await asyncio.gather(observed, return_exceptions=True)

    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise RuntimeError("An owned deletion/roster request failed") from outcome
    if schedule_error is not None:
        raise RuntimeError("The owned deletion/roster schedule failed") from schedule_error
    if early_response is not None:
        assert early_response.status_code == 200, (
            "An owner may change a live worker before account deletion"
        )
        raise RuntimeError("The real Membership read/deletion interleaving was not witnessed")
    if deletion_response is None or roster_response is None:
        raise RuntimeError("The owned deletion/roster responses are incomplete")
    assert deletion_response.status_code == 204, deletion_response.text
    assert roster_response.status_code == 404, (
        "A roster mutation must reject a worker whose account deletion already completed"
    )
    assert roster_response.json()["detail"] == "Membership not found"
    async with get_sessionmaker()() as db:
        stored = await db.get(FarmMembership, membership["id"])
        account = await db.get(User, worker_id)
    assert account is not None and account.deleted_at is not None
    assert stored is not None
    assert stored.role_id == expected_role_id
    expected_active = lifecycle != "status"
    assert stored.is_active is expected_active
