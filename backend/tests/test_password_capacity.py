"""Availability regressions for bounded off-loop password work."""

from __future__ import annotations

import asyncio
import logging
import threading

import httpx
import pytest

import app.api.auth as auth_api
import app.api.team as team_api
import app.security as security
from app.core.config import get_settings
from app.db import get_engine
from app.ratelimit import auth_limiter

from .conftest import OWNER_PW, owner_with_farm, register


async def test_password_work_is_off_loop_and_has_no_waiting_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    started = [threading.Event() for _ in range(security._password_worker_count)]
    call_index = 0
    call_lock = threading.Lock()

    def stalled_hash(password: str) -> str:
        nonlocal call_index
        with call_lock:
            index = call_index
            call_index += 1
        started[index].set()
        assert release.wait(timeout=5)
        return f"hashed:{password}"

    monkeypatch.setattr(security, "hash_password", stalled_hash)
    jobs = [
        asyncio.create_task(security.hash_password_async(f"password-{index}"))
        for index in range(security._password_worker_count)
    ]
    try:
        for signal in started:
            assert await asyncio.to_thread(signal.wait, 2)

        # The event loop remains responsive while every native worker is busy.
        ticked = False

        async def event_loop_tick() -> None:
            nonlocal ticked
            await asyncio.sleep(0)
            ticked = True

        await asyncio.wait_for(event_loop_tick(), timeout=0.1)
        assert ticked

        # Capacity is workers, not workers plus an attacker-controlled queue.
        with pytest.raises(security.PasswordWorkCapacityError):
            await security.hash_password_async("must-fail-fast")
    finally:
        release.set()
        await asyncio.gather(*jobs)


