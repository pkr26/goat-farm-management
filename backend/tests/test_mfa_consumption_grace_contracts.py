"""A consumed MFA proof stays spent throughout its accepted clock-skew window."""

from datetime import UTC, datetime, timedelta, tzinfo
from typing import Self
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app import security
from app.api import auth as auth_api
from app.db import get_sessionmaker
from app.models import ConsumedMfaChallenge, User
from app.security import TOTP_STEP_SECONDS, _totp_code_for_step

from .conftest import OWNER_PW, register
from .test_totp import _enroll_and_activate
from .test_totp import stable_totp_key as stable_totp_key

pytestmark = pytest.mark.usefixtures("stable_totp_key")


@pytest.mark.parametrize(
    ("already_consumed", "grace_offset", "expected_status"),
    [(True, 1, 401), (False, 1, 200), (False, 59, 200), (False, 60, 401)],
    ids=["spent-proof", "unused-proof", "unused-near-grace-limit", "expired-at-grace-limit"],
)
async def test_mfa_single_use_covers_the_full_accepted_clock_grace(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    already_consumed: bool,
    grace_offset: int,
    expected_status: int,
) -> None:
    email = "mfa-grace-contract@farm.in"
    headers = await register(client, email)
    secret, _codes = await _enroll_and_activate(client, headers)
    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    user_id = int(me.json()["id"])
    client.cookies.clear()
    login = await client.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
    assert login.status_code == 200
    assert login.json().get("mfa_token") and login.json().get("access_token") is None
    token = login.json()["mfa_token"]
    claims, expired = security._decode_payload_result(token, "mfa")
    assert claims is not None and not expired
    nominal_expiry = int(claims["exp"])
    jti = str(claims["jti"])
    # The two exchanges use different fresh authenticator steps. Rejection
    # must come from the spent password proof, rather than TOTP-code replay.
    clock = [datetime.fromtimestamp(nominal_expiry - TOTP_STEP_SECONDS - 1, UTC)]

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(clock[0].timestamp(), tz)

    monkeypatch.setattr(security, "datetime", ClockDateTime)
    monkeypatch.setattr(auth_api, "utcnow", lambda: clock[0].replace(tzinfo=None))

    async def exchange() -> httpx.Response:
        step = int(clock[0].timestamp() // TOTP_STEP_SECONDS)
        return await client.post(
            "/api/auth/totp/challenge",
            json={"mfa_token": token, "code": _totp_code_for_step(secret, step)},
        )

    if already_consumed:
        first = await exchange()
        assert first.status_code == 200, first.text
        client.cookies.clear()
    async with get_sessionmaker()() as observer:
        previous_step = await observer.scalar(select(User.totp_last_step).where(User.id == user_id))

    clock[0] = datetime.fromtimestamp(nominal_expiry + grace_offset, UTC)
    accepted_claims, expired = security._decode_payload_result(token, "mfa")
    assert accepted_claims is not None, "The control must exercise an authentic signed token"
    if already_consumed:
        assert not expired, "The spent-proof control must remain within accepted clock grace"
    result = await exchange()
    if expected_status == 401:
        assert result.status_code == 401, (
            "A spent or expired MFA challenge must not mint another session: "
            f"status={result.status_code}"
        )
        assert "access_token" not in result.json()
    else:
        assert result.status_code == 200, result.text
        assert result.json()["access_token"]
    async with get_sessionmaker()() as observer:
        consumed = await observer.get(ConsumedMfaChallenge, jti)
        if already_consumed or expected_status == 200:
            assert consumed is not None
        else:
            assert consumed is None, "An expired proof must not reach the challenge claim"
        if expected_status == 401:
            assert (
                await observer.scalar(select(User.totp_last_step).where(User.id == user_id))
                == previous_step
            ), "Refusing a replay must not consume the fresh authenticator step"

    if already_consumed:
        # Once the same decoder actually refuses the old proof, the ordinary
        # next claim should reap its durable row. The earlier row came from
        # the genuine successful exchange above.
        clock[0] += timedelta(minutes=2)
        _claims, expired = security._decode_payload_result(token, "mfa")
        assert expired
        successor_jti = uuid4().hex
        async with get_sessionmaker()() as db:
            assert await auth_api._consume_mfa_jti(
                db,
                successor_jti,
                clock[0].replace(tzinfo=None) + timedelta(minutes=5),
            )
            await db.commit()
        async with get_sessionmaker()() as observer:
            assert await observer.get(ConsumedMfaChallenge, jti) is None
            assert await observer.get(ConsumedMfaChallenge, successor_jti) is not None
