"""Mutation-campaign gap tests for the notifications service (2026-09-30).

The campaign's surviving mutants here were all in unasserted behavior
boundaries, not dead code: the retry loop's attempt budget and log format,
the claim-settle poll's deadline arithmetic, the digest fan-out continuing
past an inactive recipient, the feed-reorder message's name truncation, and
the overdue-critical sweep's 3-day threshold. Each test below pins one of
those exactly.
"""

import logging
from datetime import timedelta
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import FeedInventory, NotificationLog, Task
from app.services.notifications import (
    feed_reorder_daily,
    overdue_critical_sweep,
    run_digest_for_farm,
)
from app.services.notifications.providers import DeliveryResult, NotificationDeliveryError
from app.services.notifications.service import _send_with_retry
from app.utils import today

from .conftest import owner_with_farm
from .test_notifications import RecordingProvider, _farm, _membership_id, _recipient, midday


def _notif_settings(**overrides: Any) -> Settings:
    return Settings(environment="development", notifications_enabled=True, **overrides)


class FlakyProvider:
    """Transport that raises for its first ``fail`` calls, then succeeds."""

    name = "flaky"

    def __init__(self, fail: int) -> None:
        self.fail = fail
        self.calls = 0

    async def send_sms(self, phone: str, message: str) -> DeliveryResult:
        self.calls += 1
        if self.calls <= self.fail:
            raise NotificationDeliveryError("gateway blip")
        return DeliveryResult(ok=True, message_id=f"flaky-{self.calls}")


async def test_send_with_retry_budget_is_exactly_configured_attempts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # notifications_send_retry_attempts is the TOTAL attempt budget (initial
    # try included): 2 → exactly one transport failure is absorbed by retry.
    settings = _notif_settings(notifications_send_retry_attempts=2,
        notifications_send_retry_backoff_seconds=0.0,
    )
    provider = FlakyProvider(fail=1)
    with caplog.at_level(logging.WARNING, logger="app.services.notifications.service"):
        result = await _send_with_retry(provider, "+919999999999", "m", settings)
    assert result.ok
    assert provider.calls == 2  # one failure + one retry, exactly
    assert "(attempt 1/2), retrying" in caplog.text


async def test_send_with_retry_exhaustion_reports_final_attempt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _notif_settings(notifications_send_retry_attempts=3,
        notifications_send_retry_backoff_seconds=0.0,
    )
    provider = FlakyProvider(fail=99)
    logger_name = "app.services.notifications.service"
    with caplog.at_level(logging.WARNING, logger=logger_name), pytest.raises(
        NotificationDeliveryError
    ):
        await _send_with_retry(provider, "+919999999999", "m", settings)
    assert provider.calls == 3  # initial + 2 retries
    # Only the attempts that will be retried log; the last failure raises.
    assert "(attempt 1/3), retrying" in caplog.text
    assert "(attempt 2/3), retrying" in caplog.text
    assert "attempt 3/3" not in caplog.text


async def test_send_with_retry_budget_cannot_shrink_below_one_attempt() -> None:
    # notifications_send_retry_attempts is ge=1 at the settings layer; the
    # max(1, ...) clamp still pins that a transport failure always gets its
    # one real attempt (a max(2, ...) drift would silently add retries).
    settings = _notif_settings(notifications_send_retry_attempts=1,
        notifications_send_retry_backoff_seconds=0.0,
    )
    provider = FlakyProvider(fail=0)  # succeeds immediately
    result = await _send_with_retry(provider, "+919999999999", "m", settings)
    assert result.ok and provider.calls == 1


