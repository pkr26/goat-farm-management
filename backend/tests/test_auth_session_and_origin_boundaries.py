"""Browser-origin fallback and exact server-side refresh lifetime contracts."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, text, update

from app.api import auth as auth_api
from app.core.config import get_settings
from app.db import get_sessionmaker
from app.models import RefreshSession, User
from app.security import RefreshClaims, decode_refresh_claims
from app.utils import utcnow

from .conftest import register


def _refresh_claims(client: httpx.AsyncClient) -> RefreshClaims:
    token = client.cookies.get(get_settings().refresh_cookie_name)
    assert token is not None
    claims = decode_refresh_claims(token)
    assert claims is not None
    return claims


async def _assert_family_unchanged(claims: RefreshClaims) -> None:
    async with get_sessionmaker()() as db:
        session = (
            await db.execute(select(RefreshSession).where(RefreshSession.jti == claims.jti))
        ).scalar_one()
        assert session.consumed_at is None
        assert session.revoked_at is None
        assert session.replacement_jti is None
        count = await db.scalar(
            select(func.count())
            .select_from(RefreshSession)
            .where(RefreshSession.family_id == claims.family_id)
        )
        assert count == 1


@pytest.mark.parametrize(
    "referer",
    ["https://untrusted.invalid/settings", "http://test.attacker.invalid/?next=http://test"],
)
async def test_referer_only_refresh_rejection_preserves_the_cookie_family(
    client: httpx.AsyncClient, referer: str
) -> None:
    await register(client, "referer-rejection@farm.in")
    cookie = client.cookies.get(get_settings().refresh_cookie_name)
    claims = _refresh_claims(client)

    rejected = await client.post("/api/auth/refresh", headers={"Referer": referer})

    assert rejected.status_code == 403, rejected.text
    assert rejected.json()["detail"] == "Untrusted origin for cookie-authenticated request."
    assert "set-cookie" not in rejected.headers
    assert client.cookies.get(get_settings().refresh_cookie_name) == cookie
    await _assert_family_unchanged(claims)

    # The same credential remains available to its legitimate browser tab.
    accepted = await client.post(
        "/api/auth/refresh", headers={"Referer": "http://test/settings?tab=security"}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["access_token"]
    assert client.cookies.get(get_settings().refresh_cookie_name) != cookie


async def test_referer_only_refresh_accepts_an_explicitly_allowed_browser_origin(
    client: httpx.AsyncClient,
) -> None:
    await register(client, "allowed-referer@farm.in")
    browser = get_settings().cors_origins[0].rstrip("/")
    response = await client.post(
        "/api/auth/refresh", headers={"Referer": browser + "/account/security"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


async def test_expired_signed_refresh_does_not_wait_for_the_user_write_lock(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await register(client, "expired-before-lock@farm.in")
    claims = _refresh_claims(client)
    monkeypatch.setattr(auth_api, "utcnow", lambda: claims.expires_at)
    blocked = False
    response_task: asyncio.Task[httpx.Response] | None = None
    async with get_sessionmaker()() as blocker, get_sessionmaker()() as observer:
        await blocker.execute(select(User).where(User.id == claims.user_id).with_for_update())
        blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        assert blocker_pid is not None
        try:
            response_task = asyncio.create_task(client.post("/api/auth/refresh"))
            # Drive release from real completion or a PostgreSQL dependency,
            # rather than using elapsed time to declare a mutation killed.
            async with asyncio.timeout(15):
                while not response_task.done():
                    blocked = bool(
                        await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE datname = current_database() "
                                "AND :holder = ANY(pg_blocking_pids(pid)))"
                            ).bindparams(holder=blocker_pid)
                        )
                    )
                    # Refresh the activity snapshot so a newly opened request
                    # connection becomes visible on the next read-only probe.
                    await observer.rollback()
                    if blocked:
                        break
                    await asyncio.sleep(0)
        finally:
            await blocker.rollback()
            if response_task is not None and not response_task.done():
                await asyncio.wait_for(response_task, 15)
    assert response_task is not None
    response = response_task.result()
    assert not blocked, "an already expired signed credential waited for the principal lock"
    assert response.status_code == 401, response.text
    await _assert_family_unchanged(claims)


async def test_signed_expiry_reached_during_refresh_rejects_a_later_persisted_expiry(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await register(client, "signed-expiry-in-flight@farm.in")
    claims = _refresh_claims(client)
    async with get_sessionmaker()() as db:
        await db.execute(
            update(RefreshSession)
            .where(RefreshSession.jti == claims.jti)
            .values(expires_at=claims.expires_at + timedelta(hours=1))
        )
        await db.commit()
    times = iter([claims.expires_at - timedelta(seconds=1), claims.expires_at])
    monkeypatch.setattr(auth_api, "utcnow", lambda: next(times, claims.expires_at))

    response = await client.post("/api/auth/refresh")

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Invalid or expired refresh token"
    await _assert_family_unchanged(claims)


async def test_persisted_refresh_expiry_is_exclusive_even_while_signature_is_live(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await register(client, "persisted-expiry-equality@farm.in")
    claims = _refresh_claims(client)
    boundary = utcnow() + timedelta(seconds=10)
    assert claims.expires_at > boundary
    async with get_sessionmaker()() as db:
        await db.execute(
            update(RefreshSession)
            .where(RefreshSession.jti == claims.jti)
            .values(expires_at=boundary)
        )
        await db.commit()
    monkeypatch.setattr(auth_api, "utcnow", lambda: boundary)

    response = await client.post("/api/auth/refresh")

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Invalid or expired refresh token"
    await _assert_family_unchanged(claims)
