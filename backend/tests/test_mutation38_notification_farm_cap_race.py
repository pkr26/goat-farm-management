"""Concurrent delivery claims conserve one farm's actual paid-send allowance."""

import asyncio
from datetime import date
from typing import cast

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import NotificationLog
from app.services.notifications import send_notification, service
from app.services.notifications.service import SendOutcome

from .test_notifications import RecordingProvider, _farm, _membership_id, _recipient, midday
from .test_screening_clinical_integrity import _finding


async def test_two_real_recipient_claims_cannot_exceed_one_farm_paid_send(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "notifications_enabled", False)
    owner, finding_id = await _finding(client)
    reviewed = await client.post(
        f"/api/screening/findings/{finding_id}/review",
        headers=owner,
        json={
            "status": "CONFIRMED",
            "expected_status": "PENDING_REVIEW",
            "expected_revision": 0,
            "review_note": "A committed clinical fact for the two real recipient deliveries",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    farm_id = int(owner["X-Farm-Id"])
    memberships = [await _membership_id(client, owner), await _membership_id(client, owner)]
    async with get_sessionmaker()() as setup:
        recipients = [
            await _recipient(setup, farm_id, memberships[0], phone="+919111111111"),
            await _recipient(setup, farm_id, memberships[1], phone="+919222222222"),
        ]
        recipient_ids = [recipient.id for recipient in recipients]

    settings = Settings(
        environment="development",
        notifications_enabled=True,
        notifications_farm_daily_cap=1,
        notifications_delivery_concurrency=2,
        notifications_per_farm_delivery_concurrency=2,
    )
    provider = RecordingProvider()
    first_counted = asyncio.Event()
    second_counted = asyncio.Event()
    release_first = asyncio.Event()
    actual_counts: list[int] = []
    actual_first_pid: list[int] = []
    real_count = service._farm_send_count_today

    async def count_then_schedule(
        db: AsyncSession, counted_farm: int, local_date: date, excluding_id: int
    ) -> int:
        count = await real_count(db, counted_farm, local_date, excluding_id)
        actual_counts.append(count)
        if len(actual_counts) == 1:
            actual_first_pid.append(int(await db.scalar(text("SELECT pg_backend_pid()"))))
            first_counted.set()
            await release_first.wait()
        else:
            second_counted.set()
        return count

    monkeypatch.setattr(service, "_farm_send_count_today", count_then_schedule)
    requests: list[asyncio.Task[SendOutcome]] = []
    observed_dependency = False
    async with (
        get_sessionmaker()() as first_db,
        get_sessionmaker()() as second_db,
        get_sessionmaker()() as observer,
    ):
        first_farm = await _farm(first_db, farm_id)
        first_recipient = await first_db.get(type(recipients[0]), recipient_ids[0])
        second_farm = await _farm(second_db, farm_id)
        second_recipient = await second_db.get(type(recipients[1]), recipient_ids[1])
        assert first_recipient is not None and second_recipient is not None
        second_pid = int(await second_db.scalar(text("SELECT pg_backend_pid()")))
        fact = f"finding:{finding_id}:review:1:CONFIRMED"
        try:
            requests.append(
                asyncio.create_task(
                    send_notification(
                        first_db,
                        settings,
                        provider,
                        farm=first_farm,
                        recipient=first_recipient,
                        alert_class="SCREENING_FLAG",
                        message=f"Actual confirmed clinical finding #{finding_id}",
                        payload=fact,
                        now_local=midday(first_farm),
                    )
                )
            )
            async with asyncio.timeout(15):
                await first_counted.wait()
            requests.append(
                asyncio.create_task(
                    send_notification(
                        second_db,
                        settings,
                        provider,
                        farm=second_farm,
                        recipient=second_recipient,
                        alert_class="SCREENING_FLAG",
                        message=f"Actual confirmed clinical finding #{finding_id}",
                        payload=fact,
                        now_local=midday(second_farm),
                    )
                )
            )
            # The release is driven by either a genuine PostgreSQL dependency
            # or the second actual COUNT, rather than elapsed time being a kill.
            async with asyncio.timeout(15):
                while not second_counted.is_set():
                    blockers = cast(
                        list[int],
                        await observer.scalar(
                            text("SELECT pg_blocking_pids(:pid)").bindparams(pid=second_pid)
                        ),
                    )
                    if actual_first_pid[0] in blockers:
                        observed_dependency = True
                        break
                    await asyncio.sleep(0.01)
        finally:
            release_first.set()
            outcomes = await asyncio.gather(*requests)

    assert observed_dependency or second_counted.is_set()
    assert len(provider.sent) == 1, "One farm's cap permits exactly one paid recipient delivery"
    assert sorted(outcome.status for outcome in outcomes) == ["SENT", "SKIPPED_CAP"]
    assert all(outcome.fresh for outcome in outcomes)
    assert actual_counts == [0, 1]
    async with get_sessionmaker()() as db:
        logs = list((await db.execute(select(NotificationLog))).scalars())
        assert len(logs) == 2
        assert {log.recipient_id for log in logs} == set(recipient_ids)
        assert sorted(log.status for log in logs) == ["SENT", "SKIPPED_CAP"]
