"""A recipient deleted during real provider backpressure is skipped safely."""

import asyncio
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import Farm, NotificationLog, NotificationRecipient
from app.services.notifications import notify_alert_class, run_digest_for_farm
from app.services.notifications.providers import DeliveryResult

from .conftest import owner_with_farm
from .test_notifications import _farm, _membership_id, _recipient


class PausedProvider:
    name = "queued-recipient-contract"

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.phones: list[str] = []

    async def send_sms(self, phone: str, message: str) -> DeliveryResult:
        self.phones.append(phone)
        self.entered.set()
        await self.release.wait()
        return DeliveryResult(ok=True, message_id="native-queued-first-send")


@pytest.mark.parametrize("kind", ["digest", "alert"])
async def test_native_fanout_skips_a_recipient_deleted_while_waiting_for_a_delivery_slot(
    client: httpx.AsyncClient, kind: Literal["digest", "alert"]
) -> None:
    owner = await owner_with_farm(client, email=f"deleted-queued-{kind}@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    memberships = [await _membership_id(client, owner) for _ in range(2)]
    settings = Settings(
        environment="development",
        notifications_enabled=True,
        notifications_delivery_concurrency=1,
        notifications_per_farm_delivery_concurrency=1,
        notifications_quiet_start_hour=23,
        notifications_quiet_end_hour=0,
    )
    provider = PausedProvider()
    async with get_sessionmaker()() as worker:
        recipients = [
            await _recipient(worker, farm_id, memberships[0], phone="+919111111111"),
            await _recipient(worker, farm_id, memberships[1], phone="+919222222222"),
        ]
        ids_by_phone = {row.phone: row.id for row in recipients}
        farm = await _farm(worker, farm_id)
        now = datetime(2051, 1, 12, 10, tzinfo=ZoneInfo(farm.timezone))

        async def run_fanout() -> int:
            if kind == "digest":
                return (
                    await run_digest_for_farm(worker, settings, provider, farm, now_local=now)
                ).sent
            return await notify_alert_class(
                worker,
                settings,
                provider,
                farm=farm,
                alert_class="SCREENING_FLAG",
                message="Review the confirmed screening finding.",
                payload="native-deleted-queued-recipient",
                now_local=now,
            )

        pending = asyncio.create_task(run_fanout())
        try:
            await asyncio.wait_for(provider.entered.wait(), timeout=20)
            assert len(provider.phones) == 1
            first_phone = provider.phones[0]
            # Observe which native recipient acquired the first slot; the
            # contract does not depend on a database row ordering promise.
            deleted_id = next(mid for phone, mid in ids_by_phone.items() if phone != first_phone)
            async with get_sessionmaker()() as editor:
                await editor.execute(
                    delete(NotificationRecipient).where(NotificationRecipient.id == deleted_id)
                )
                await editor.commit()
                assert await editor.get(NotificationRecipient, deleted_id) is None
                assert await editor.get(Farm, farm_id) is not None
            provider.release.set()
            try:
                delivered = await asyncio.wait_for(pending, timeout=20)
            except (AttributeError, LookupError, TypeError, ValueError) as error:
                pytest.fail(
                    f"Deleting a queued recipient must preserve the completed fanout: {error}"
                )
            assert delivered == 1 and provider.phones == [first_phone]
        finally:
            provider.release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    async with get_sessionmaker()() as observer:
        logs = list(
            (
                await observer.execute(
                    select(NotificationLog).where(NotificationLog.farm_id == farm_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(logs) == 1 and logs[0].status == "SENT"
        assert logs[0].recipient_id == ids_by_phone[first_phone]
