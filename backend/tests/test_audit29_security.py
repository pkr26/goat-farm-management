"""HTTP and transaction regressions for the independent audit's security fixes."""

import json

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_sessionmaker
from app.models import RefreshSession, SecurityEvent
from app.security import decode_access_claims

from .conftest import owner_with_farm, register
from .test_worker_pin_auth import _make_pin_worker, _worker_login


@pytest.mark.parametrize("value", ["9" * 5000, "0" * 5000, str(2**62), "9" * 19])
async def test_oversized_numeric_farm_selector_is_a_client_error(
    client: httpx.AsyncClient, value: str
) -> None:
    headers = await register(client, "farm-range@farm.in")
    result = await client.get("/api/animals", headers=headers | {"X-Farm-Id": value})
    assert result.status_code == 400, result.text
    assert result.json()["detail"] == "X-Farm-Id out of range"


async def test_many_leading_zeros_preserve_farm_authorization(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="zero-selector@farm.in")
    same = await client.get(
        "/api/auth/permissions", headers=owner | {"X-Farm-Id": "0" * 5000 + owner["X-Farm-Id"]}
    )
    assert same.status_code == 200
    foreign = await owner_with_farm(client, email="zero-foreign@farm.in")
    denied = await client.get(
        "/api/auth/permissions", headers=owner | {"X-Farm-Id": "0" * 5000 + foreign["X-Farm-Id"]}
    )
    assert denied.status_code == 404


async def _logout_events() -> list[SecurityEvent]:
    async with get_sessionmaker()() as db:
        return list(
            await db.scalars(select(SecurityEvent).where(SecurityEvent.event.like("auth.logout.%")))
        )


@pytest.mark.parametrize("mode", ["exact", "cookie", "bearer", "pin-cookie", "pin-bearer"])
async def test_logout_records_one_attributed_event_for_a_real_change(
    client: httpx.AsyncClient, mode: str
) -> None:
    headers = await register(client, "logout-event@farm.in")
    if mode.startswith("pin"):
        owner = await owner_with_farm(client, email="logout-pin-owner@farm.in")
        membership, farm = await _make_pin_worker(client, owner, email="logout-pin@farm.in")
        login = await _worker_login(client, farm, membership, "4321")
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    token = headers["Authorization"].removeprefix("Bearer ")
    claims = decode_access_claims(token)
    assert claims is not None
    cookie_name = get_settings().refresh_cookie_name
    refresh_cookie = client.cookies.get(cookie_name)
    assert refresh_cookie
    if mode.endswith("bearer"):
        client.cookies.clear()
    path = "/api/auth/logout-session" if mode == "exact" else "/api/auth/logout"
    sent_headers = headers if mode in {"exact", "bearer", "pin-bearer"} else {}
    response = await client.post(path, headers=sent_headers)
    assert response.status_code == 204, response.text
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    events = await _logout_events()
    assert len(events) == 1
    row = events[0]
    expected_event = "auth.logout.session_revoked" if mode == "exact" else "auth.logout.completed"
    assert row.event == expected_event
    assert row.targets["user_id"] == claims.user_id
    assert row.targets["family_id"] == claims.family_id
    if mode == "exact":
        assert row.targets["revoked_sessions"] == 1
    else:
        assert row.targets["session_origin"] == claims.scope.origin
        expected_scope = (
            "refresh_family"
            if mode.startswith("pin")
            else "all_refresh_sessions_and_access_generation"
            if mode == "bearer"
            else "refresh_family_and_access_generation"
        )
        assert row.targets["revocation_scope"] == expected_scope
    serialized = json.dumps(row.targets)
    assert token not in serialized and refresh_cookie not in serialized
    assert "@farm.in" not in serialized and "4321" not in serialized

    # Replay the original proof, including a stale cookie, rather than only
    # an empty post-logout browser. Neither path may manufacture another event.
    if mode not in {"bearer", "pin-bearer"}:
        client.cookies.clear()
        client.cookies.set(cookie_name, refresh_cookie, domain="test.local", path="/")
    for _ in range(3):
        replay = await client.post(path, headers=sent_headers)
        assert replay.status_code == (401 if mode == "exact" else 204), replay.text
    assert len(await _logout_events()) == 1


@pytest.mark.parametrize("path", ["/api/auth/logout-session", "/api/auth/logout"])
async def test_logout_event_and_revocation_roll_back_together(
    client: httpx.AsyncClient, path: str
) -> None:
    headers = await register(client, "logout-rollback@farm.in")
    claims = decode_access_claims(headers["Authorization"].removeprefix("Bearer "))
    assert claims is not None

    def reject_logout_commit(session: Session) -> None:
        if any(
            isinstance(row, SecurityEvent) and row.event.startswith("auth.logout.")
            for row in session.new
        ):
            raise RuntimeError("simulated storage failure before logout commit")

    event.listen(Session, "before_commit", reject_logout_commit)
    try:
        with pytest.raises(RuntimeError, match="simulated storage failure"):
            await client.post(path, headers=headers)
    finally:
        event.remove(Session, "before_commit", reject_logout_commit)
    assert await _logout_events() == []
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    async with get_sessionmaker()() as db:
        sessions = list(
            await db.scalars(
                select(RefreshSession).where(RefreshSession.family_id == claims.family_id)
            )
        )
        assert sessions and all(row.revoked_at is None for row in sessions)
