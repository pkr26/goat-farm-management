"""Real durable delivery claims conserve replay identity and committed settlement."""

import asyncio
from datetime import datetime
from typing import ClassVar

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import Farm, NotificationLog, NotificationRecipient
from app.services.notifications import send_notification, service
from app.services.notifications.providers import DeliveryResult, NotificationProvider

from .conftest import owner_with_farm
from .test_notifications import RecordingProvider, _farm, _membership_id, _recipient, midday


async def _supported_delivery(
    db: AsyncSession,
    settings: Settings,
    provider: NotificationProvider,
    farm: Farm,
    recipient: NotificationRecipient,
    payload: str,
    at: datetime,
) -> service.SendOutcome:
    try:
        return await send_notification(
            db,
            settings,
            provider,
            farm=farm,
            recipient=recipient,
            alert_class="SCREENING_FLAG",
            message="Actual retained screening notification",
            payload=payload,
            now_local=at,
        )
    except (NoResultFound, MultipleResultsFound, TypeError) as exc:
        pytest.fail(f"A valid delivery claim must resolve its own typed receipt: {exc}")
    except ValueError as exc:
        if str(exc) != "Unknown alert class 'SCREENING_FLAG'":
            raise
        pytest.fail(f"The declared screening alert class must admit its valid delivery: {exc}")
    except RuntimeError as exc:
        if str(exc) != "claimed notification row vanished before settle":
            raise
        pytest.fail(f"A real committed and undeleted delivery claim cannot vanish: {exc}")


