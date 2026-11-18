"""Blocked authentication histories survive unrelated public-route churn.

These exercise the public routes with the supported reduced-capacity memory
backend constructor. It does not measure an attack against the default 50k
deployment capacity or introduce a deployment setting for that capacity.
"""

import httpx
import pytest
from sqlalchemy import func, select

from app.api import auth as auth_api
from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import FarmMembership
from app.ratelimit import MemoryLimiterBackend, SlidingWindowRateLimiter

from .conftest import owner_with_farm


def _client_from_ip(client: httpx.AsyncClient, ip: str) -> httpx.AsyncClient:
    transport = client._transport
    assert isinstance(transport, httpx.ASGITransport)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport.app, client=(ip, 42001)),
        base_url="http://test",
    )


async def test_a_blocked_farm_pin_spray_survives_unrelated_client_eviction_pressure(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="spray-eviction-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    unknown_memberships = list(range(10_000, 10_016))
    async with get_sessionmaker()() as db:
        existing = await db.scalar(
            select(func.count())
            .select_from(FarmMembership)
            .where(FarmMembership.id.in_(unknown_memberships))
        )
        assert existing == 0, "The hostile requests must use genuinely absent memberships"
    configured = Settings.model_validate(
        get_settings().model_dump()
        | {
            "auth_rate_limit_enabled": True,
            "worker_pin_rate_limit_max_attempts": 1,
            "worker_pin_rate_limit_window_seconds": 3600,
        }
    )
    for name in (
        "auth_rate_limit_enabled",
        "worker_pin_rate_limit_max_attempts",
        "worker_pin_rate_limit_window_seconds",
    ):
        monkeypatch.setattr(get_settings(), name, getattr(configured, name))
    limiter = SlidingWindowRateLimiter(backend=MemoryLimiterBackend(max_keys=24))
    monkeypatch.setattr(auth_api, "auth_limiter", limiter)
    try:
        async with _client_from_ip(client, "198.51.100.10") as original_client:
            for membership_id in unknown_memberships[:10]:
                denied = await original_client.post(
                    "/api/auth/worker-login",
                    json={"farm_id": farm_id, "membership_id": membership_id, "pin": "9999"},
                )
                assert denied.status_code == 401, denied.text

            # Do not probe the original IP between its final failure and the
            # churn. A probe itself legitimately reclassifies its live budget.
            # Every pressure entry below is produced by a real failed request.
            for index, membership_id in enumerate(unknown_memberships[10:15], start=21):
                async with _client_from_ip(client, f"198.51.100.{index}") as other_client:
                    denied = await other_client.post(
                        "/api/auth/worker-login",
                        json={"farm_id": farm_id, "membership_id": membership_id, "pin": "9999"},
                    )
                    assert denied.status_code == 401, denied.text

            blocked = await original_client.post(
                "/api/auth/worker-login",
                json={
                    "farm_id": farm_id,
                    "membership_id": unknown_memberships[-1],
                    "pin": "9999",
                },
            )
            assert blocked.status_code == 429, (
                "Other clients' new bookkeeping keys must not reopen this IP/farm's "
                f"still-live PIN spray budget: {blocked.status_code} {blocked.text}"
            )
    finally:
        limiter.clear()


async def test_a_register_ip_at_its_budget_survives_unrelated_client_eviction_pressure(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = Settings.model_validate(
        get_settings().model_dump()
        | {
            "auth_rate_limit_enabled": True,
            "auth_rate_limit_max_attempts": 2,
            "auth_rate_limit_window_seconds": 3600,
            "min_password_length": 12,
        }
    )
    for name in (
        "auth_rate_limit_enabled",
        "auth_rate_limit_max_attempts",
        "auth_rate_limit_window_seconds",
        "min_password_length",
    ):
        monkeypatch.setattr(get_settings(), name, getattr(configured, name))
    limiter = SlidingWindowRateLimiter(backend=MemoryLimiterBackend(max_keys=24))
    monkeypatch.setattr(auth_api, "auth_limiter", limiter)
    try:
        async with _client_from_ip(client, "198.51.100.10") as original_client:
            for index in range(2):
                denied = await original_client.post(
                    "/api/auth/register",
                    json={"email": f"register-victim-{index}@farm.in", "password": "x"},
                )
                # This body passes RegisterIn and reaches the real password
                # policy after charging the IP; no account or email-probe
                # ledger is involved in this ordinary public refusal.
                assert denied.status_code == 400, denied.text

            # Keep the original IP unprobed during pressure. Each foreign
            # source records only one attempt, below its two-attempt ceiling,
            # so its real bookkeeping entry is expendable before a blocked IP.
            for index in range(24):
                async with _client_from_ip(client, f"198.51.100.{index + 31}") as other_client:
                    denied = await other_client.post(
                        "/api/auth/register",
                        json={"email": f"register-pressure-{index}@farm.in", "password": "x"},
                    )
                    assert denied.status_code == 400, denied.text

            blocked = await original_client.post(
                "/api/auth/register",
                json={"email": "register-victim-after-pressure@farm.in", "password": "x"},
            )
            assert blocked.status_code == 429, (
                "Other clients' below-threshold attempts must not reopen this IP's "
                f"still-live registration budget: {blocked.status_code} {blocked.text}"
            )
    finally:
        limiter.clear()
