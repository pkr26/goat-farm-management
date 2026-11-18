"""Compacted, valid predecessors log out the live row after retained history expires."""

from datetime import UTC, datetime, timedelta, tzinfo
from typing import Self

import httpx
import jwt.api_jwt as jwt_api
import pytest
from sqlalchemy import select

from app import deps, security, utils
from app.api import auth as auth_api
from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import RefreshSession, User

DAY = 24 * 60 * 60


class _RealDatetimeInstanceMeta(type):
    def __instancecheck__(cls, instance: object) -> bool:
        # PyJWT encodes genuine datetimes fetched from PostgreSQL too.
        return isinstance(instance, datetime)


async def test_compacted_cookie_logout_commits_after_short_lived_history_is_purged(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [datetime.now(UTC)]

    class ClockDateTime(datetime, metaclass=_RealDatetimeInstanceMeta):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(clock[0].timestamp(), tz)

    if (
        vars(auth_api).get("utcnow") is not utils.utcnow
        or vars(deps).get("utcnow") is not utils.utcnow
    ):
        raise RuntimeError("The application clock bindings changed")
    monkeypatch.setattr(utils, "datetime", ClockDateTime)
    monkeypatch.setattr(security, "datetime", ClockDateTime)
    monkeypatch.setattr(jwt_api, "datetime", ClockDateTime)
    settings = get_settings()

    def configure_ttl(seconds: int) -> None:
        validated = Settings.model_validate(
            settings.model_dump()
            | {"refresh_token_ttl_seconds": seconds, "refresh_max_sessions_per_family": 2}
        )
        monkeypatch.setattr(
            settings, "refresh_token_ttl_seconds", validated.refresh_token_ttl_seconds
        )
        monkeypatch.setattr(settings, "refresh_max_sessions_per_family", 2)

    def cookie(response: httpx.Response) -> str:
        value = response.cookies.get(settings.refresh_cookie_name)
        if not value:
            raise RuntimeError("A successful real issuance did not set its refresh cookie")
        return value

    def claims(token: str) -> security.RefreshClaims:
        value = security.decode_refresh_claims(token)
        if value is None:
            raise RuntimeError("A genuinely issued token did not validate on the coherent clock")
        return value

    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("The real application transport is required")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport.app, raise_app_exceptions=True),
            base_url=client.base_url,
            event_hooks=client.event_hooks,
        ) as browser:
            origin = str(browser.base_url).rstrip("/")

            def headers(token: str) -> dict[str, str]:
                return {"Cookie": f"{settings.refresh_cookie_name}={token}", "Origin": origin}

            configure_ttl(365 * DAY)
            registered = await browser.post(
                "/api/auth/register",
                json={"email": "retention-history-owner@farm.in", "password": "ownerpass123"},
            )
            if registered.status_code != 201:
                raise RuntimeError(f"Registration setup failed: {registered.status_code}")
            user_id = int(registered.json()["user"]["id"])
            predecessor = cookie(registered)
            original = claims(predecessor)
            clock[0] += timedelta(seconds=2)
            configure_ttl(DAY)
            short = await browser.post("/api/auth/refresh", headers=headers(predecessor))
            if short.status_code != 200:
                raise RuntimeError(f"Short issuance failed: {short.status_code}")
            short_token = cookie(short)
            short_claims = claims(short_token)
            clock[0] += timedelta(seconds=2)
            configure_ttl(365 * DAY)
            renewed = await browser.post("/api/auth/refresh", headers=headers(short_token))
            if renewed.status_code != 200:
                raise RuntimeError(f"Renewed issuance failed: {renewed.status_code}")
            successor = cookie(renewed)
            current = claims(successor)
            if (
                not original.family_id
                or original.family_id != short_claims.family_id
                or original.family_id != current.family_id
            ):
                raise RuntimeError("Actual rotations did not preserve their family")
            async with get_sessionmaker()() as db:
                retained = list(
                    (
                        await db.scalars(
                            select(RefreshSession.jti).where(RefreshSession.user_id == user_id)
                        )
                    ).all()
                )
                if set(retained) != {short_claims.jti, current.jti}:
                    raise RuntimeError("The original predecessor was not genuinely compacted")

            # Normal production maintenance removes only the actually expired
            # short-lived history, after its ordinary thirty-day retention.
            clock[0] = short_claims.expires_at.replace(tzinfo=UTC) + timedelta(days=31)
            claims(predecessor)
            claims(successor)
            async with get_sessionmaker()() as db:
                removed = await deps.purge_expired_refresh_sessions(
                    db, older_than_days=30, batch_size=16
                )
                await db.commit()
                retained = list(
                    (
                        await db.scalars(
                            select(RefreshSession.jti).where(RefreshSession.user_id == user_id)
                        )
                    ).all()
                )
                before = await db.scalar(select(User.token_version).where(User.id == user_id))
                if removed != 1 or retained != [current.jti] or before is None:
                    raise RuntimeError(
                        "The normal retention path did not leave exactly one live successor"
                    )
            logout = await browser.post("/api/auth/logout", headers=headers(predecessor))
            if logout.request.headers.get_list("Cookie") != [
                f"{settings.refresh_cookie_name}={predecessor}"
            ]:
                raise RuntimeError("The real logout did not present exactly the saved predecessor")
            async with get_sessionmaker()() as db:
                after = await db.scalar(select(User.token_version).where(User.id == user_id))
            blocked = await browser.post("/api/auth/refresh", headers=headers(successor))
    except BaseException as exc:
        raise RuntimeError(
            "The genuine retention-history fixture or application request failed"
        ) from exc

    assert logout.status_code == 204, logout.text
    assert blocked.status_code == 401, blocked.text
    assert after is not None and after > before
