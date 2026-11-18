"""Cancelling a login before secret work does not spend its failure budget."""

import asyncio
from typing import Any

import asyncpg
import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Connection

from app.api import auth as auth_api
from app.core.config import Settings, get_settings
from app.db import get_engine, get_sessionmaker
from app.ratelimit import SlidingWindowRateLimiter

from .conftest import OWNER_PW, register


async def test_cancelling_the_blocked_user_lookup_preserves_a_valid_login_budget(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    email = "cancel-before-password-work@farm.in"
    await register(client, email)
    configured = Settings.model_validate(
        get_settings().model_dump()
        | {
            "auth_rate_limit_enabled": True,
            "auth_rate_limit_max_attempts": 1,
            "auth_rate_limit_window_seconds": 3600,
        }
    )
    for name in (
        "auth_rate_limit_enabled",
        "auth_rate_limit_max_attempts",
        "auth_rate_limit_window_seconds",
    ):
        monkeypatch.setattr(get_settings(), name, getattr(configured, name))
    limiter = SlidingWindowRateLimiter()
    monkeypatch.setattr(auth_api, "auth_limiter", limiter)
    transport = client._transport
    if not isinstance(transport, httpx.ASGITransport):
        raise RuntimeError("This public-route scheduling test requires its ASGI transport")
    lookup_pid: int | None = None

    def observe_lookup(
        connection: Connection,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        nonlocal lookup_pid
        query = " ".join(statement.split())
        values = parameters.values() if isinstance(parameters, dict) else parameters
        if (
            query.startswith(
                "SELECT users.id, users.password_hash, users.token_version FROM users "
            )
            and "users.email =" in query
            and "users.deleted_at IS NULL" in query
            and email in values
        ):
            driver = connection.connection.driver_connection
            if not isinstance(driver, asyncpg.Connection):
                raise RuntimeError("Cannot identify the real PostgreSQL login connection")
            lookup_pid = driver.get_server_pid()

    pending: asyncio.Task[httpx.Response] | None = None
    engine = get_engine().sync_engine
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport.app), base_url="http://test"
        ) as browser:
            async with get_sessionmaker()() as holder, get_sessionmaker()() as observer:
                event.listen(engine, "before_cursor_execute", observe_lookup)
                try:
                    holder_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
                    if not isinstance(holder_pid, int):
                        raise RuntimeError("Cannot identify the owned lock-holder connection")
                    await holder.execute(text("LOCK TABLE users IN ACCESS EXCLUSIVE MODE"))
                    pending = asyncio.create_task(
                        browser.post("/api/auth/login", json={"email": email, "password": OWNER_PW})
                    )
                    # A table lock blocks the first plain credential snapshot,
                    # unlike a row lock that only blocks the post-verify reload.
                    # Observe the exact query's real PID and its actual blocker.
                    async with asyncio.timeout(15):
                        while True:
                            if pending.done():
                                pending.result()
                                raise RuntimeError("Login ended before the controlled lookup wait")
                            pid = lookup_pid
                            if pid is not None:
                                blocked = await observer.scalar(
                                    text(
                                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                        "WHERE pid=:lookup AND datname=current_database() "
                                        "AND wait_event_type='Lock' "
                                        "AND :holder=ANY(pg_blocking_pids(pid)))"
                                    ).bindparams(lookup=pid, holder=holder_pid)
                                )
                                # Activity snapshots are transaction-scoped;
                                # each sample must see newly established waits.
                                await observer.rollback()
                                if blocked:
                                    break
                            await asyncio.sleep(0)
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(pending, timeout=10)
                finally:
                    try:
                        if pending is not None and not pending.done():
                            pending.cancel()
                            await asyncio.wait_for(
                                asyncio.gather(pending, return_exceptions=True), timeout=10
                            )
                    finally:
                        try:
                            await observer.rollback()
                        finally:
                            try:
                                await holder.rollback()
                            finally:
                                event.remove(engine, "before_cursor_execute", observe_lookup)
            accepted = await browser.post(
                "/api/auth/login", json={"email": email, "password": OWNER_PW}
            )
            assert accepted.status_code == 200, (
                "A login cancelled before its credential lookup completed must not spend "
                f"the valid user's failed-secret budget: {accepted.status_code} {accepted.text}"
            )
    finally:
        limiter.clear()