async def test_wait_for_claim_deadline_budget(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dedupe loser polls for at most 5s + attempts*(attempts+1)*backoff.

    Drives a fake loop clock through the service's own asyncio seam so the
    exact budget is asserted (retry_attempts=1, backoff=2.0 → 5 + 1*2*2 = 9s
    of polling before giving up on a row stuck in SENDING).
    """
    import app.services.notifications.service as service

    class FakeAsyncio:
        now = 1000.0

        @classmethod
        def get_running_loop(cls) -> object:
            class Loop:
                time = staticmethod(lambda: cls.now)

            return Loop()

        @staticmethod
        async def sleep(seconds: float) -> None:
            # Each poll's 0.1s sleep advances the fake clock by the interval.
            FakeAsyncio.now += seconds

    owner = await owner_with_farm(client, email="claim-deadline@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)

    async with get_sessionmaker()() as db:
        recipient = await _recipient(db, farm_id, membership_id)
        log = NotificationLog(
            farm_id=farm_id,
            recipient_id=recipient.id,
            alert_class="DAILY_DIGEST",
            payload_hash="deadbeef01",
            local_date=today(),
            status="SENDING",
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        monkeypatch.setattr(service, "asyncio", FakeAsyncio)
        settings = _notif_settings(notifications_send_retry_attempts=1,
            notifications_send_retry_backoff_seconds=2.0,
        )
        status = await service._wait_for_claim_to_settle(db, log.id, settings)

    assert status == "SENDING"
    # The poll budget is exactly 5.0 + attempts*(attempts+1)*backoff seconds;
    # the loop exits within one 0.1s poll step of that deadline.
    assert 9.0 <= FakeAsyncio.now - 1000.0 < 9.2


async def test_overdue_critical_threshold_is_three_days(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="overdue-crit@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = _notif_settings()
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        await _recipient(db, farm_id, membership_id)
        exactly_three = today() - timedelta(days=3)
        only_two = today() - timedelta(days=2)
        db.add_all(
            [
                Task(farm_id=farm_id, title="deep overdue", due_date=exactly_three,
                     category="OTHER", status="PENDING"),
                Task(farm_id=farm_id, title="shallow overdue", due_date=only_two,
                     category="OTHER", status="PENDING"),
            ]
        )
        await db.commit()
        sent = await overdue_critical_sweep(db, settings, provider, farm, now_local=midday(farm))

    assert sent == 1
    message = provider.sent[0][1]
    # Exactly the 3+ day pile: the 2-day task is not critical and not counted.
    assert message.startswith("Herdly: 1 duties are 3+ days overdue")
    assert exactly_three.isoformat() in message
    assert "shallow" not in message

    # And with only sub-threshold overdue work there is no alert at all.
    provider2 = RecordingProvider()
    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        db.add(
            Task(farm_id=farm_id, title="another two-day", due_date=only_two,
                 category="OTHER", status="PENDING")
        )
        await db.commit()
        sent2 = await overdue_critical_sweep(db, _notif_settings(), provider2, farm)
    assert sent2 == 0
    assert provider2.sent == []


def _expected_reorder_message(db_rows: list[tuple[Any, ...]], total: int) -> str:
    names = ", ".join(str(row[0]) for row in db_rows[:5])
    return (
        f"Herdly: {total} feed items below reorder level "
        f"({names}{'…' if total > 5 else ''}). Order feed."
    )


async def _below_reorder_rows(farm_id: int) -> list[tuple[Any, ...]]:
    from sqlalchemy import select as sa_select

    from app.models import FeedInventory as FI

    async with get_sessionmaker()() as db:
        rows = (
            await db.execute(
                sa_select(FI.ingredient)
                .where(
                    FI.farm_id == farm_id,
                    FI.reorder_level.is_not(None),
                    FI.qty_on_hand < FI.reorder_level,
                )
                .order_by(FI.ingredient)
                .limit(10)
            )
        ).all()
        return [(row[0],) for row in rows]


async def _seed_depleted(db: Any, farm_id: int, count: int, prefix: str) -> None:
    from sqlalchemy import delete as sa_delete

    # Drop the farm's starter stock rows so the below-reorder total is
    # exactly what this test inserts (truncation/ellipsis boundaries).
    await db.execute(sa_delete(FeedInventory).where(FeedInventory.farm_id == farm_id))
    for i in range(1, count + 1):
        db.add(
            FeedInventory(
                farm_id=farm_id,
                ingredient=f"{prefix}-{i:02d}",
                category="CONCENTRATE",
                qty_on_hand=1.0,
                reorder_level=10.0,
            )
        )
    await db.commit()


async def test_feed_reorder_message_lists_five_names_then_ellipsis(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="feed-reorder-trunc@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = _notif_settings()
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        await _recipient(db, farm_id, membership_id)
        await _seed_depleted(db, farm_id, 6, "feed")
        await db.commit()
        sent = await feed_reorder_daily(db, settings, provider, farm, now_local=midday(farm))

    rows = await _below_reorder_rows(farm_id)
    assert len(rows) == 6
    assert sent == 1
    assert provider.sent[0][1] == _expected_reorder_message(rows, 6)
    assert provider.sent[0][1] == (
        "Herdly: 6 feed items below reorder level "
        "(feed-01, feed-02, feed-03, feed-04, feed-05…). Order feed."
    )


async def test_feed_reorder_message_five_names_no_ellipsis(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="feed-reorder-five@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = _notif_settings()
    provider = RecordingProvider()

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        await _recipient(db, farm_id, membership_id)
        await _seed_depleted(db, farm_id, 5, "feed")
        await db.commit()
        sent = await feed_reorder_daily(db, settings, provider, farm, now_local=midday(farm))

    rows = await _below_reorder_rows(farm_id)
    assert len(rows) == 5, "exactly five: every name shown, no ellipsis"
    assert sent == 1
    assert provider.sent[0][1] == _expected_reorder_message(rows, 5)
    assert provider.sent[0][1] == (
        "Herdly: 5 feed items below reorder level "
        "(feed-01, feed-02, feed-03, feed-04, feed-05). Order feed."
    )


async def test_digest_continues_past_inactive_recipient(client: httpx.AsyncClient) -> None:
    """An inactive membership skips ITS recipient; the fan-out continues.

    `continue` mutated to `break` would silently end the digest loop at the
    first deactivated worker, starving every later recipient.
    """
    owner = await owner_with_farm(client, email="digest-continue@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    settings = _notif_settings()
    provider = RecordingProvider()

    inactive_membership = await _membership_id(client, owner)
    deactivated = await client.put(
        f"/api/team/workers/{inactive_membership}/status",
        json={"is_active": False},
        headers=owner,
    )
    assert deactivated.status_code == 200, deactivated.text
    active_membership = await _membership_id(client, owner)

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        # Lower id first: the inactive recipient is iterated before the active one.
        await _recipient(db, farm_id, inactive_membership, phone="+919444444401")
        await _recipient(db, farm_id, active_membership, phone="+919444444402")
        await db.commit()
        summary = await run_digest_for_farm(db, settings, provider, farm, now_local=midday(farm))

    assert (summary.sent, summary.skipped) == (1, 1)
    assert len(provider.sent) == 1
    assert provider.sent[0][0] == "+919444444402"
