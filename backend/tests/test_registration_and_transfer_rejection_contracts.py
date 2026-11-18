"""Public registration and ownership-transfer rejection contracts.

WORK-only draft: copy to the normal tests package only after approval.
"""

import asyncio
from collections.abc import Iterator, Sequence
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import auth as auth_api
from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import Farm, User
from app.ratelimit import auth_limiter
from app.schemas.common import MAX_INT32_ID

from .conftest import OWNER_PW, login_and_rotate, owner_with_farm
from .test_session_privacy import _password_worker


@pytest.fixture(autouse=True)
def _fresh_registration_admission(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Keep admission enabled with a supported validated policy. Two attempts
    # remain below its ceiling; rate limiting is not this test's oracle.
    values = {
        "auth_rate_limit_enabled": True,
        "auth_rate_limit_max_attempts": 8,
        "auth_rate_limit_window_seconds": 3600,
        "min_password_length": 12,
    }
    settings = get_settings()
    validated = Settings.model_validate(settings.model_dump() | values)
    for name in values:
        monkeypatch.setattr(settings, name, getattr(validated, name))
    auth_limiter.clear()
    auth_api.register_email_limiter.clear()
    yield
    auth_limiter.clear()
    auth_api.register_email_limiter.clear()


def _raising_browser(client: httpx.AsyncClient) -> httpx.AsyncClient:
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("These contracts require the real application ASGI transport")
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport.app, raise_app_exceptions=True),
        base_url="http://test",
        event_hooks=client.event_hooks,
    )


async def _ready_successor(client: httpx.AsyncClient, owner: dict[str, str], email: str) -> int:
    membership = await _password_worker(client, owner, email)
    await login_and_rotate(client, email, "workerpass123")
    return int(membership["id"])


async def _farm_owners() -> dict[int, int]:
    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(Farm.id, Farm.owner_id))).tuples().all()
        return dict(rows)


@pytest.mark.parametrize("farm_id", [0, -1, MAX_INT32_ID + 1])
async def test_transfer_rejects_out_of_database_range_without_touching_ownership(
    client: httpx.AsyncClient, farm_id: int
) -> None:
    async with _raising_browser(client) as browser:
        owner = await owner_with_farm(browser, "invalid-transfer-owner@farm.in")
        membership_id = await _ready_successor(browser, owner, "invalid-transfer-successor@farm.in")
        before = await _farm_owners()
        denied = await browser.post(
            f"/api/auth/farms/{farm_id}/transfer-ownership",
            headers=owner,
            json={"membership_id": membership_id, "current_password": OWNER_PW},
        )
    assert denied.status_code == 404, denied.text
    assert await _farm_owners() == before


async def test_transfer_hides_another_owners_farm_as_an_absent_identity(
    client: httpx.AsyncClient,
) -> None:
    async with _raising_browser(client) as browser:
        caller = await owner_with_farm(browser, "nonowner-transfer-caller@farm.in")
        other = await owner_with_farm(browser, "nonowner-transfer-owner@farm.in")
        membership_id = await _ready_successor(
            browser, other, "nonowner-transfer-successor@farm.in"
        )
        before = await _farm_owners()
        absent_id = MAX_INT32_ID
        if absent_id in before:
            raise RuntimeError("The designated in-range absent farm already exists")
        body = {"membership_id": membership_id, "current_password": OWNER_PW}
        denied = await browser.post(
            f"/api/auth/farms/{other['X-Farm-Id']}/transfer-ownership",
            headers=caller,
            json=body,
        )
        absent = await browser.post(
            f"/api/auth/farms/{absent_id}/transfer-ownership",
            headers=caller,
            json=body,
        )
    assert denied.status_code == 404, denied.text
    assert absent.status_code == 404, absent.text
    assert denied.json() == absent.json()
    assert await _farm_owners() == before


async def _drain_registration_requests(
    requests: list[asyncio.Task[httpx.Response]],
) -> list[httpx.Response]:
    # This runs after the release Event is set, including setup-failure paths.
    # A service fault must remain nonassertion infrastructure, not a fake
    # business-response assertion kill.
    _, pending = await asyncio.wait(requests, timeout=30)
    if pending:
        for task in pending:
            task.cancel()
        _, still_pending = await asyncio.wait(pending, timeout=10)
        if still_pending:
            raise RuntimeError("Owned registration requests did not finish cancellation")
        await asyncio.gather(*requests, return_exceptions=True)
        raise TimeoutError("Owned registration requests did not finish after barrier release")
    responses = []
    for task in requests:
        try:
            responses.append(task.result())
        except BaseException as exc:
            raise RuntimeError("An owned real registration request failed") from exc
    return responses


async def test_two_real_same_email_registrations_return_one_controlled_duplicate(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = "concurrent-registration-contract@farm.in"
    both_at_insert = asyncio.Event()
    release_insert = asyncio.Event()
    arrivals = 0
    real_flush = AsyncSession.flush

    async def coordinate_actual_insert(
        session: AsyncSession, objects: Sequence[Any] | None = None
    ) -> None:
        nonlocal arrivals
        if any(isinstance(item, User) and item.email == email for item in session.new):
            # register() has already run its real password hash and actual
            # existence SELECT before adding this User and reaching flush.
            arrivals += 1
            if arrivals > 2:
                raise RuntimeError("Unexpected extra registration insert")
            if arrivals == 2:
                both_at_insert.set()
            await release_insert.wait()
        await real_flush(session, objects)

    monkeypatch.setattr(AsyncSession, "flush", coordinate_actual_insert)
    async with _raising_browser(client) as first, _raising_browser(client) as second:
        body = {"email": email, "password": OWNER_PW, "name": "One registered identity"}
        requests = [
            asyncio.create_task(first.post("/api/auth/register", json=body)),
            asyncio.create_task(second.post("/api/auth/register", json=body)),
        ]
        try:
            # Failure to establish the genuine race is fixture/infrastructure,
            # never a business rejection inferred from elapsed time.
            await asyncio.wait_for(both_at_insert.wait(), timeout=30)
        finally:
            release_insert.set()
            responses = await _drain_registration_requests(requests)

    assert sorted(response.status_code for response in responses) == [201, 400]
    winner = next(response for response in responses if response.status_code == 201)
    loser = next(response for response in responses if response.status_code == 400)
    async with get_sessionmaker()() as db:
        users = list((await db.execute(select(User).where(User.email == email))).scalars())
        assert len(users) == 1
        assert users[0].id == winner.json()["user"]["id"]
        assert users[0].deleted_at is None
    async with _raising_browser(client) as browser:
        authenticated = await browser.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {winner.json()['access_token']}"},
        )
    assert authenticated.status_code == 200, authenticated.text
    assert authenticated.json()["id"] == winner.json()["user"]["id"]
    assert "access_token" not in loser.json()