@pytest.mark.parametrize("quiet", [False, True], ids=["paid", "quiet-placeholder"])
async def test_real_distinct_facts_do_not_alias_when_the_first_claim_is_replayed(
    client: httpx.AsyncClient,
    quiet: bool,
) -> None:
    owner = await owner_with_farm(client, email="distinct-claim-facts@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(environment="development", notifications_enabled=True)
    provider = RecordingProvider()
    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        now = midday(farm).replace(hour=22 if quiet else 10)
        first = await _supported_delivery(db, settings, provider, farm, recipient, "fact:41", now)
        second = await _supported_delivery(db, settings, provider, farm, recipient, "fact:42", now)
        replay = await _supported_delivery(db, settings, provider, farm, recipient, "fact:41", now)
        expected = "SKIPPED_QUIET" if quiet else "SENT"
        assert first.status == second.status == replay.status == expected
        assert first.fresh and second.fresh and not replay.fresh
        assert len(provider.sent) == (0 if quiet else 2)
        rows = list((await db.execute(select(NotificationLog))).scalars())
        assert len(rows) == 2
        assert {row.status for row in rows} == {expected}
        assert len({row.payload_hash for row in rows}) == 2
        assert all(row.outbox_id is None for row in rows)


async def test_cached_loser_observes_the_actual_committed_provider_settlement(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="cached-claim-settlement@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(environment="development", notifications_enabled=True)
    entered, release = asyncio.Event(), asyncio.Event()

    class ActualProvider:
        name = "controlled"

        async def send_sms(self, phone: str, message: str) -> DeliveryResult:
            entered.set()
            await release.wait()
            return DeliveryResult(ok=True, message_id="actual-paid-claim")

    async with get_sessionmaker()() as setup:
        farm = await _farm(setup, farm_id)
        recipient = await _recipient(setup, farm_id, membership_id)
        at = midday(farm)
        recipient_id = recipient.id

    async def deliver() -> service.SendOutcome:
        async with get_sessionmaker()() as sender_db:
            sender_farm = await _farm(sender_db, farm_id)
            sender_recipient = await sender_db.get(NotificationRecipient, recipient_id)
            assert sender_recipient is not None
            return await _supported_delivery(
                sender_db,
                settings,
                ActualProvider(),
                sender_farm,
                sender_recipient,
                "cached-fact:41",
                at,
            )

    sender = asyncio.create_task(deliver())
    try:
        await asyncio.wait_for(entered.wait(), timeout=15)
        async with get_sessionmaker()() as loser:
            cached = (await loser.execute(select(NotificationLog))).scalar_one()
            assert cached.status == "SENDING"
            log_id = cached.id
            release.set()
            outcome = await sender
            assert outcome.status == "SENT" and outcome.fresh
            async with get_sessionmaker()() as factual:
                committed = await factual.get(NotificationLog, log_id)
                assert committed is not None and committed.status == "SENT"
                assert committed.provider_message_id == "actual-paid-claim"
            assert cached.status == "SENDING", (
                "The genuine identity map still holds its earlier read"
            )

            class Clock:
                now = 0.0

                class Loop:
                    @staticmethod
                    def time() -> float:
                        return Clock.now

                @staticmethod
                def get_running_loop() -> "Clock.Loop":
                    return Clock.Loop()

                @staticmethod
                async def sleep(seconds: float) -> None:
                    Clock.now += 1.0
                    await asyncio.sleep(0)

            monkeypatch.setattr(service, "asyncio", Clock)
            try:
                observed = await service._wait_for_claim_to_settle(loser, log_id, settings)
            except (NoResultFound, MultipleResultsFound) as exc:
                pytest.fail(
                    f"The real settled claim must remain retrievable by its own identity: {exc}"
                )
            assert observed == "SENT"
            assert cached.status == "SENT" and cached.provider_message_id == "actual-paid-claim"
            assert Clock.now == 0
    finally:
        release.set()
        await asyncio.gather(sender, return_exceptions=True)


async def test_real_interrupted_claim_wait_stops_at_the_exact_stated_slack_boundary(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="interrupted-claim-budget@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(
        environment="development",
        notifications_enabled=True,
        notifications_send_retry_attempts=1,
        notifications_send_retry_backoff_seconds=0,
    )
    entered = asyncio.Event()
    interrupted = asyncio.Event()

    class InterruptedProvider:
        name = "interrupted"

        async def send_sms(self, phone: str, message: str) -> DeliveryResult:
            entered.set()
            await interrupted.wait()
            return DeliveryResult(ok=True, message_id="never-accepted")

    async with get_sessionmaker()() as setup:
        farm = await _farm(setup, farm_id)
        recipient = await _recipient(setup, farm_id, membership_id)
        at = midday(farm)
        recipient_id = recipient.id

    async def deliver() -> service.SendOutcome:
        async with get_sessionmaker()() as sender_db:
            sender_farm = await _farm(sender_db, farm_id)
            sender_recipient = await sender_db.get(NotificationRecipient, recipient_id)
            assert sender_recipient is not None
            return await _supported_delivery(
                sender_db,
                settings,
                InterruptedProvider(),
                sender_farm,
                sender_recipient,
                "actual-interruption:41",
                at,
            )

    sender = asyncio.create_task(deliver())
    try:
        await asyncio.wait_for(entered.wait(), timeout=15)
        # This is the real cancellation policy: the provider attempt has begun,
        # the durable SENDING claim already committed, and no accepted result
        # is invented. A retry must conserve that possibly paid opportunity.
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        async with get_sessionmaker()() as db:
            actual = (await db.execute(select(NotificationLog))).scalar_one()
            assert actual.status == "SENDING" and actual.provider_message_id is None

            class Clock:
                now = 0.0
                sleeps: ClassVar[list[float]] = []

                class Loop:
                    @staticmethod
                    def time() -> float:
                        return Clock.now

                @staticmethod
                def get_running_loop() -> "Clock.Loop":
                    return Clock.Loop()

                @staticmethod
                async def sleep(seconds: float) -> None:
                    Clock.sleeps.append(seconds)
                    Clock.now += 1.0
                    await asyncio.sleep(0)

            monkeypatch.setattr(service, "asyncio", Clock)
            try:
                observed = await service._wait_for_claim_to_settle(db, actual.id, settings)
            except (NoResultFound, MultipleResultsFound) as exc:
                pytest.fail(
                    f"A genuine interrupted claim must conserve its durable identity: {exc}"
                )
            assert observed == "SENDING"
            assert Clock.now == 5.0 and Clock.sleeps == [0.1] * 5
    finally:
        interrupted.set()
        if not sender.done():
            sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
