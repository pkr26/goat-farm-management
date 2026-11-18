"""Real retention/privacy work is committed before a maintenance interval yields."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app import main
from app.core.config import get_settings
from app.db import get_sessionmaker
from app.deps import deactivate_deleted_user_memberships, purge_expired_refresh_sessions
from app.models import FarmMembership, NotificationRecipient, RefreshSession, User
from app.security import decode_access_claims
from app.utils import utcnow

from .conftest import login_and_rotate, owner_with_farm, register
from .test_team_extended import WORKER_PW, add_worker, role_id
from .test_team_reauthorization_and_revocation_contracts import _client, _credential_state

type CleanupKind = Literal["refresh", "deleted-user"]
type Observe = Callable[[int], Awaitable[None]]


async def _refresh_rows(client: httpx.AsyncClient, count: int) -> Observe:
    headers = await register(client, "refresh-maintenance@farm.in")
    claims = decode_access_claims(headers["Authorization"].removeprefix("Bearer "))
    assert claims is not None
    user_id = claims.user_id
    async with get_sessionmaker()() as db:
        user = await db.get(User, user_id)
        assert user is not None
        credentials = (user.password_hash, user.token_version, user.must_change_password)
        expired = [
            RefreshSession(
                user_id=user_id,
                jti=uuid4().hex,
                family_id="maintenance-old",
                expires_at=utcnow() - timedelta(days=60, seconds=index),
                created_at=utcnow() - timedelta(days=61),
            )
            for index in range(count)
        ]
        db.add_all(expired)
        # Recent expiry is still inside the retained security-history window.
        db.add(
            RefreshSession(
                user_id=user_id,
                jti=uuid4().hex,
                family_id="maintenance-recent",
                expires_at=utcnow() - timedelta(days=1),
                created_at=utcnow() - timedelta(days=2),
            )
        )
        await db.commit()
        ids = {row.id for row in expired}
        protected = list(
            (
                await db.execute(
                    select(
                        RefreshSession.id,
                        RefreshSession.jti,
                        RefreshSession.expires_at,
                        RefreshSession.consumed_at,
                        RefreshSession.revoked_at,
                        RefreshSession.replacement_jti,
                    )
                    .where(RefreshSession.user_id == user_id, RefreshSession.id.not_in(ids))
                    .order_by(RefreshSession.id)
                )
            ).all()
        )

    async def observe(remaining: int) -> None:
        async with get_sessionmaker()() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(RefreshSession)
                    .where(RefreshSession.id.in_(ids))
                )
                == remaining
            )
            preserved = list(
                (
                    await db.execute(
                        select(
                            RefreshSession.id,
                            RefreshSession.jti,
                            RefreshSession.expires_at,
                            RefreshSession.consumed_at,
                            RefreshSession.revoked_at,
                            RefreshSession.replacement_jti,
                        )
                        .where(RefreshSession.user_id == user_id, RefreshSession.id.not_in(ids))
                        .order_by(RefreshSession.id)
                    )
                ).all()
            )
            assert preserved == protected
            account = await db.get(User, user_id)
            assert account is not None and account.deleted_at is None
            assert (
                account.password_hash,
                account.token_version,
                account.must_change_password,
            ) == credentials
        assert (await client.get("/api/auth/me", headers=headers)).status_code == 200

    return observe


async def _deleted_workers(client: httpx.AsyncClient, count: int) -> Observe:
    owner = await owner_with_farm(client, email="privacy-maintenance-owner@farm.in")
    role = await role_id(client, owner, "CLEANER")
    memberships: list[int] = []
    users: list[int] = []

    async def worker(index: int, *, deleted: bool) -> tuple[int, dict[str, str]]:
        email = f"privacy-maintenance-{index}@farm.in"
        created = await add_worker(client, owner, role, email)
        assert created.status_code == 201, created.text
        membership = int(created.json()["id"])
        pin = await client.post(
            f"/api/team/workers/{membership}/reset-pin", headers=owner, json={"pin": "012345"}
        )
        assert pin.status_code == 200, pin.text
        preferences = await client.put(
            f"/api/team/workers/{membership}/notifications",
            headers=owner,
            json={
                "phone": f"+9198765{index:05d}",
                "daily_digest": True,
                "screening_flags": True,
                "kidding_watch": True,
                "overdue_critical": True,
                "feed_reorder": True,
                "movement_restriction": True,
                "verified": True,
            },
        )
        assert preferences.status_code == 200, preferences.text
        async with _client() as browser:
            headers = await login_and_rotate(browser, email, WORKER_PW)
            if deleted:
                removed = await browser.request(
                    "DELETE",
                    "/api/auth/account",
                    headers=headers,
                    json={"current_password": WORKER_PW + "!r1"},
                )
                assert removed.status_code == 204, removed.text
                memberships.append(membership)
                users.append(int(created.json()["user_id"]))
        return membership, headers

    for index in range(count):
        await worker(index, deleted=True)
    live_membership, live_headers = await worker(count, deleted=False)
    live_credentials = await _credential_state(live_membership)
    live_endpoint = f"/api/team/workers/{live_membership}/notifications"
    live_preferences = (await client.get(live_endpoint, headers=owner)).json()

    async def observe(remaining: int) -> None:
        async with get_sessionmaker()() as db:
            retained = list(
                (
                    await db.execute(
                        select(FarmMembership).where(FarmMembership.id.in_(memberships))
                    )
                ).scalars()
            )
            assert len(retained) == count, "Cleanup must preserve the historical membership anchors"
            assert sum(member.is_active for member in retained) == remaining
            for member in retained:
                if not member.is_active:
                    assert member.pin_hash is None and member.pin_updated_at is None
                else:
                    assert member.pin_hash is not None and member.pin_updated_at is not None
            recipients = list(
                (
                    await db.execute(
                        select(NotificationRecipient).where(
                            NotificationRecipient.membership_id.in_(memberships)
                        )
                    )
                ).scalars()
            )
            assert len(recipients) == count
            assert sum(recipient.phone != "deleted" for recipient in recipients) == remaining
            for recipient in recipients:
                flags = (
                    recipient.daily_digest,
                    recipient.screening_flags,
                    recipient.kidding_watch,
                    recipient.overdue_critical,
                    recipient.feed_reorder,
                    recipient.movement_restriction,
                    recipient.verified,
                )
                assert all(flags) if recipient.phone != "deleted" else not any(flags)
            tombstones = list((await db.execute(select(User).where(User.id.in_(users)))).scalars())
            assert len(tombstones) == count and all(
                user.deleted_at is not None for user in tombstones
            )
            live = await db.get(FarmMembership, live_membership)
            assert live is not None and live.is_active
        assert await _credential_state(live_membership) == live_credentials
        assert (await client.get(live_endpoint, headers=owner)).json() == live_preferences
        assert (await client.get("/api/auth/me", headers=live_headers)).status_code == 200
        assert (await client.get("/api/team", headers=owner)).status_code == 200

    return observe


async def _drive(
    kind: CleanupKind,
    count: int,
    max_batches: int,
    observe: Observe,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_once: bool = False,
) -> None:
    batch_size = 2
    work = min(count, batch_size * max_batches)
    expected = [batch_size] * (work // batch_size)
    if work % batch_size:
        expected.append(work % batch_size)
    real_cleanup = (
        purge_expired_refresh_sessions if kind == "refresh" else deactivate_deleted_user_memberships
    )
    attribute = (
        "purge_expired_refresh_sessions"
        if kind == "refresh"
        else "deactivate_deleted_user_memberships"
    )
    loop = (
        main._refresh_session_cleanup_loop
        if kind == "refresh"
        else main._deleted_membership_cleanup_loop
    )
    settings = get_settings()
    interval = (
        settings.refresh_session_cleanup_interval_seconds
        if kind == "refresh"
        else settings.deleted_membership_cleanup_interval_seconds
    )
    batches: list[int] = []
    failed: list[str] = []
    attempts = 0
    pauses = 0
    resumed = False
    holder = get_sessionmaker()()

    async def cleanup(db: AsyncSession, *, batch_size: int) -> int:
        nonlocal attempts
        attempts += 1
        if batches:
            # The previous page must be visible outside its transaction
            # before the worker starts another page in this same interval.
            await observe(count - sum(batches))
        if fail_once and attempts == 1:
            await db.execute(text("SET LOCAL lock_timeout = '100ms'"))
        try:
            removed = await real_cleanup(db, batch_size=batch_size)
        except DBAPIError as exc:
            code = getattr(exc.orig, "sqlstate", None)
            assert isinstance(code, str)
            failed.append(code)
            raise
        batches.append(removed)
        return removed

    async def pause(_delay: float) -> None:
        nonlocal pauses, resumed
        pauses += 1
        if kind == "refresh" and pauses == 1:
            return  # refresh retention waits for its first interval
        if fail_once and not resumed:
            assert failed == ["55P03"], (
                "The owned table lock must cause a real PostgreSQL lock error"
            )
            await holder.rollback()
            resumed = True
            return
        await observe(count - work)
        raise asyncio.CancelledError

    monkeypatch.setattr(main, attribute, cleanup)
    monkeypatch.setattr(
        main, "asyncio", SimpleNamespace(sleep=pause, CancelledError=asyncio.CancelledError)
    )
    try:
        if fail_once:
            table = "refresh_sessions" if kind == "refresh" else "farm_memberships"
            await holder.execute(text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE"))
        with pytest.raises(asyncio.CancelledError):
            await loop(interval, batch_size, max_batches)
    finally:
        await holder.rollback()
        await holder.close()
    assert batches == expected, (
        "Drain available pages, stop on a short page and respect the tick budget"
    )
    assert failed == (["55P03"] if fail_once else [])
    if fail_once:
        assert resumed and attempts == len(expected) + 1


@pytest.mark.parametrize("kind", ["refresh", "deleted-user"])
@pytest.mark.parametrize(
    ("count", "max_batches"), [(5, 5), (7, 2)], ids=["full-then-short", "finite-tick-budget"]
)
async def test_maintenance_commits_available_pages_before_yielding_its_interval(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    kind: CleanupKind,
    count: int,
    max_batches: int,
) -> None:
    observe = await (
        _refresh_rows(client, count) if kind == "refresh" else _deleted_workers(client, count)
    )
    await observe(count)
    await _drive(kind, count, max_batches, observe, monkeypatch)


@pytest.mark.parametrize("kind", ["refresh", "deleted-user"])
async def test_maintenance_retries_after_a_real_transient_database_lock_failure(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    kind: CleanupKind,
) -> None:
    observe = await (_refresh_rows(client, 1) if kind == "refresh" else _deleted_workers(client, 1))
    await _drive(kind, 1, 2, observe, monkeypatch, fail_once=True)
