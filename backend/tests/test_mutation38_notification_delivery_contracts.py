"""Recipient delivery conserves retry pacing, admission and durable claim facts."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound, NoResultFound

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import FarmMembership, NotificationLog, Task
from app.services.notifications import send_notification, service
from app.services.notifications.providers import DeliveryResult, NotificationDeliveryError
from app.utils import today

from .conftest import owner_with_farm
from .test_notifications import RecordingProvider, _farm, _membership_id, _recipient, midday
from .test_notifications_mutation_gaps import FlakyProvider


async def test_actual_retry_pacing_increases_after_each_proven_pre_send_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="development",
        notifications_enabled=True,
        notifications_send_retry_attempts=3,
        notifications_send_retry_backoff_seconds=0.25,
    )
    provider = FlakyProvider(fail=2)
    sleeps: list[float] = []

    class RetryTimer:
        @staticmethod
        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            await asyncio.sleep(0)

    monkeypatch.setattr(service, "asyncio", RetryTimer)
    try:
        result = await service._send_with_retry(provider, "+919999999999", "Real retry", settings)
    except NotificationDeliveryError as exc:
        pytest.fail(f"Two safe pre-send failures must fit the three-attempt budget: {exc}")
    assert result.ok and provider.calls == 3
    assert sleeps == [0.25, 0.5]


async def test_one_attempt_budget_does_not_repeat_an_exhausted_safe_failure() -> None:
    settings = Settings(
        environment="development",
        notifications_enabled=True,
        notifications_send_retry_attempts=1,
        notifications_send_retry_backoff_seconds=0,
    )
    provider = FlakyProvider(fail=99)
    with pytest.raises(NotificationDeliveryError):
        await service._send_with_retry(provider, "+919999999999", "One paid opportunity", settings)
    assert provider.calls == 1


async def test_delivery_admission_conserves_separate_global_and_farm_allowances() -> None:
    settings = Settings(
        environment="development",
        notifications_enabled=True,
        notifications_delivery_concurrency=3,
        notifications_per_farm_delivery_concurrency=1,
    )
    release = asyncio.Event()
    requests: list[asyncio.Task[None]] = []

    async def attempt(farm_id: int, started: asyncio.Event, admitted: asyncio.Event) -> None:
        started.set()
        async with service.delivery_slot(settings, farm_id):
            admitted.set()
            await release.wait()

    other_started, same_started = asyncio.Event(), asyncio.Event()
    other_admitted, same_admitted = asyncio.Event(), asyncio.Event()
    try:
        try:
            async with service.delivery_slot(settings, 11):
                requests.append(asyncio.create_task(attempt(12, other_started, other_admitted)))
                requests.append(asyncio.create_task(attempt(11, same_started, same_admitted)))
                # Each child signals before its genuine semaphore acquisition.
                # Its parent resumes only after that child either admits or
                # suspends on the occupied allowance: no elapsed-time verdict.
                await other_started.wait()
                await same_started.wait()
                assert other_admitted.is_set(), "Another farm must use the free global allowance"
                assert not same_admitted.is_set(), "A farm cannot consume a second local allowance"
        except (IndexError, AttributeError) as exc:
            pytest.fail(
                f"Valid delivery limits must admit ordinary farms without adapter errors: {exc}"
            )
    finally:
        release.set()
        await asyncio.gather(*requests, return_exceptions=True)


async def test_actual_delivered_claim_replays_without_repeating_the_provider(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="delivery-claim-contract@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(environment="development", notifications_enabled=True)
    provider = RecordingProvider()
    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        try:
            first = await send_notification(
                db,
                settings,
                provider,
                farm=farm,
                recipient=recipient,
                alert_class="SCREENING_FLAG",
                message="Actual screening result",
                payload="actual-finding:41:CONFIRMED",
                now_local=midday(farm),
            )
            replay = await send_notification(
                db,
                settings,
                provider,
                farm=farm,
                recipient=recipient,
                alert_class="SCREENING_FLAG",
                message="Actual screening result",
                payload="actual-finding:41:CONFIRMED",
                now_local=midday(farm),
            )
        except (NoResultFound, MultipleResultsFound) as exc:
            pytest.fail(f"A committed delivery must resolve its own durable replay claim: {exc}")
        assert first.status == replay.status == "SENT"
        assert first.fresh and not replay.fresh
        assert len(provider.sent) == 1
        rows = list((await db.execute(select(NotificationLog))).scalars())
        assert len(rows) == 1 and rows[0].status == "SENT"
        assert rows[0].provider_message_id == "rec-1" and rows[0].error is None


async def test_provider_failure_receipt_preserves_exact_redacted_error_bound(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="delivery-error-bound@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    settings = Settings(environment="development", notifications_enabled=True)
    prefix = "provider-refusal-" + "x" * 650

    class RefusalProvider:
        name = "refusal"

        async def send_sms(self, phone: str, message: str) -> DeliveryResult:
            return DeliveryResult(ok=False, error=prefix + " " + phone)

    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        outcome = await send_notification(
            db,
            settings,
            RefusalProvider(),
            farm=farm,
            recipient=recipient,
            alert_class="SCREENING_FLAG",
            message="Actual screening result",
            payload="actual-finding:42:CONFIRMED",
            now_local=midday(farm),
        )
        assert outcome.status == "FAILED" and outcome.fresh
        row = (await db.execute(select(NotificationLog))).scalar_one()
        assert row.error == prefix[:500] and len(row.error) == 500
        assert row.provider_message_id is None


@pytest.mark.parametrize("overdue", [False, True], ids=["today", "overdue"])
async def test_digest_literal_body_preserves_five_titles_and_sixty_character_bound(
    client: httpx.AsyncClient,
    overdue: bool,
) -> None:
    owner = await owner_with_farm(client, email="digest-literal-contract@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        role_id = await db.scalar(
            select(FarmMembership.role_id).where(FarmMembership.id == membership_id)
        )
        assert isinstance(role_id, int)
        reference = today(farm.timezone)
        names = ["A" * 60 + "outside-sms-bound", *[f"Inspection {i:02d}" for i in range(1, 12)]]
        db.add_all(
            [
                Task(
                    farm_id=farm_id,
                    title=name,
                    due_date=reference - timedelta(days=int(overdue)),
                    category="OTHER",
                    status="PENDING",
                    assigned_role_id=role_id,
                )
                for name in names
            ]
        )
        await db.commit()
        body = await service._digest_text_for_recipient(db, farm, recipient, reference)
    lines = [f"Herdly {reference.isoformat()}: 12 duties today"]
    if overdue:
        lines.append("12 overdue (incl. today's list)")
    lines.extend(["- " + "A" * 60, *[f"- Inspection {i:02d}" for i in range(1, 5)]])
    lines.append("... and 7 more")
    assert body == "\n".join(lines)
