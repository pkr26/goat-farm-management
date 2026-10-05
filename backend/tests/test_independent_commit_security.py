"""Fresh HTTP/concurrency acceptance checks for the ad2f616 security fixes."""

import asyncio

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db import get_sessionmaker
from app.main import create_app
from app.models import SecurityEvent
from app.security import decode_access_claims

from .conftest import owner_with_farm, register


@pytest.mark.parametrize("mode", ["exact", "cookie", "bearer"])
async def test_simultaneous_logout_emits_exactly_one_attributed_transition(
    client: httpx.AsyncClient, mode: str
) -> None:
    headers = await register(client, "independent-concurrent@farm.in")
    claims = decode_access_claims(headers["Authorization"].removeprefix("Bearer "))
    assert claims is not None
    cookie_name = get_settings().refresh_cookie_name
    cookie = client.cookies.get(cookie_name)
    assert cookie
    path = "/api/auth/logout-session" if mode == "exact" else "/api/auth/logout"

    async def revoke() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as requester:
            if mode == "cookie":
                requester.cookies.set(cookie_name, cookie, domain="test.local", path="/")
            return await requester.post(path, headers={} if mode == "cookie" else headers)

    results = await asyncio.gather(*(revoke() for _ in range(6)))
    assert 204 in {response.status_code for response in results}
    assert all(response.status_code in {204, 401} for response in results)
    if mode != "exact":
        assert all(response.status_code == 204 for response in results)
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    async with get_sessionmaker()() as db:
        events = list(
            await db.scalars(select(SecurityEvent).where(SecurityEvent.event.like("auth.logout.%")))
        )
    assert len(events) == 1
    assert events[0].targets["user_id"] == claims.user_id
    assert events[0].targets["family_id"] == claims.family_id


async def test_ambiguous_logout_proofs_cannot_revoke_or_attribute_either_account(
    client: httpx.AsyncClient,
) -> None:
    first = await register(client, "independent-first@farm.in")
    first_cookie = client.cookies.get(get_settings().refresh_cookie_name)
    second = await register(client, "independent-second@farm.in")
    client.cookies.clear()
    assert first_cookie
    client.cookies.set(
        get_settings().refresh_cookie_name, first_cookie, domain="test.local", path="/"
    )
    rejected = await client.post("/api/auth/logout", headers=second)
    assert rejected.status_code == 401
    for headers in (first, second):
        assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    async with get_sessionmaker()() as db:
        assert (
            list(
                await db.scalars(
                    select(SecurityEvent).where(SecurityEvent.event.like("auth.logout.%"))
                )
            )
            == []
        )


@pytest.mark.parametrize("value", [str(2**31), str(2**62 - 1), "0" * 10000 + str(2**31)])
async def test_large_in_range_farm_selectors_are_bounded_without_database_overflow(
    client: httpx.AsyncClient, value: str
) -> None:
    headers = await owner_with_farm(client, email="independent-header@farm.in")
    result = await client.get("/api/animals", headers=headers | {"X-Farm-Id": value})
    assert result.status_code == 404, result.text
