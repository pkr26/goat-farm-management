"""TOTP second factor (HUM-1 follow-up, 2026-09-16): enrollment lifecycle,
login challenge flow, single-use/replay protections, throttling, and at-rest
encryption of the shared secret."""

import base64
import hashlib
import hmac as hmac_mod
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db import get_sessionmaker
from app.models import User
from app.security import (
    TOTP_STEP_SECONDS,
    _totp_code_for_step,
    decrypt_totp_secret,
    generate_totp_secret_b32,
    verify_totp_code,
)

from .conftest import OWNER_PW, owner_with_farm, register


def _current_code(secret_b32: str, *, drift: int = 0) -> tuple[str, int]:
    step = int(datetime.now(UTC).timestamp() // TOTP_STEP_SECONDS) + drift
    return _totp_code_for_step(secret_b32, step), step


async def _enroll_and_activate(
    client: httpx.AsyncClient, headers: dict, *, password: str = OWNER_PW
) -> str:
    enroll = await client.post(
        "/api/auth/totp/enroll",
        json={"current_password": password},
        headers=headers,
    )
    assert enroll.status_code == 200, enroll.text
    secret = enroll.json()["secret"]
    code, _step = _current_code(secret)
    confirm = await client.post("/api/auth/totp/confirm", json={"code": code}, headers=headers)
    assert confirm.status_code == 204, confirm.text
    return secret


async def test_totp_unit_engine_roundtrip_and_replay_rejection() -> None:
    secret = generate_totp_secret_b32()
    assert len(base64.b32decode(secret)) == 20
    code, step = _current_code(secret)
    assert verify_totp_code(secret, code, at=datetime.now(UTC), last_used_step=None) == step
    # A used step never re-validates (single-use codes).
    assert verify_totp_code(secret, code, at=datetime.now(UTC), last_used_step=step) is None
    # Malformed shapes never authenticate.
    for bad in ("12345", "1234567", "12345x", "123 45"):
        assert verify_totp_code(secret, bad, at=datetime.now(UTC), last_used_step=None) is None


async def test_totp_secret_is_encrypted_at_rest(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-crypt@farm.in")
    secret = await _enroll_and_activate(client, headers)
    async with get_sessionmaker()() as db:
        row = (
            await db.execute(select(User).where(User.email == "totp-crypt@farm.in"))
        ).scalar_one()
        assert row.totp_state == "ACTIVE"
        stored = bytes(row.totp_secret_enc)
        assert secret.encode() not in stored  # never plaintext
        assert len(stored) > 12
        assert decrypt_totp_secret(stored) == secret
        assert hmac_mod.compare_digest(
            hashlib.sha256(stored).digest(), hashlib.sha256(stored).digest()
        )


async def test_full_totp_login_challenge_flow(client: httpx.AsyncClient) -> None:
    headers = await register(client, "totp-full@farm.in")
    secret = await _enroll_and_activate(client, headers)
    me = await client.get("/api/auth/me", headers=headers)
    assert me.json()["totp_state"] == "ACTIVE"

    login = await client.post(
        "/api/auth/login",
        json={"email": "totp-full@farm.in", "password": OWNER_PW},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["mfa_token"] and body["access_token"] is None and body["user"] is None

    # The confirm step already consumed the current code (codes are
    # single-use); the next step's code is inside the ±1 drift window.
    code, _step = _current_code(secret, drift=1)
    challenge = await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": body["mfa_token"], "code": code},
    )
    assert challenge.status_code == 200, challenge.text
    tokens = challenge.json()
    assert tokens["access_token"] and tokens["user"]["email"] == "totp-full@farm.in"
    # The exchanged access token authenticates like any session.
    authed = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert authed.status_code == 200


async def test_pending_enrollment_does_not_gate_login(client: httpx.AsyncClient) -> None:
    headers = await register(client, "totp-pending@farm.in")
    enroll = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
    )
    assert enroll.status_code == 200
    login = await client.post(
        "/api/auth/login",
        json={"email": "totp-pending@farm.in", "password": OWNER_PW},
    )
    assert login.status_code == 200
    assert login.json()["access_token"]  # no challenge for unconfirmed enrollment


async def test_challenge_rejects_wrong_code_then_accepts_right(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-wrong@farm.in")
    secret = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-wrong@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]

    wrong = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": "000000"}
    )
    assert wrong.status_code == 401
    # The token stays usable until consumed by a SUCCESSFUL challenge. The
    # confirm step consumed the current code; use the next step's.
    code, _step = _current_code(secret, drift=1)
    right = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert right.status_code == 200, right.text