async def test_password_capacity_error_maps_to_retryable_429(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def saturated_verify(password: str, stored: str) -> tuple[bool, bool, bool]:
        raise security.PasswordWorkCapacityError("busy")

    monkeypatch.setattr(auth_api, "verify_password_with_work_async", saturated_verify)
    response = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "not-the-password"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    # The 429 carries the machine-readable code for localized clients.
    assert response.json() == {
        "detail": "Password service is busy — please retry shortly.",
        "code": "RATE_LIMITED",
    }


async def test_same_email_burst_is_reserved_before_password_work(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def stalled_verify(password: str, stored: str) -> tuple[bool, bool, bool]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return False, False, False

    monkeypatch.setattr(auth_api, "verify_password_with_work_async", stalled_verify)
    first = asyncio.create_task(
        client.post(
            "/api/auth/login",
            json={"email": "same-burst@farm.in", "password": "wrongpass123"},
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        second = await asyncio.wait_for(
            client.post(
                "/api/auth/login",
                json={"email": "same-burst@farm.in", "password": "wrongpass123"},
            ),
            timeout=0.5,
        )
        assert second.status_code == 429
        assert second.headers["Retry-After"] == "1"
        assert calls == 1
    finally:
        release.set()
    assert (await first).status_code == 401


async def test_current_password_verification_holds_no_database_connection(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Login/change/delete release their snapshot transaction before Argon."""
    headers = await register(client, "argon-connection@farm.in")
    cases = [
        (
            "/api/auth/login",
            {"email": "argon-connection@farm.in", "password": "wrongpass123"},
            None,
            401,
        ),
        (
            "/api/auth/change-password",
            {"current_password": "wrongpass123", "new_password": "replacement123"},
            headers,
            400,
        ),
        (
            "/api/auth/account",
            {"current_password": "wrongpass123"},
            headers,
            400,
        ),
    ]
    for path, payload, request_headers, expected_status in cases:
        started = asyncio.Event()
        release = asyncio.Event()

        async def stalled_verify(
            password: str,
            stored: str,
            started_signal: asyncio.Event = started,
            release_signal: asyncio.Event = release,
        ) -> tuple[bool, bool]:
            started_signal.set()
            await release_signal.wait()
            return False, False

        async def stalled_verify_with_work(
            password: str,
            stored: str,
            started_signal: asyncio.Event = started,
            release_signal: asyncio.Event = release,
        ) -> tuple[bool, bool, bool]:
            started_signal.set()
            await release_signal.wait()
            return False, False, False

        if path == "/api/auth/login":
            monkeypatch.setattr(
                auth_api, "verify_password_with_work_async", stalled_verify_with_work
            )
        else:
            monkeypatch.setattr(auth_api, "verify_password_async", stalled_verify)
        request = asyncio.create_task(
            client.request(
                "DELETE" if path == "/api/auth/account" else "POST",
                path,
                json=payload,
                headers=request_headers,
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            pool = get_engine().sync_engine.pool
            assert pool.checkedout() == 0
        finally:
            release.set()
        response = await request
        assert response.status_code == expected_status, response.text


async def test_replacement_hashing_holds_no_database_connection(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await register(client, "argon-hash-connection@farm.in", OWNER_PW)
    started = asyncio.Event()
    release = asyncio.Event()

    async def accepted_verify(password: str, stored: str) -> tuple[bool, bool]:
        return True, False

    async def stalled_hash(password: str) -> str:
        started.set()
        await release.wait()
        return "replacement-hash-created-off-transaction"

    monkeypatch.setattr(auth_api, "verify_password_async", accepted_verify)
    monkeypatch.setattr(auth_api, "hash_password_async", stalled_hash)
    change = asyncio.create_task(
        client.post(
            "/api/auth/change-password",
            json={"current_password": OWNER_PW, "new_password": "replacement123"},
            headers=headers,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert get_engine().sync_engine.pool.checkedout() == 0
    finally:
        release.set()
    response = await change
    assert response.status_code == 200, response.text


async def test_team_password_hashing_uses_only_the_required_transaction(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="team-argon-owner@farm.in")
    team = await client.get("/api/team", headers=owner)
    assert team.status_code == 200, team.text
    role_id = next(role["id"] for role in team.json()["roles"] if role["code"] == "CLEANER")
    existing = await client.post(
        "/api/team/workers",
        json={
            "email": "team-argon-existing@farm.in",
            "password": "workerpass123",
            "role_id": role_id,
        },
        headers=owner,
    )
    assert existing.status_code == 201, existing.text

    cases = [
        (
            "/api/team/workers",
            {
                "email": "team-argon-new@farm.in",
                "password": "workerpass123",
                "role_id": role_id,
            },
            201,
        ),
        (
            f"/api/team/workers/{existing.json()['id']}/reset-password",
            {"password": "replacement123"},
            200,
        ),
    ]
    for path, payload, expected_status in cases:
        started = asyncio.Event()
        release = asyncio.Event()

        async def stalled_hash(
            password: str,
            started_signal: asyncio.Event = started,
            release_signal: asyncio.Event = release,
        ) -> str:
            started_signal.set()
            await release_signal.wait()
            return "prepared-off-transaction-password-hash"

        monkeypatch.setattr(team_api, "hash_password_async", stalled_hash)
        request = asyncio.create_task(client.post(path, json=payload, headers=owner))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            # Both creation and reset release their read-only authorization /
            # replay-preflight transaction before memory-hard password work.
            assert get_engine().sync_engine.pool.checkedout() == 0
        finally:
            release.set()
        response = await request
        assert response.status_code == expected_status, response.text


async def test_one_owner_cannot_fill_every_team_password_work_slot(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_a = await owner_with_farm(
        client,
        email="argon-fairness-a@farm.in",
        farm_name="Argon Fairness A",
    )
    owner_b = await owner_with_farm(
        client,
        email="argon-fairness-b@farm.in",
        farm_name="Argon Fairness B",
    )
    team_a = (await client.get("/api/team", headers=owner_a)).json()
    team_b = (await client.get("/api/team", headers=owner_b)).json()
    role_a = next(role["id"] for role in team_a["roles"] if role["code"] == "CLEANER")
    role_b = next(role["id"] for role in team_b["roles"] if role["code"] == "CLEANER")
    target = await client.post(
        "/api/team/workers",
        json={
            "email": "argon-fairness-target@farm.in",
            "password": "initial-worker-password",
            "role_id": role_a,
        },
        headers=owner_a,
    )
    assert target.status_code == 201, target.text

    started_a = asyncio.Event()
    started_b = asyncio.Event()
    release = asyncio.Event()
    admitted_passwords: list[str] = []

    async def stalled_hash(password: str) -> str:
        admitted_passwords.append(password)
        if password == "owner-a-create-password":
            started_a.set()
        elif password == "owner-b-create-password":
            started_b.set()
        await release.wait()
        return f"prepared:{password}"

    monkeypatch.setattr(team_api, "hash_password_async", stalled_hash)
    first_a = asyncio.create_task(
        client.post(
            "/api/team/workers",
            json={
                "email": "argon-fairness-a-new@farm.in",
                "password": "owner-a-create-password",
                "role_id": role_a,
            },
            headers=owner_a,
        )
    )
    first_b: asyncio.Task[httpx.Response] | None = None
    try:
        await asyncio.wait_for(started_a.wait(), timeout=2)

        # Create and reset share one owner reservation. This request fails fast
        # before reaching the global Argon pool while A's create is in flight.
        saturated_a = await asyncio.wait_for(
            client.post(
                f"/api/team/workers/{target.json()['id']}/reset-password",
                json={"password": "owner-a-reset-password"},
                headers=owner_a,
            ),
            timeout=0.5,
        )
        assert saturated_a.status_code == 429, saturated_a.text
        assert saturated_a.headers["Retry-After"] == "1"
        assert "owner-a-reset-password" not in admitted_passwords

        # A different owner is admitted while A is still stalled, leaving the
        # remaining global capacity available to unrelated auth/team traffic.
        first_b = asyncio.create_task(
            client.post(
                "/api/team/workers",
                json={
                    "email": "argon-fairness-b-new@farm.in",
                    "password": "owner-b-create-password",
                    "role_id": role_b,
                },
                headers=owner_b,
            )
        )
        await asyncio.wait_for(started_b.wait(), timeout=2)
    finally:
        release.set()

    assert (await first_a).status_code == 201
    assert first_b is not None
    assert (await first_b).status_code == 201
    assert admitted_passwords == ["owner-a-create-password", "owner-b-create-password"]


async def test_team_create_and_reset_share_preargon_sliding_budget(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="argon-budget-owner@farm.in")
    team = (await client.get("/api/team", headers=owner)).json()
    role_id = next(role["id"] for role in team["roles"] if role["code"] == "CLEANER")
    target = await client.post(
        "/api/team/workers",
        json={
            "email": "argon-budget-target@farm.in",
            "password": "initial-worker-password",
            "role_id": role_id,
        },
        headers=owner,
    )
    assert target.status_code == 201, target.text
    await register(client, "argon-budget-existing@farm.in", "existing-password")

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "auth_rate_limit_max_attempts", 2)
    monkeypatch.setattr(settings, "auth_rate_limit_window_seconds", 300)
    auth_limiter.clear()
    hash_calls = 0

    async def counted_hash(password: str) -> str:
        nonlocal hash_calls
        hash_calls += 1
        return f"prepared:{password}"

    monkeypatch.setattr(team_api, "hash_password_async", counted_hash)
    try:
        # This reaches Argon but fails generically after hashing because the
        # global account already exists.
        existing = await client.post(
            "/api/team/workers",
            json={
                "email": "argon-budget-existing@farm.in",
                "password": "first-budget-password",
                "role_id": role_id,
            },
            headers=owner,
        )
        assert existing.status_code == 400, existing.text

        reset = await client.post(
            f"/api/team/workers/{target.json()['id']}/reset-password",
            json={"password": "second-budget-password"},
            headers=owner,
        )
        assert reset.status_code == 200, reset.text

        blocked = await client.post(
            "/api/team/workers",
            json={
                "email": "argon-budget-blocked@farm.in",
                "password": "third-budget-password",
                "role_id": role_id,
            },
            headers=owner,
        )
        assert blocked.status_code == 429, blocked.text
        assert blocked.headers["Retry-After"] == "300"
        assert blocked.json()["detail"] == team_api.TEAM_PASSWORD_WORK_LIMIT_REASON
        assert hash_calls == 2
    finally:
        auth_limiter.clear()


async def test_cancelled_team_hash_keeps_owner_reservation_until_work_finishes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="argon-cancel-owner@farm.in")
    team = (await client.get("/api/team", headers=owner)).json()
    role_id = next(role["id"] for role in team["roles"] if role["code"] == "CLEANER")
    started = asyncio.Event()
    release = asyncio.Event()
    hash_calls = 0

    async def cancellation_resistant_hash(password: str) -> str:
        nonlocal hash_calls
        hash_calls += 1
        started.set()
        await release.wait()
        return f"prepared:{password}"

    monkeypatch.setattr(team_api, "hash_password_async", cancellation_resistant_hash)
    cancelled_request = asyncio.create_task(
        client.post(
            "/api/team/workers",
            json={
                "email": "argon-cancel-abandoned@farm.in",
                "password": "cancelled-worker-password",
                "role_id": role_id,
            },
            headers=owner,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    cancelled_request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_request

    still_reserved = await client.post(
        "/api/team/workers",
        json={
            "email": "argon-cancel-blocked@farm.in",
            "password": "blocked-worker-password",
            "role_id": role_id,
        },
        headers=owner,
    )
    assert still_reserved.status_code == 429, still_reserved.text
    assert hash_calls == 1

    release.set()
    for _attempt in range(100):
        if not auth_limiter._reservations:  # white-box: callback released the exact slot
            break
        await asyncio.sleep(0.01)
    assert not auth_limiter._reservations

    retry = await client.post(
        "/api/team/workers",
        json={
            "email": "argon-cancel-retry@farm.in",
            "password": "retry-worker-password",
            "role_id": role_id,
        },
        headers=owner,
    )
    assert retry.status_code == 201, retry.text
    assert hash_calls == 2


async def test_team_password_throttle_logs_the_throttled_actor_id(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The sliding-budget 429 emits exactly one audit line naming the owner id."""
    owner = await owner_with_farm(client, email="argon-throttle-log-owner@farm.in")
    actor_id = (await client.get("/api/auth/me", headers=owner)).json()["id"]
    team = (await client.get("/api/team", headers=owner)).json()
    role_id = next(role["id"] for role in team["roles"] if role["code"] == "CLEANER")

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "auth_rate_limit_max_attempts", 1)
    monkeypatch.setattr(settings, "auth_rate_limit_window_seconds", 300)
    auth_limiter.clear()

    async def fast_hash(password: str) -> str:
        return f"prepared:{password}"

    monkeypatch.setattr(team_api, "hash_password_async", fast_hash)
    try:
        first = await client.post(
            "/api/team/workers",
            json={
                "email": "throttle-log-a@farm.in",
                "password": "first-log-password",
                "role_id": role_id,
            },
            headers=owner,
        )
        assert first.status_code == 201, first.text

        with caplog.at_level(logging.INFO, logger="goatfarm.team"):
            blocked = await client.post(
                "/api/team/workers",
                json={
                    "email": "throttle-log-b@farm.in",
                    "password": "second-log-password",
                    "role_id": role_id,
                },
                headers=owner,
            )
        assert blocked.status_code == 429, blocked.text
        assert blocked.json()["detail"] == team_api.TEAM_PASSWORD_WORK_LIMIT_REASON

        # The refusal must be attributable to exactly one real owner id: the
        # access log carries no principal, so this is the only attribution.
        records = [record for record in caplog.records if record.name == "goatfarm.team"]
        assert [record.getMessage() for record in records] == [
            f"team password work throttled (actor_id={actor_id})"
        ]
    finally:
        auth_limiter.clear()
