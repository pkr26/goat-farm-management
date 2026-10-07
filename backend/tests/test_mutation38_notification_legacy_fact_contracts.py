"""Retained paid/deferred receipts follow their actual confirmed clinical fact."""

from datetime import UTC, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound, NoResultFound

from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import NotificationLog, NotificationOutbox, ScreeningFinding
from app.services.notifications import service
from app.services.notifications.outbox import dispatch_outbox_event, enqueue_alert

from .test_notifications import RecordingProvider, _farm, _membership_id, _recipient, midday
from .test_screening_clinical_integrity import _finding


@pytest.mark.parametrize(
    ("legacy_status", "deliveries"),
    [("SENT", 0), ("FAILED", 0), ("SENDING", 0), ("SKIPPED_QUIET", 1), ("SKIPPED_CAP", 1)],
)
async def test_real_confirmed_fact_adopts_its_retained_receipt_before_cross_day_replay(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    legacy_status: str,
    deliveries: int,
) -> None:
    # The clinical decision is factual and committed before notifications
    # are enabled. A retained pre-outbox receipt is a native migration input;
    # it does not invent or repeat the veterinarian's domain decision.
    monkeypatch.setattr(get_settings(), "notifications_enabled", False)
    owner, finding_id = await _finding(client)
    response = await client.post(
        f"/api/screening/findings/{finding_id}/review",
        headers=owner,
        json={
            "status": "CONFIRMED",
            "expected_status": "PENDING_REVIEW",
            "expected_revision": 0,
            "review_note": "Confirmed clinical evidence before the notification migration",
        },
    )
    assert response.status_code == 200, response.text
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    event_key = f"finding:{finding_id}:review:1:CONFIRMED"
    monkeypatch.setattr(get_settings(), "notifications_enabled", True)
    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        at = midday(farm) + timedelta(days=1)
        recipient_id = recipient.id
        event_id = await enqueue_alert(
            db,
            farm_id,
            "SCREENING_FLAG",
            f"Actual confirmed clinical finding #{finding_id}",
            event_key,
        )
        assert event_id is not None
        legacy = NotificationLog(
            farm_id=farm_id,
            recipient_id=recipient_id,
            alert_class="SCREENING_FLAG",
            payload_hash=service.payload_hash(f"SCREENING_FLAG:{event_key}"),
            local_date=at.date(),
            status=legacy_status,
            provider_message_id="retained-accepted-receipt" if legacy_status == "SENT" else None,
        )
        db.add(legacy)
        await db.commit()
        legacy_id = legacy.id

    settings = Settings(environment="development", notifications_enabled=True)
    provider = RecordingProvider()
    try:
        sent = await dispatch_outbox_event(event_id, settings, provider, now_utc=at.astimezone(UTC))
        replay = await dispatch_outbox_event(
            event_id, settings, provider, now_utc=(at + timedelta(days=1)).astimezone(UTC)
        )
    except (NoResultFound, MultipleResultsFound, TypeError) as exc:
        pytest.fail(
            f"A real confirmed fact must resolve its typed retained delivery receipt: {exc}"
        )
    except RuntimeError as exc:
        if str(exc) != "claimed notification row vanished before settle":
            raise
        pytest.fail(f"An existing retained receipt cannot vanish during its adoption: {exc}")
    assert sent == deliveries and replay == 0
    assert len(provider.sent) == deliveries
    async with get_sessionmaker()() as db:
        logs = list((await db.execute(select(NotificationLog))).scalars())
        event = await db.get(NotificationOutbox, event_id)
        finding = await db.get(ScreeningFinding, finding_id)
        assert event is not None and event.completed_at is not None
        assert finding is not None
        assert finding.status == "CONFIRMED" and finding.review_revision == 1
        assert len(logs) == 1
        log = logs[0]
        assert log.outbox_id == event_id and log.recipient_id == recipient_id
        if deliveries:
            assert log.id != legacy_id and log.status == "SENT"
            assert log.provider_message_id == "rec-1"
        else:
            assert log.id == legacy_id and log.status == legacy_status
            assert log.provider_message_id == (
                "retained-accepted-receipt" if legacy_status == "SENT" else None
            )
