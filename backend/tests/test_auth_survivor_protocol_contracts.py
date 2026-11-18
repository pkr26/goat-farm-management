"""Public browser, affiliation, session, and second-factor protocol boundaries."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, tzinfo
from http.cookies import SimpleCookie
from typing import Self

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import security
from app.api import auth as auth_api
from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.deps import active_membership
from app.models import Farm, FarmMembership, RefreshSession, TotpRecoveryCode, User
from app.ratelimit import auth_limiter
from app.schemas.common import MAX_INT32_ID
from app.security import (
    RefreshClaims,
    decode_refresh_claims,
    verify_password_async,
    verify_totp_code,
)
from app.utils import utcnow

from .conftest import OWNER_PW, create_farm, login_and_rotate, owner_with_farm, register
from .test_auth_extended import set_refresh_cookie
from .test_session_privacy import _password_worker
from .test_totp import _current_code, _enroll_and_activate, stable_totp_key  # noqa: F401
from .test_worker_pin_auth import _make_pin_worker, _worker_login

pytestmark = pytest.mark.usefixtures("stable_totp_key")


@pytest.fixture(autouse=True)
def _clear_auth_admission() -> Iterator[None]:
    auth_limiter.clear()
    yield
    auth_limiter.clear()


def _configure(monkeypatch: pytest.MonkeyPatch, **values: object) -> None:
    configured = Settings.model_validate(get_settings().model_dump() | values)
    for name in values:
        monkeypatch.setattr(get_settings(), name, getattr(configured, name))


def _cookie(client: httpx.AsyncClient) -> str:
    value = client.cookies.get(get_settings().refresh_cookie_name)
    assert value is not None
    return value


def _claims(cookie: str) -> RefreshClaims:
    claims = decode_refresh_claims(cookie)
    assert claims is not None
    return claims


def _http(client: httpx.AsyncClient, *, ip: str = "127.0.0.1") -> httpx.AsyncClient:
    transport = client._transport
    assert isinstance(transport, httpx.ASGITransport)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=transport.app, client=(ip, 42001), raise_app_exceptions=False
        ),
        base_url="http://test",
    )


async def _family_revoked(cookie: str) -> bool:
    claims = _claims(cookie)
    async with get_sessionmaker()() as db:
        live = await db.scalar(
            select(func.count())
            .select_from(RefreshSession)
            .where(
                RefreshSession.family_id == claims.family_id,
                RefreshSession.revoked_at.is_(None),
            )
        )
        return live == 0


async def test_refresh_accepts_unrelated_browser_cookies(client: httpx.AsyncClient) -> None:
    await register(client, "browser-cookie-contract@farm.in")
    token = _cookie(client)
    async with _http(client) as browser:
        response = await browser.post(
            "/api/auth/refresh",
            headers={"Cookie": f"theme=dark; {get_settings().refresh_cookie_name}={token}; tab=1"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


async def test_roster_exact_page_has_no_cursor_and_overflow_has_one(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, "exact-roster-owner@farm.in")
    ids = []
    for index in range(2):
        membership, farm = await _make_pin_worker(
            client, owner, email=f"exact-roster-{index}@farm.in"
        )
        ids.append(membership)
    page = await client.get("/api/auth/worker-roster", params={"farm_id": farm, "limit": 2})
    assert page.status_code == 200, page.text
    assert [item["membership_id"] for item in page.json()["items"]] == ids
    assert page.json()["next_after_membership_id"] is None
    third, _ = await _make_pin_worker(client, owner, email="exact-roster-2@farm.in")
    page = await client.get("/api/auth/worker-roster", params={"farm_id": farm, "limit": 2})
    assert page.status_code == 200, page.text
    assert page.json()["next_after_membership_id"] == ids[-1]
    final = await client.get(
        "/api/auth/worker-roster",
        params={"farm_id": farm, "limit": 2, "after_membership_id": ids[-1]},
    )
    assert final.status_code == 200, final.text
    assert [item["membership_id"] for item in final.json()["items"]] == [third]
    assert final.json()["next_after_membership_id"] is None


async def test_exact_owned_export_cap_is_inclusive_and_owned_farms_prevent_deletion(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, "two-owned-boundary@farm.in")
    second = await create_farm(client, owner, "Second owned farm")
    _configure(monkeypatch, max_account_affiliations_per_response=2)
    async with _http(client) as browser:
        exported = await browser.get("/api/auth/account/export", headers=owner)
        denied = await browser.request(
            "DELETE",
            "/api/auth/account",
            headers=owner,
            json={"current_password": OWNER_PW},
        )
    assert exported.status_code == 200, exported.text
    assert {row["id"] for row in exported.json()["owned_farms"]} == {
        int(owner["X-Farm-Id"]),
        int(second["X-Farm-Id"]),
    }
    assert exported.json()["memberships"] == []
    assert denied.status_code == 409, denied.text
    assert (await client.get("/api/auth/me", headers=owner)).status_code == 200


@pytest.mark.parametrize("owned_count", [1, 2], ids=["exact-combined-cap", "combined-overflow"])
async def test_export_counts_owned_farms_and_memberships_together_without_truncation(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, owned_count: int
) -> None:
    owner = await owner_with_farm(client, "mixed-export-owner@farm.in")
    await _password_worker(client, owner, "mixed-export-worker@farm.in")
    worker = await login_and_rotate(client, "mixed-export-worker@farm.in", "workerpass123")
    owned_ids = []
    for index in range(owned_count):
        farm = await create_farm(client, worker, f"Worker owned {index}")
        owned_ids.append(int(farm["X-Farm-Id"]))
    _configure(monkeypatch, max_account_affiliations_per_response=2)
    async with _http(client) as browser:
        response = await browser.get("/api/auth/account/export", headers=worker)
    if owned_count == 2:
        assert response.status_code == 409, response.text
        assert (
            response.json()["detail"] == "Account has too many farm affiliations to return safely."
        )
    else:
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["owned_farms"]] == owned_ids
        assert [row["farm_id"] for row in response.json()["memberships"]] == [
            int(owner["X-Farm-Id"])
        ]


async def test_transfer_respects_the_successors_actual_ownership_cap(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, "transfer-cap-owner@farm.in")
    worker = await _password_worker(client, owner, "transfer-cap-worker@farm.in")
    successor = await login_and_rotate(client, "transfer-cap-worker@farm.in", "workerpass123")
    await create_farm(client, successor, "Already owned")
    await create_farm(client, owner, "The departing owner retains this farm")
    endpoint = f"/api/auth/farms/{owner['X-Farm-Id']}/transfer-ownership"
    payload = {"membership_id": worker["id"], "current_password": OWNER_PW}
    _configure(monkeypatch, max_farms_per_user=1)
    denied = await client.post(endpoint, headers=owner, json=payload)
    assert denied.status_code == 409, denied.text
    assert (await client.get("/api/team", headers=owner)).status_code == 200
    assert (await client.get("/api/auth/me", headers=successor)).status_code == 200
    _configure(monkeypatch, max_farms_per_user=2)
    accepted = await client.post(endpoint, headers=owner, json=payload)
    assert accepted.status_code == 200, accepted.text
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None and farm.owner_id == worker["user_id"]


async def test_transfer_rejects_an_absent_successor_without_changing_ownership(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, "absent-successor@farm.in")
    async with _http(client) as browser:
        denied = await browser.post(
            f"/api/auth/farms/{owner['X-Farm-Id']}/transfer-ownership",
            headers=owner,
            json={"membership_id": 123456, "current_password": OWNER_PW},
        )
    assert denied.status_code == 409, denied.text
    assert (await client.get("/api/team", headers=owner)).status_code == 200


@pytest.mark.parametrize("direction", [-1, 1], ids=["backward-clock", "forward-clock"])
async def test_refresh_replay_includes_each_exact_grace_boundary(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, direction: int
) -> None:
    await register(client, "exact-reuse-grace@farm.in")
    predecessor = _cookie(client)
    rotated = await client.post("/api/auth/refresh")
    assert rotated.status_code == 200, rotated.text
    successor = _cookie(client)
    async with get_sessionmaker()() as db:
        consumed_at = await db.scalar(
            select(RefreshSession.consumed_at).where(RefreshSession.jti == _claims(predecessor).jti)
        )
    assert consumed_at is not None
    boundary = consumed_at + timedelta(
        seconds=direction * get_settings().refresh_reuse_grace_seconds
    )
    monkeypatch.setattr(auth_api, "utcnow", lambda: boundary)
    set_refresh_cookie(client, predecessor)
    replay = await client.post("/api/auth/refresh")
    assert replay.status_code == 200, replay.text
    assert _cookie(client) == successor
    assert not await _family_revoked(successor)


async def test_cookie_logout_does_not_expand_to_a_companion_password_family(
    client: httpx.AsyncClient,
) -> None:
    first = await register(client, "companion-families@farm.in")
    first_cookie = _cookie(client)
    second = await client.post(
        "/api/auth/login", json={"email": "companion-families@farm.in", "password": OWNER_PW}
    )
    second_cookie = _cookie(client)
    set_refresh_cookie(client, first_cookie)
    logged_out = await client.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {second.json()['access_token']}"}
    )
    assert logged_out.status_code == 204, logged_out.text
    assert await _family_revoked(first_cookie)
    assert not await _family_revoked(second_cookie)
    assert (await client.get("/api/auth/me", headers=first)).status_code == 401
    companion = {"Authorization": f"Bearer {second.json()['access_token']}"}
    assert (await client.get("/api/auth/me", headers=companion)).status_code == 401
    set_refresh_cookie(client, second_cookie)
    assert (await client.post("/api/auth/refresh")).status_code == 200


async def test_bearer_only_logout_after_rotation_revokes_every_family(
    client: httpx.AsyncClient,
) -> None:
    await register(client, "rotated-bearer-logout@farm.in")
    rotated = await client.post("/api/auth/refresh")
    cookie = _cookie(client)
    async with _http(client) as browser:
        response = await browser.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {rotated.json()['access_token']}"},
        )
    assert response.status_code == 204, response.text
    assert await _family_revoked(cookie)


async def test_pin_logout_from_a_compacted_predecessor_commits_family_revocation(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, "compacted-pin-owner@farm.in")
    membership, farm = await _make_pin_worker(client, owner, email="compacted-pin-worker@farm.in")
    login = await _worker_login(client, farm, membership, "4321")
    assert login.status_code == 200, login.text
    predecessor = _cookie(client)
    _configure(monkeypatch, refresh_max_sessions_per_family=2)
    for _ in range(2):
        refreshed = await client.post("/api/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text
    successor = _cookie(client)
    async with get_sessionmaker()() as db:
        assert (
            await db.scalar(
                select(RefreshSession.id).where(RefreshSession.jti == _claims(predecessor).jti)
            )
            is None
        )
    set_refresh_cookie(client, predecessor)
    logged_out = await client.post("/api/auth/logout")
    assert logged_out.status_code == 204, logged_out.text
    assert await _family_revoked(successor)
    set_refresh_cookie(client, successor)
    assert (await client.post("/api/auth/refresh")).status_code == 401
    assert (await client.get("/api/team", headers=owner)).status_code == 200


def _invalid_code(secret: str) -> str:
    for number in range(100):
        code = f"{number:06d}"
        if verify_totp_code(secret, code, at=utcnow(), last_used_step=None) is None:
            return code
    raise AssertionError("Unable to choose an invalid numeric authenticator code")


async def _factor(email: str) -> tuple[str | None, bytes | None, int]:
    async with get_sessionmaker()() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        count = await db.scalar(
            select(func.count())
            .select_from(TotpRecoveryCode)
            .where(TotpRecoveryCode.user_id == user.id)
        )
        return user.totp_state, user.totp_secret_enc, int(count or 0)


async def test_invalid_confirmation_is_a_400_and_preserves_pending_enrollment(
    client: httpx.AsyncClient,
) -> None:
    email = "invalid-confirm-contract@farm.in"
    headers = await register(client, email)
    enrolled = await client.post(
        "/api/auth/totp/enroll", headers=headers, json={"current_password": OWNER_PW}
    )
    assert enrolled.status_code == 200, enrolled.text
    before = await _factor(email)
    denied = await client.post(
        "/api/auth/totp/confirm",
        headers=headers,
        json={"code": _invalid_code(enrolled.json()["secret"])},
    )
    assert denied.status_code == 400, denied.text
    assert await _factor(email) == before
    assert before[0] == "PENDING" and before[2] == 0


async def test_active_factor_cannot_be_confirmed_again_with_a_fresh_valid_code(
    client: httpx.AsyncClient,
) -> None:
    email = "active-confirm-contract@farm.in"
    headers = await register(client, email)
    secret, _ = await _enroll_and_activate(client, headers)
    before = await _factor(email)
    code, _ = _current_code(secret, drift=1)
    denied = await client.post("/api/auth/totp/confirm", headers=headers, json={"code": code})
    assert denied.status_code == 409, denied.text
    assert denied.json().get("code") == "LIFECYCLE_CONFLICT"
    assert await _factor(email) == before


async def test_pending_factor_cannot_regenerate_recovery_codes(
    client: httpx.AsyncClient,
) -> None:
    email = "pending-regen-contract@farm.in"
    headers = await register(client, email)
    enrolled = await client.post(
        "/api/auth/totp/enroll", headers=headers, json={"current_password": OWNER_PW}
    )
    assert enrolled.status_code == 200, enrolled.text
    before = await _factor(email)
    code, _ = _current_code(enrolled.json()["secret"])
    denied = await client.post(
        "/api/auth/totp/recovery/regenerate",
        headers=headers,
        json={"current_password": OWNER_PW, "code": code},
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["detail"] == "Two-factor is not active."
    assert await _factor(email) == before


async def test_confirmation_revalidates_a_reenrolled_secret_after_real_hash_work(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = "reenroll-confirm-contract@farm.in"
    headers = await register(client, email)
    first = await client.post(
        "/api/auth/totp/enroll", headers=headers, json={"current_password": OWNER_PW}
    )
    assert first.status_code == 200, first.text
    prepared = asyncio.Event()
    release = asyncio.Event()
    real_prepare = auth_api._prepare_totp_recovery_codes

    async def prepare() -> tuple[list[str], list[str]]:
        result = await real_prepare()
        prepared.set()
        await release.wait()
        return result

    monkeypatch.setattr(auth_api, "_prepare_totp_recovery_codes", prepare)
    code, _ = _current_code(first.json()["secret"])
    confirmation = asyncio.create_task(
        client.post("/api/auth/totp/confirm", headers=headers, json={"code": code})
    )
    try:
        await asyncio.wait_for(prepared.wait(), 20)
        replacement = await client.post(
            "/api/auth/totp/enroll", headers=headers, json={"current_password": OWNER_PW}
        )
        assert replacement.status_code == 200, replacement.text
        assert replacement.json()["secret"] != first.json()["secret"]
        before = await _factor(email)
    finally:
        release.set()
        response = await asyncio.wait_for(confirmation, 20)
    assert response.status_code == 409, response.text
    assert await _factor(email) == before


@pytest.mark.parametrize("operation", ["confirm", "disable", "recovery-codes", "challenge"])
async def test_second_factor_guess_budget_follows_the_account_to_a_new_ip(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    headers = await register(client, "roaming-factor-contract@farm.in")
    if operation == "confirm":
        enrolled = await client.post(
            "/api/auth/totp/enroll", headers=headers, json={"current_password": OWNER_PW}
        )
        assert enrolled.status_code == 200, enrolled.text
        secret = enrolled.json()["secret"]
    else:
        secret, _ = await _enroll_and_activate(client, headers)
    mfa = ""
    if operation == "challenge":
        login = await client.post(
            "/api/auth/login",
            json={"email": "roaming-factor-contract@farm.in", "password": OWNER_PW},
        )
        mfa = login.json()["mfa_token"]
    _configure(monkeypatch, auth_rate_limit_enabled=True)
    payload = {"code": _invalid_code(secret)}
    if operation in {"disable", "recovery-codes"}:
        payload["current_password"] = OWNER_PW
    if operation == "challenge":
        payload["mfa_token"] = mfa
    path = (
        "/api/auth/totp/recovery/regenerate"
        if operation == "recovery-codes"
        else f"/api/auth/totp/{operation}"
    )
    # Password confirmation and TOTP share the regeneration composite scope.
    # Rotate each source address so that only the account-wide guess budget
    # can refuse the final request, including on that double-charged route.
    for attempt in range(5):
        async with _http(client, ip=f"192.0.2.{10 + attempt}") as browser:
            denied = await browser.post(path, headers=headers, json=payload)
        assert denied.status_code == (401 if operation == "challenge" else 400), denied.text
    async with _http(client, ip="192.0.2.20") as browser:
        blocked = await browser.post(path, headers=headers, json=payload)
    assert blocked.status_code == 429, blocked.text
    assert int(blocked.headers["Retry-After"]) > 0


@pytest.mark.parametrize("change", ["password", "delete", "reenroll"])
async def test_old_mfa_challenge_cannot_follow_a_changed_account_or_factor(
    client: httpx.AsyncClient, change: str
) -> None:
    email = "changed-challenge-contract@farm.in"
    headers = await register(client, email)
    secret, _ = await _enroll_and_activate(client, headers)
    login = await client.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
    assert login.status_code == 200, login.text
    challenge = login.json()["mfa_token"]
    code, _ = _current_code(secret, drift=1)
    if change == "password":
        changed = await client.post(
            "/api/auth/change-password",
            headers=headers,
            json={"current_password": OWNER_PW, "new_password": "changed-password123"},
        )
        assert changed.status_code == 200, changed.text
    elif change == "delete":
        changed = await client.request(
            "DELETE", "/api/auth/account", headers=headers, json={"current_password": OWNER_PW}
        )
        assert changed.status_code == 204, changed.text
    else:
        disabled = await client.post(
            "/api/auth/totp/disable",
            headers=headers,
            json={"current_password": OWNER_PW, "code": code},
        )
        assert disabled.status_code == 204, disabled.text
        enrolled = await client.post(
            "/api/auth/totp/enroll", headers=headers, json={"current_password": OWNER_PW}
        )
        assert enrolled.status_code == 200, enrolled.text
        code, _ = _current_code(enrolled.json()["secret"])
    async with _http(client) as browser:
        denied = await browser.post(
            "/api/auth/totp/challenge", json={"mfa_token": challenge, "code": code}
        )
    assert denied.status_code == 401, denied.text
    assert "set-cookie" not in denied.headers
    if change == "reenroll":
        state, _, count = await _factor(email)
        assert state == "PENDING" and count == 0


async def test_numeric_guesses_do_not_start_recovery_hashes_and_first_recovery_match_stops_work(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = "recovery-work-contract@farm.in"
    headers = await register(client, email)
    secret, codes = await _enroll_and_activate(client, headers)
    login = await client.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
    assert login.status_code == 200, login.text
    challenge = login.json()["mfa_token"]
    real_verify = verify_password_async
    verified: list[str] = []

    async def verify(password: str, encoded: str) -> tuple[bool, bool]:
        verified.append(encoded)
        return await real_verify(password, encoded)

    monkeypatch.setattr(auth_api, "verify_password_async", verify)
    denied = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": challenge, "code": _invalid_code(secret)}
    )
    assert denied.status_code == 401, denied.text
    assert verified == [], "a numeric TOTP guess entered the Argon2 recovery-code workflow"
    accepted = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": challenge, "code": codes[0]}
    )
    assert accepted.status_code == 200, accepted.text
    assert len(verified) == 1, "recovery verification continued after the first matching hash"
    async with get_sessionmaker()() as db:
        used = await db.scalar(
            select(func.count())
            .select_from(TotpRecoveryCode)
            .where(TotpRecoveryCode.used_at.is_not(None))
        )
    assert used == 1


async def test_cancelled_pin_verification_is_charged_after_real_password_work(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, "cancel-pin-owner@farm.in")
    membership, farm = await _make_pin_worker(client, owner, email="cancel-pin-worker@farm.in")
    _configure(monkeypatch, auth_rate_limit_enabled=True, worker_pin_rate_limit_max_attempts=1)
    completed = asyncio.Event()
    release = asyncio.Event()
    idle = asyncio.Event()
    real_verify = verify_password_async

    async def verify(password: str, encoded: str) -> tuple[bool, bool]:
        result = await real_verify(password, encoded)
        completed.set()
        try:
            await release.wait()
        finally:
            idle.set()
        return result

    monkeypatch.setattr(auth_api, "verify_password_async", verify)
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("This PIN scheduling test requires its ASGI transport")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport.app), base_url="http://test"
    ) as browser:
        request = asyncio.create_task(_worker_login(browser, farm, membership, "9999"))
        verified = asyncio.create_task(completed.wait())
        premature: httpx.Response | None = None
        try:
            done, _ = await asyncio.wait(
                {request, verified}, timeout=15, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                raise TimeoutError(
                    "The PIN request neither completed nor reached real verification"
                )
            if request.done():
                premature = request.result()  # Preserve unknown request failures.
            else:
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
        finally:
            release.set()
            if not verified.done():
                verified.cancel()
            await asyncio.gather(verified, return_exceptions=True)
            if not request.done():
                request.cancel()
            outcomes = await asyncio.wait_for(
                asyncio.gather(request, return_exceptions=True), timeout=15
            )
            for outcome in outcomes:
                if isinstance(outcome, BaseException) and not isinstance(
                    outcome, asyncio.CancelledError
                ):
                    raise outcome
            if completed.is_set():
                await asyncio.wait_for(idle.wait(), 15)
        if premature is not None:
            assert premature.status_code == 401, (
                "An unspent, schema-valid PIN guess must be admitted before its failure refusal: "
                f"{premature.status_code} {premature.text}"
            )
            assert completed.is_set(), "The admitted PIN guess must perform real verification"
        # Restore the verifier before retrying, so a capacity reservation cannot
        # be mistaken for the guess ledger's refusal of this proven correct PIN.
        monkeypatch.setattr(auth_api, "verify_password_async", real_verify)
        denied = await _worker_login(browser, farm, membership, "4321")
        assert denied.status_code == 429, denied.text


async def test_distributed_pin_failures_reach_the_farm_spray_budget(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, "spray-protocol-owner@farm.in")
    memberships = []
    for index in range(11):
        membership, farm = await _make_pin_worker(
            client, owner, email=f"spray-protocol-{index}@farm.in"
        )
        memberships.append(membership)
    _configure(monkeypatch, auth_rate_limit_enabled=True, worker_pin_rate_limit_max_attempts=2)
    for membership in memberships[:-1]:
        for _ in range(2):
            denied = await _worker_login(client, farm, membership, "9999")
            assert denied.status_code == 401, denied.text
    # A fresh identity has no individual failures; the farm-level history
    # alone must stop the next real guess before spending another hash.
    blocked = await _worker_login(client, farm, memberships[-1], "4321")
    assert blocked.status_code == 429, blocked.text


async def test_enabling_totp_closes_a_live_pin_session_without_closing_password_signin(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, "factor-pin-owner@farm.in")
    worker = await _password_worker(client, owner, "factor-pin-worker@farm.in")
    worker_password = await login_and_rotate(client, "factor-pin-worker@farm.in", "workerpass123")
    await create_farm(client, worker_password, "This worker owns another farm")
    reset = await client.post(
        f"/api/team/workers/{worker['id']}/reset-pin", headers=owner, json={"pin": "4321"}
    )
    assert reset.status_code == 200, reset.text
    password_login = await client.post(
        "/api/auth/login",
        json={"email": "factor-pin-worker@farm.in", "password": "workerpass123!r1"},
    )
    assert password_login.status_code == 200, password_login.text
    password = {"Authorization": f"Bearer {password_login.json()['access_token']}"}
    pin_login = await _worker_login(client, int(owner["X-Farm-Id"]), worker["id"], "4321")
    assert pin_login.status_code == 200, pin_login.text
    pin = {"Authorization": f"Bearer {pin_login.json()['access_token']}"}
    await _enroll_and_activate(client, password, password="workerpass123!r1")
    assert (await client.get("/api/auth/me", headers=password)).status_code == 200
    assert (await client.get("/api/auth/me", headers=pin)).status_code == 401
    denied = await client.post("/api/auth/refresh")
    assert denied.status_code == 401, denied.text
    assert "Password sign-in" in denied.json()["detail"]


@pytest.mark.parametrize(
    "credential",
    [
        "retained-signed",
        "retained-persisted",
        "compacted-signed",
        "compacted-persisted",
        "bearer-persisted",
    ],
)
async def test_logout_at_an_exact_expiry_cannot_revoke_a_live_credential_family(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, credential: str
) -> None:
    headers = await register(client, "expired-logout-boundary@farm.in")
    predecessor = _cookie(client)
    claims = _claims(predecessor)
    if credential.startswith("compacted"):
        _configure(monkeypatch, refresh_max_sessions_per_family=2)
        for _ in range(2):
            rotated = await client.post("/api/auth/refresh")
            assert rotated.status_code == 200, rotated.text
    live = await client.post(
        "/api/auth/login", json={"email": "expired-logout-boundary@farm.in", "password": OWNER_PW}
    )
    assert live.status_code == 200, live.text
    live_cookie = _cookie(client)
    boundary = (
        claims.expires_at if credential.endswith("signed") else utcnow() + timedelta(seconds=10)
    )
    if credential.endswith("persisted"):
        async with get_sessionmaker()() as db:
            await db.execute(
                update(RefreshSession)
                .where(RefreshSession.family_id == claims.family_id)
                .values(expires_at=boundary)
            )
            await db.commit()
    monkeypatch.setattr(auth_api, "utcnow", lambda: boundary)
    async with _http(client) as browser:
        if credential.startswith("bearer"):
            logout_headers = headers
        else:
            logout_headers = {"Cookie": f"{get_settings().refresh_cookie_name}={predecessor}"}
        logged_out = await browser.post("/api/auth/logout", headers=logout_headers)
    assert logged_out.status_code == 204, logged_out.text
    assert not await _family_revoked(live_cookie)
    assert not await _family_revoked(predecessor)
    # The stale proof cannot change the generation of an independently live
    # session, even though the browser always receives a cookie clear.
    companion = {"Authorization": f"Bearer {live.json()['access_token']}"}
    assert (await client.get("/api/auth/me", headers=companion)).status_code == 200


async def test_pin_farm_list_rechecks_a_membership_revoked_after_authentication(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, "farm-list-recheck-owner@farm.in")
    membership, farm = await _make_pin_worker(
        client, owner, email="farm-list-recheck-worker@farm.in"
    )
    login = await _worker_login(client, farm, membership, "4321")
    assert login.status_code == 200, login.text
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("This PIN membership race requires its ASGI transport")
    authenticated = asyncio.Event()
    release = asyncio.Event()

    async def recheck(db: AsyncSession, user_id: int, farm_id: int) -> FarmMembership | None:
        authenticated.set()
        await release.wait()
        return await active_membership(db, user_id, farm_id)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport.app), base_url="http://test"
    ) as browser:
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        visible = await browser.get("/api/auth/farms", headers=headers)
        assert visible.status_code == 200, visible.text
        assert [row["id"] for row in visible.json()] == [farm]
        monkeypatch.setattr(auth_api, "active_membership", recheck)
        listing = asyncio.create_task(browser.get("/api/auth/farms", headers=headers))
        try:
            await asyncio.wait_for(authenticated.wait(), 15)
            deactivated = await client.put(
                f"/api/team/workers/{membership}/status",
                headers=owner,
                json={"is_active": False},
            )
            assert deactivated.status_code == 200, deactivated.text
        finally:
            release.set()
            denied = await asyncio.wait_for(listing, 15)
    assert denied.status_code == 401, denied.text


async def test_ownership_transfer_invalidates_an_already_issued_mfa_challenge(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, "transfer-proof-owner@farm.in")
    worker = await _password_worker(client, owner, "transfer-proof-worker@farm.in")
    successor = await login_and_rotate(client, "transfer-proof-worker@farm.in", "workerpass123")
    secret, _ = await _enroll_and_activate(client, successor, password="workerpass123!r1")
    login = await client.post(
        "/api/auth/login",
        json={"email": "transfer-proof-worker@farm.in", "password": "workerpass123!r1"},
    )
    assert login.status_code == 200, login.text
    challenge = login.json()["mfa_token"]
    transferred = await client.post(
        f"/api/auth/farms/{owner['X-Farm-Id']}/transfer-ownership",
        headers=owner,
        json={"membership_id": worker["id"], "current_password": OWNER_PW},
    )
    assert transferred.status_code == 200, transferred.text
    code, _ = _current_code(secret, drift=1)
    denied = await client.post(
        "/api/auth/totp/challenge", json={"mfa_token": challenge, "code": code}
    )
    assert denied.status_code == 401, denied.text
    assert "set-cookie" not in denied.headers


async def test_ownership_transfer_accepts_the_largest_valid_database_farm_identity(
    client: httpx.AsyncClient,
) -> None:
    owner = await register(client, "max-farm-owner@farm.in")
    # A restored production sequence may legitimately reach the final int32
    # value. Let PostgreSQL issue that identity through normal farm creation,
    # including its real tenant seed and FK records, rather than forging a
    # farm or changing an existing immutable identity.
    async with get_sessionmaker()() as db:
        await db.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('farms', 'id'), :previous, true)"
            ).bindparams(previous=MAX_INT32_ID - 1)
        )
        await db.commit()
    owner = await create_farm(client, owner, "Last int32 farm")
    assert int(owner["X-Farm-Id"]) == MAX_INT32_ID
    worker = await _password_worker(client, owner, "max-farm-successor@farm.in")
    await login_and_rotate(client, "max-farm-successor@farm.in", "workerpass123")
    transferred = await client.post(
        f"/api/auth/farms/{MAX_INT32_ID}/transfer-ownership",
        headers=owner,
        json={"membership_id": worker["id"], "current_password": OWNER_PW},
    )
    assert transferred.status_code == 200, transferred.text
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, MAX_INT32_ID)
        assert farm is not None and farm.owner_id == worker["user_id"]


async def test_genuinely_expired_bearer_logout_remains_idempotent_without_invalid_token_throttling(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await register(client, "expired-bearer-budget@farm.in")
    cookie = _cookie(client)
    payload, expired = security._decode_payload_result(
        headers["Authorization"].removeprefix("Bearer "), "access"
    )
    assert payload is not None and not expired
    after_expiry = datetime.fromtimestamp(int(payload["exp"]), UTC) + timedelta(minutes=2)

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(after_expiry.timestamp(), tz)

    monkeypatch.setattr(security, "datetime", ClockDateTime)
    _configure(monkeypatch, auth_rate_limit_enabled=True, auth_rate_limit_max_attempts=1)
    decoded = security.decode_access_claims_result(headers["Authorization"].removeprefix("Bearer "))
    assert decoded.claims is None and decoded.expired
    async with _http(client) as browser:
        for _ in range(2):
            response = await browser.post("/api/auth/logout", headers=headers)
            assert response.status_code == 204, response.text
    assert not await _family_revoked(cookie)


async def test_cancellation_after_password_acceptance_does_not_spend_the_failure_budget(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = "proven-login-cancellation@farm.in"
    headers = await register(client, email)
    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    user_id = int(me.json()["id"])
    _configure(monkeypatch, auth_rate_limit_enabled=True, auth_rate_limit_max_attempts=1)
    request: asyncio.Task[httpx.Response] | None = None
    async with get_sessionmaker()() as holder, get_sessionmaker()() as observer:
        await holder.execute(select(User.id).where(User.id == user_id).with_for_update())
        holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        assert holder_pid is not None
        try:
            request = asyncio.create_task(
                client.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
            )
            # Waiting on this row proves the real password verification and
            # credential-accepted boundary completed before cancellation.
            async with asyncio.timeout(15):
                while True:
                    blocked = await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname=current_database() "
                            "AND :holder=ANY(pg_blocking_pids(pid)))"
                        ).bindparams(holder=holder_pid)
                    )
                    # PostgreSQL's activity/statistics snapshot belongs to
                    # this observer transaction. A new transaction must see
                    # a wait that began after our preceding observation.
                    await observer.rollback()
                    if blocked:
                        break
                    assert not request.done(), "login did not reach its final principal lock"
                    await asyncio.sleep(0)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        finally:
            await holder.rollback()
            if request is not None and not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
    accepted = await client.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
    assert accepted.status_code == 200, accepted.text


async def test_grace_replay_does_not_extend_a_subsecond_successor_cookie_lifetime(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, refresh_token_ttl_seconds=1)
    second = utcnow().replace(microsecond=0)
    clock = [second + timedelta(milliseconds=100)]
    monkeypatch.setattr(auth_api, "utcnow", lambda: clock[0])
    await register(client, "subsecond-refresh-cookie@farm.in")
    predecessor = _cookie(client)
    clock[0] = second + timedelta(milliseconds=300)
    assert (await client.post("/api/auth/refresh")).status_code == 200
    successor = _cookie(client)
    async with get_sessionmaker()() as db:
        expiry = await db.scalar(
            select(RefreshSession.expires_at).where(RefreshSession.jti == _claims(successor).jti)
        )
    assert expiry is not None
    clock[0] = second + timedelta(milliseconds=900)
    remaining = (expiry - clock[0]).total_seconds()
    assert 0 < remaining < 1
    set_refresh_cookie(client, predecessor)
    replay = await client.post("/api/auth/refresh")
    assert replay.status_code == 200, replay.text
    cookies = SimpleCookie()
    cookies.load(replay.headers["set-cookie"])
    advertised = int(cookies[get_settings().refresh_cookie_name]["max-age"])
    assert 0 <= advertised <= remaining, "browser cookie age exceeded the fixed server expiry"


async def test_backward_clock_rotation_cannot_replay_a_successor_at_its_exact_expiry(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, refresh_token_ttl_seconds=1)
    second = utcnow().replace(microsecond=0)
    clock = [second + timedelta(milliseconds=500)]
    monkeypatch.setattr(auth_api, "utcnow", lambda: clock[0])
    await register(client, "backward-clock-expired-successor@farm.in")
    predecessor = _cookie(client)
    clock[0] -= timedelta(seconds=1)
    assert (await client.post("/api/auth/refresh")).status_code == 200
    successor = _cookie(client)
    async with get_sessionmaker()() as db:
        expiry = await db.scalar(
            select(RefreshSession.expires_at).where(RefreshSession.jti == _claims(successor).jti)
        )
    assert expiry is not None
    clock[0] = expiry
    assert _claims(predecessor).expires_at > clock[0]
    set_refresh_cookie(client, predecessor)
    denied = await client.post("/api/auth/refresh")
    assert denied.status_code == 401, denied.text
    assert await _family_revoked(successor)


async def test_transferred_owner_can_rotate_a_retained_pin_but_cannot_sign_in_with_it(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, "owner-pin-transfer@farm.in")
    worker = await _password_worker(client, owner, "owner-pin-successor@farm.in")
    await login_and_rotate(client, "owner-pin-successor@farm.in", "workerpass123")
    transferred = await client.post(
        f"/api/auth/farms/{owner['X-Farm-Id']}/transfer-ownership",
        headers=owner,
        json={"membership_id": worker["id"], "current_password": OWNER_PW},
    )
    assert transferred.status_code == 200, transferred.text
    password_login = await client.post(
        "/api/auth/login",
        json={"email": "owner-pin-successor@farm.in", "password": "workerpass123!r1"},
    )
    assert password_login.status_code == 200, password_login.text
    successor = {
        "Authorization": f"Bearer {password_login.json()['access_token']}",
        "X-Farm-Id": owner["X-Farm-Id"],
    }
    reset = await client.post(
        f"/api/team/workers/{worker['id']}/reset-pin", headers=successor, json={"pin": "4321"}
    )
    assert reset.status_code == 200, reset.text
    # PIN reset revokes the old password family, so reauthenticate through
    # the supported owner/password path before demonstrating the refusal.
    password_login = await client.post(
        "/api/auth/login",
        json={"email": "owner-pin-successor@farm.in", "password": "workerpass123!r1"},
    )
    assert password_login.status_code == 200, password_login.text
    successor["Authorization"] = f"Bearer {password_login.json()['access_token']}"
    password_cookie = _cookie(client)
    denied = await _worker_login(client, int(owner["X-Farm-Id"]), worker["id"], "4321")
    assert denied.status_code == 401, denied.text
    assert _cookie(client) == password_cookie
    assert (await client.get("/api/team", headers=successor)).status_code == 200
