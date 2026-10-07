"""Real health writes alert only on suspicion, preserving their durable message."""

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import FarmMembership, HealthEvent, NotificationLog
from app.models.notifications import NotificationOutbox
from app.services.notifications.providers import ConsoleNotificationProvider, DeliveryResult

from .conftest import owner_with_farm
from .test_health_extended import make_animal
from .test_notifications import _movement_recipient
from .test_tasks_extended import worker_headers


@pytest.mark.parametrize("suspected", [False, True])
async def test_real_health_alerts_preserve_suspicion_message_and_single_delivery_on_replay(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    suspected: bool,
) -> None:
    owner = await owner_with_farm(client)
    farm_id = int(owner["X-Farm-Id"])
    _worker, worker_id = await worker_headers(client, owner, "VET", "health-alert@example.test")
    animal = await make_animal(client, owner, "REAL-HEALTH-ALERT")
    async with get_sessionmaker()() as db:
        membership_id = (
            await db.execute(
                select(FarmMembership.id).where(
                    FarmMembership.farm_id == farm_id,
                    FarmMembership.user_id == worker_id,
                )
            )
        ).scalar_one()
        await _movement_recipient(db, farm_id, membership_id, opted_in=True)
    sent: list[tuple[str, str]] = []

    class CapturingProvider(ConsoleNotificationProvider):
        async def send_sms(self, phone: str, message: str) -> DeliveryResult:
            # The real notification/outbox admission and delivery ledger
            # execute unchanged; only the existing console transport records
            # its message. No external network or SMS account is used.
            sent.append((phone, message))
            return await super().send_sms(phone, message)

    def capturing_provider(_settings: Settings) -> ConsoleNotificationProvider:
        return CapturingProvider()

    request_headers = owner | {"Idempotency-Key": "health-alert-reviewed-write"}
    request_body = {
        "animal_id": animal["id"],
        "type": "TREATMENT",
        "disease_target": "FMD",
        "suspected_scheduled_disease": suspected,
        "notes": "Veterinary examination",
    }
    expected_message = (
        "Herdly: suspected FMD recorded in the health log — "
        "movement restriction placed. Check the restricted animals."
    )
    settings = get_settings()
    with monkeypatch.context() as patch:
        patch.setattr(settings, "notifications_enabled", True)
        patch.setattr(settings, "notifications_quiet_start_hour", 4)
        patch.setattr(settings, "notifications_quiet_end_hour", 4)
        patch.setattr(
            "app.services.notifications.hooks.build_notification_provider", capturing_provider
        )
        created = await client.post(
            "/api/health/events", headers=request_headers, json=request_body
        )
        assert created.status_code == 201, created.text
        event_id = created.json()[0]["id"]
        assert len(sent) == (1 if suspected else 0)
        if suspected:
            assert sent[0] == ("+919999999999", expected_message)
        replay = await client.post("/api/health/events", headers=request_headers, json=request_body)
        assert replay.status_code == 201, replay.text
        assert replay.headers.get("Idempotency-Replayed") == "true"
        assert replay.json() == created.json()
        assert len(sent) == (1 if suspected else 0)
        async with get_sessionmaker()() as db:
            events = list((await db.execute(select(HealthEvent))).scalars())
            outbox = list((await db.execute(select(NotificationOutbox))).scalars())
            log = list((await db.execute(select(NotificationLog))).scalars())
            assert len(events) == 1 and events[0].suspected_scheduled_disease is suspected
            if suspected:
                assert len(outbox) == len(log) == 1
                assert outbox[0].event_key == f"movement-restriction:health:{event_id}"
                assert outbox[0].message == expected_message
                assert outbox[0].completed_at is not None and log[0].status == "SENT"
            else:
                assert not outbox and not log