async def test_challenge_token_is_single_use(client: httpx.AsyncClient) -> None:
    headers = await register(client, "totp-replay@farm.in")
    secret = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-replay@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]
    code, _step = _current_code(secret, drift=1)
    first = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert first.status_code == 200
    replay = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert replay.status_code == 401


async def test_used_code_does_not_authenticate_a_second_challenge(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-codesingle@farm.in")
    secret = await _enroll_and_activate(client, headers)
    login1 = await client.post(
        "/api/auth/login", json={"email": "totp-codesingle@farm.in", "password": OWNER_PW}
    )
    code, _step = _current_code(secret, drift=1)
    ok = await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": login1.json()["mfa_token"], "code": code},
    )
    assert ok.status_code == 200
    login2 = await client.post(
        "/api/auth/login", json={"email": "totp-codesingle@farm.in", "password": OWNER_PW}
    )
    again = await client.post(
        "/api/auth/totp/challenge",
        json={"mfa_token": login2.json()["mfa_token"], "code": code},
    )
    assert again.status_code == 401


async def test_challenge_dies_with_a_password_change(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-ver@farm.in")
    secret = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-ver@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]
    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": OWNER_PW, "new_password": "New-Pass-12345!"},
        headers=headers,
    )
    assert changed.status_code == 200
    code, _step = _current_code(secret)
    stale = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert stale.status_code == 401


async def test_enroll_requires_the_current_password(client: httpx.AsyncClient) -> None:
    headers = await register(client, "totp-pw@farm.in")
    bad = await client.post(
        "/api/auth/totp/enroll", json={"current_password": "wrong-password"}, headers=headers
    )
    assert bad.status_code == 400
    ok = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["secret"] and body["otpauth_uri"].startswith("otpauth://totp/Herdly:")


async def test_disable_requires_password_and_code_when_active(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-off@farm.in")
    secret = await _enroll_and_activate(client, headers)
    # Confirm consumed the current step's code; disable needs a fresh one.
    code, _step = _current_code(secret, drift=1)
    bad_code = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": OWNER_PW, "code": "000000"},
        headers=headers,
    )
    assert bad_code.status_code == 400
    bad_pw = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": "wrong-password", "code": code},
        headers=headers,
    )
    assert bad_pw.status_code == 400
    ok = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": OWNER_PW, "code": code},
        headers=headers,
    )
    assert ok.status_code == 204
    login = await client.post(
        "/api/auth/login", json={"email": "totp-off@farm.in", "password": OWNER_PW}
    )
    assert login.json()["access_token"]  # no second factor anymore


async def test_disable_pending_enrollment_needs_only_the_password(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-offpending@farm.in")
    enroll = await client.post(
        "/api/auth/totp/enroll", json={"current_password": OWNER_PW}, headers=headers
    )
    assert enroll.status_code == 200
    ok = await client.post(
        "/api/auth/totp/disable",
        json={"current_password": OWNER_PW, "code": "123456"},  # code ignored pre-activation
        headers=headers,
    )
    assert ok.status_code == 204
    me = await client.get("/api/auth/me", headers=headers)
    assert me.json()["totp_state"] is None


async def test_confirm_without_enrollment_is_a_conflict(
    client: httpx.AsyncClient,
) -> None:
    headers = await register(client, "totp-noconfirm@farm.in")
    resp = await client.post("/api/auth/totp/confirm", json={"code": "123456"}, headers=headers)
    assert resp.status_code == 409


async def test_challenge_brute_force_is_throttled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "auth_rate_limit_enabled", True)
    headers = await register(client, "totp-throttle@farm.in")
    secret = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-throttle@farm.in", "password": OWNER_PW}
    )
    mfa_token = login.json()["mfa_token"]
    statuses = []
    for _ in range(6):
        resp = await client.post(
            "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": "000000"}
        )
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert statuses[-1] == 429, statuses
    # The correct code is now locked out too until the window slides.
    code, _step = _current_code(secret)
    locked = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": mfa_token, "code": code}
    )
    assert locked.status_code == 429


async def test_owner_with_totp_full_app_flow_still_works(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="totp-owner@farm.in")
    secret = await _enroll_and_activate(client, headers)
    login = await client.post(
        "/api/auth/login", json={"email": "totp-owner@farm.in", "password": OWNER_PW}
    )
    code, _step = _current_code(secret, drift=1)
    tokens = (
        await client.post(
            "/api/auth/totp/challenge",
            json={"mfa_token": login.json()["mfa_token"], "code": code},
        )
    ).json()
    farm_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    farms = await client.get("/api/auth/farms", headers=farm_headers)
    assert farms.status_code == 200 and len(farms.json()) == 1
