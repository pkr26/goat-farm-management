"""Keep an accepted delivery's settlement atomic across a policy-clock crossing."""

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import NotificationLog
from app.services import retention
from app.services.notifications import send_notification
from app.services.notifications.service import SendOutcome

from .conftest import owner_with_farm
from .test_notifications import (
    RecordingProvider,
    _farm,
    _membership_id,
    _recipient,
    midday,
)


async def test_paid_legacy_delivery_can_finish_while_retention_crosses_its_policy_cutoff(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="notification-retention-settle@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    async with get_sessionmaker()() as setup:
        recipient = await _recipient(setup, farm_id, membership_id)
        recipient_id = recipient.id
    settings = Settings(
        environment="development",
        notifications_enabled=True,
        retention_notification_days=30,
        retention_max_batches_per_farm=1,
    )
    provider = RecordingProvider()
    real_get = AsyncSession.get
    settlement_loaded = asyncio.Event()
    release_settlement = asyncio.Event()
    actual_claims: list[tuple[int, datetime, int, str]] = []

    async def load_then_schedule(db: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        # Delegate unchanged to genuine AsyncSession/asyncpg before scheduling.
        row = await real_get(db, *args, **kwargs)
        if args[0] is NotificationLog and "with_for_update" in kwargs and row is not None:
            actual_claims.append(
                (
                    row.id,
                    row.created_at,
                    int(await db.scalar(text("SELECT pg_backend_pid()"))),
                    row.status,
                )
            )
            settlement_loaded.set()
            await release_settlement.wait()
        return row

    monkeypatch.setattr(AsyncSession, "get", load_then_schedule)
    requests: list[asyncio.Task[SendOutcome]] = []
    lock_modes: list[str] = []
    retention_summary: retention.RetentionSummary | None = None
    policy_now: datetime | None = None
    async with (
        get_sessionmaker()() as send_db,
        get_sessionmaker()() as retention_db,
        get_sessionmaker()() as observer,
    ):
        farm = await _farm(send_db, farm_id)
        send_recipient = await send_db.get(type(recipient), recipient_id)
        assert send_recipient is not None
        try:
            requests.append(
                asyncio.create_task(
                    send_notification(
                        send_db,
                        settings,
                        provider,
                        farm=farm,
                        recipient=send_recipient,
                        alert_class="DAILY_DIGEST",
                        message="One ordinary paid daily delivery",
                        payload="one ordinary paid daily delivery",
                        now_local=midday(farm),
                    )
                )
            )
            async with asyncio.timeout(20):
                await settlement_loaded.wait()
            claim_id, created_at, settlement_pid, _claimed_status = actual_claims[0]
            # Cross the declared thirty-day policy by one microsecond using
            # the retention service's existing clock seam. The real generated
            # receipt and its server-created timestamp remain untouched. This
            # models clock advancement/process suspension after provider
            # acceptance and before settlement; it asserts no provider horizon.
            policy_now = created_at + timedelta(
                days=settings.retention_notification_days, microseconds=1
            )
            monkeypatch.setattr(retention, "utcnow", lambda: policy_now)
            lock_modes = list(
                (
                    await observer.execute(
                        text(
                            "SELECT mode FROM pg_locks WHERE pid = :pid "
                            "AND relation = 'notification_log'::regclass ORDER BY mode"
                        ).bindparams(pid=settlement_pid)
                    )
                ).scalars()
            )
            retention_summary = await retention.run_retention_sweep(retention_db, settings)
        finally:
            release_settlement.set()
            outcomes = await asyncio.gather(*requests, return_exceptions=True)

    assert retention_summary is not None and policy_now is not None
    witness = json.dumps(
        {
            "actual_claim_id": actual_claims[0][0],
            "actual_server_created_at": actual_claims[0][1].isoformat(),
            "actual_claim_status_before_schedule": actual_claims[0][3],
            "retention_clock": policy_now.isoformat(),
            "retention_cutoff": (
                policy_now - timedelta(days=settings.retention_notification_days)
            ).isoformat(),
            "declared_retention_days": settings.retention_notification_days,
            "actual_settlement_lock_modes": lock_modes,
            "actual_retention_notification_deletes": retention_summary.notification_log,
            "actual_retention_failed_farms": retention_summary.failed_farms,
            "actual_paid_provider_attempts": len(provider.sent),
            "actual_sender_outcomes": [repr(outcome) for outcome in outcomes],
            "generated_receipt_fields_edited": False,
            "clock_crossing_scope": "after accepted send, before settlement commit",
        },
        sort_keys=True,
    )
    print(witness)
    receipt_path = os.environ.get("MUTATION_RECEIPT_PATH")
    if receipt_path:
        Path(receipt_path).with_suffix(".settlement-witness.json").write_text(witness + "\n")
    assert len(provider.sent) == 1
    assert retention_summary.failed_farms == 0
    assert len(outcomes) == 1 and isinstance(outcomes[0], SendOutcome), (
        "An accepted delivery must settle normally across the valid retention cutoff",
        outcomes,
    )
    outcome = outcomes[0]
    assert outcome.status == "SENT" and outcome.fresh
    assert retention_summary.notification_log == 0
    async with get_sessionmaker()() as db:
        saved = (
            await db.execute(select(NotificationLog).where(NotificationLog.id == claim_id))
        ).scalar_one()
        assert saved.status == "SENT" and saved.created_at == created_at
