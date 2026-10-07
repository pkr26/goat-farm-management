"""Current native notification callers report actual deliveries and exclude tombstones."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import Farm, FarmMembership, FeedInventory, NotificationLog, User
from app.services.notifications import (
    farms_ready_for_digest,
    feed_reorder_daily,
    kidding_watch_daily,
    overdue_critical_sweep,
    run_digest_for_farm,
)
from app.services.notifications.providers import NotificationProvider

from .conftest import owner_with_farm, provisioned_worker_login
from .test_feeding_extended import stock_all
from .test_notifications import RecordingProvider, _farm, _membership_id, _recipient
from .type_helpers import json_int


def _always_open_settings() -> Settings:
    return Settings(
        environment="development",
        notifications_enabled=True,
        notifications_quiet_start_hour=0,
        notifications_quiet_end_hour=0,
        notifications_digest_hour=0,
        notifications_digest_minute=0,
    )


async def test_production_digest_caller_uses_default_farm_clock_and_settles_one_delivery(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-digest-default-clock@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    provider = RecordingProvider()
    async with get_sessionmaker()() as setup:
        recipient = await _recipient(setup, farm_id, membership_id)
        recipient_id = recipient.id
    async with get_sessionmaker()() as worker:
        farm = await _farm(worker, farm_id)
        try:
            # main._notifications_loop uses this exact omitted-clock call.
            summary = await run_digest_for_farm(worker, _always_open_settings(), provider, farm)
        except AttributeError as exc:
            pytest.fail(f"The production default-clock digest must complete a valid farm: {exc}")
    assert (summary.farm_id, summary.sent, summary.skipped) == (farm_id, 1, 0)
    assert len(provider.sent) == 1
    async with get_sessionmaker()() as observer:
        log = (await observer.execute(select(NotificationLog))).scalar_one()
        assert log.farm_id == farm_id and log.recipient_id == recipient_id
        assert log.alert_class == "DAILY_DIGEST" and log.status == "SENT"
        assert provider.sent[0][1].startswith(f"Herdly {log.local_date.isoformat()}:")


@pytest.mark.parametrize(
    "scanner",
    [kidding_watch_daily, feed_reorder_daily, overdue_critical_sweep],
    ids=["empty-kidding-watch", "empty-feed-reorder", "empty-critical-overdue"],
)
async def test_empty_native_alert_scan_reports_zero_actual_deliveries(
    client: httpx.AsyncClient,
    scanner: Callable[[AsyncSession, Settings, NotificationProvider, Farm], Awaitable[int]],
) -> None:
    owner = await owner_with_farm(client, email="native-empty-alert-scan@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    provider = RecordingProvider()
    if scanner is feed_reorder_daily:
        # A new farm has genuine preset reorder alerts. Use the supported
        # purchase API to leave every seeded ingredient adequately stocked.
        await stock_all(client, owner, 5000.0)
    async with get_sessionmaker()() as worker:
        await _recipient(worker, farm_id, membership_id)
        farm = await _farm(worker, farm_id)
        if scanner is feed_reorder_daily:
            inventory = list((await worker.execute(select(FeedInventory))).scalars())
            assert inventory and all(
                item.reorder_level is None or item.qty_on_hand >= item.reorder_level
                for item in inventory
            )
        sent = await scanner(worker, _always_open_settings(), provider, farm)
    assert sent == 0, "a farm with no alert-worthy facts must report zero delivered messages"
    assert not provider.sent
    async with get_sessionmaker()() as observer:
        assert not list((await observer.execute(select(NotificationLog))).scalars())


async def test_digest_readiness_respects_public_account_tombstone_before_membership_cleanup(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-digest-tombstone-owner@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    role = await client.post(
        "/api/team/roles",
        json={"name": "Digest tombstone worker", "permissions": ["dashboard.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    initial_password = "native-digest-tombstone-password"
    worker_email = "native-digest-tombstone-worker@farm.in"
    created = await client.post(
        "/api/team/workers",
        json={
            "name": "Native digest departing worker",
            "email": worker_email,
            "password": initial_password,
            "role_id": json_int(role.json()["id"]),
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    membership_id = json_int(created.json()["id"])
    worker_headers, user_id = await provisioned_worker_login(client, worker_email, initial_password)
    async with get_sessionmaker()() as setup:
        await _recipient(setup, farm_id, membership_id)
    settings = _always_open_settings()
    async with get_sessionmaker()() as observer:
        ready = await farms_ready_for_digest(observer, settings, datetime.now(UTC))
        assert farm_id in [farm.id for farm in ready]
    deleted = await client.request(
        "DELETE",
        "/api/auth/account",
        json={"current_password": initial_password + "!r1"},
        headers=worker_headers,
    )
    assert deleted.status_code == 204, deleted.text
    async with get_sessionmaker()() as observer:
        user = await observer.get(User, user_id)
        membership = await observer.get(FarmMembership, membership_id)
        assert user is not None and user.deleted_at is not None
        assert membership is not None and membership.is_active
        ready = await farms_ready_for_digest(observer, settings, datetime.now(UTC))
        assert farm_id not in [farm.id for farm in ready], (
            "the synchronous account tombstone must exclude digest work before async cleanup"
        )
