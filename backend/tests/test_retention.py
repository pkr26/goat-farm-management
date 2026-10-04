"""Data retention sweep (2026-09-28 audit, ITEM 9.1).

Covers the FK-safe child-first deletion of aged screening chains (including
the chain-root anchoring that keeps a re-claimed old photo's young runs
deletable), the terminal-task cutoff (PENDING and awaiting-verification DONE
rows are never candidates), the batch-size bound and its stop rule, the
default-on production configuration, sweep idempotency, and the lifespan
loop's explicit development-disable gating.
"""

import asyncio
import hashlib
import logging
import threading
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession

import app.main as main_module
from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import (
    Farm,
    NotificationLog,
    NotificationOutbox,
    NotificationRecipient,
    ScreeningBatch,
    ScreeningCallReservation,
    ScreeningContentClaim,
    ScreeningCrop,
    ScreeningDailyBudget,
    ScreeningFinding,
    ScreeningImage,
    ScreeningRetentionDeletion,
    ScreeningRun,
    Task,
    TaskStatus,
)
from app.services import retention
from app.services.retention import RetentionSummary, run_retention_sweep
from app.services.screening import pipeline as screening_pipeline
from app.services.screening.s3 import (
    ScreeningStorage,
    ScreeningStorageDeleteInProgress,
    ScreeningStorageError,
)
from app.utils import today, utcnow

from .conftest import owner_with_farm

OLD_SCREENING_AGE = timedelta(days=200)  # past the 180-day default
YOUNG_SCREENING_AGE = timedelta(days=10)
OLD_TASK_AGE = timedelta(days=400)  # past the 365-day default
YOUNG_TASK_AGE = timedelta(days=100)

_MODELS_BY_NAME = {
    "findings": ScreeningFinding,
    "runs": ScreeningRun,
    "crops": ScreeningCrop,
    "claims": ScreeningContentClaim,
    "images": ScreeningImage,
    "tasks": Task,
}


async def _table_counts(farm_id: int) -> dict[str, int]:
    async with get_sessionmaker()() as db:
        return {
            name: (
                await db.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.__table__.c["farm_id"] == farm_id)
                )
            ).scalar_one()
            for name, model in _MODELS_BY_NAME.items()
        }


async def _seed_chain(
    db: AsyncSession,
    *,
    farm_id: int,
    image_created_at: datetime,
    key_suffix: str,
    reclaimed_run_created_at: datetime | None = None,
) -> None:
    """One full screening fact chain: image ← crop/runs/claim ← findings.

    ``reclaimed_run_created_at`` adds a younger run on the (old) image — the
    worker's ERROR-retry/FLAGGED-re-screen shape that chain-root anchoring
    must still be able to delete.
    """
    image = ScreeningImage(
        farm_id=farm_id,
        bucket="BREEDING",
        s3_bucket="test-bucket",
        s3_key=f"raw/{farm_id}/2026-01-01/BREEDING/{key_suffix}.jpg",
        status="FLAGGED",
        created_at=image_created_at,
        updated_at=image_created_at,
    )
    db.add(image)
    await db.flush()
    crop = ScreeningCrop(
        farm_id=farm_id,
        image_id=image.id,
        crop_index=0,
        box_x=100,
        box_y=100,
        box_w=600,
        box_h=300,
        status="FLAGGED",
        created_at=image_created_at,
    )
    db.add(crop)
    await db.flush()
    runs = [
        ScreeningRun(
            farm_id=farm_id,
            image_id=image.id,
            stage="GATE",
            run_status="OK",
            verdict="flagged",
            provider="fake",
            model="fake",
            prompt_version="v1",
            latency_ms=10,
            created_at=image_created_at,
        ),
        ScreeningRun(
            farm_id=farm_id,
            image_id=image.id,
            crop_id=crop.id,
            stage="GATE",
            run_status="OK",
            verdict="flagged",
            provider="fake",
            model="fake",
            prompt_version="v1",
            latency_ms=10,
            created_at=image_created_at,
        ),
    ]
    if reclaimed_run_created_at is not None:
        runs.append(
            ScreeningRun(
                farm_id=farm_id,
                image_id=image.id,
                stage="GATE",
                run_status="OK",
                verdict="healthy",
                provider="fake",
                model="fake",
                prompt_version="v1",
                latency_ms=10,
                created_at=reclaimed_run_created_at,
            )
        )
    db.add_all(runs)
    await db.flush()
    db.add_all(
        [
            ScreeningFinding(
                farm_id=farm_id,
                run_id=runs[0].id,
                label="possible lesion",
                region="mouth",
                created_at=image_created_at,
            ),
            ScreeningFinding(
                farm_id=farm_id,
                run_id=runs[1].id,
                crop_id=crop.id,
                label="possible lesion",
                region="mouth",
                created_at=image_created_at,
            ),
        ]
    )
    db.add(
        ScreeningContentClaim(
            farm_id=farm_id,
            image_id=image.id,
            sha256=hashlib.sha256(f"{farm_id}:{key_suffix}".encode()).hexdigest(),
            created_at=image_created_at,
        )
    )


async def _seed_task(
    db: AsyncSession,
    *,
    farm_id: int,
    title: str,
    status: str,
    category: str = "OTHER",
    completed_at: datetime | None = None,
    verified_at: datetime | None = None,
    skipped_at: datetime | None = None,
) -> Task:
    task = Task(
        farm_id=farm_id,
        title=title,
        due_date=today(),
        status=status,
        category=category,
        auto_generated=False,
        completed_at=completed_at,
        verified_at=verified_at,
        skipped_at=skipped_at,
    )
    db.add(task)
    await db.flush()
    return task


def _settings(**overrides: Any) -> Settings:
    return Settings(environment="development", s3_bucket="test-bucket", **overrides)


@pytest.fixture(autouse=True)
def _stub_permanent_object_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retention tests exercise SQL ordering without contacting object storage."""
    monkeypatch.setattr(
        ScreeningStorage,
        "delete_permanently",
        lambda _storage, _keys: None,
    )


async def test_sweep_deletes_aged_chains_child_first_and_preserves_young_chain(
    client: httpx.AsyncClient,
) -> None:
    owner_a = await owner_with_farm(client, email="retention-a@farm.in", farm_name="Farm A")
    owner_b = await owner_with_farm(client, email="retention-b@farm.in", farm_name="Farm B")
    farm_a = int(owner_a["X-Farm-Id"])
    farm_b = int(owner_b["X-Farm-Id"])
    old = utcnow() - OLD_SCREENING_AGE
    young = utcnow() - YOUNG_SCREENING_AGE

    async with get_sessionmaker()() as db:
        # Farm A: one aged chain whose photo was re-claimed five days ago
        # (young run on an old image), plus one young chain that must survive.
        await _seed_chain(
            db,
            farm_id=farm_a,
            image_created_at=old,
            key_suffix="old-a",
            reclaimed_run_created_at=utcnow() - timedelta(days=5),
        )
        await _seed_chain(db, farm_id=farm_a, image_created_at=young, key_suffix="young-a")
        await _seed_chain(db, farm_id=farm_b, image_created_at=old, key_suffix="old-b")
        await db.commit()

    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    # Aged chains drain whole, young-reclaim run included: 2 findings per
    # chain, A has 3 runs (2 + reclaim), B has 2; one crop/claim/image each.
    assert summary.screening_findings == 4
    assert summary.screening_runs == 5
    assert summary.screening_crops == 2
    assert summary.screening_content_claims == 2
    assert summary.screening_images == 2
    assert summary.terminal_tasks == 0

    # Child-first order kept FK integrity at commit; only the young chain is left.
    assert await _table_counts(farm_a) == {
        "findings": 2,
        "runs": 2,
        "crops": 1,
        "claims": 1,
        "images": 1,
        "tasks": 0,
    }
    assert await _table_counts(farm_b) == {
        "findings": 0,
        "runs": 0,
        "crops": 0,
        "claims": 0,
        "images": 0,
        "tasks": 0,
    }


async def test_terminal_task_cutoff_respects_status_and_terminal_timestamp(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retention-tasks@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    old = utcnow() - OLD_TASK_AGE
    young = utcnow() - YOUNG_TASK_AGE

    async with get_sessionmaker()() as db:
        await _seed_task(
            db, farm_id=farm_id, title="old done", status=TaskStatus.DONE.value, completed_at=old
        )
        young_done = await _seed_task(
            db,
            farm_id=farm_id,
            title="young done",
            status=TaskStatus.DONE.value,
            completed_at=young,
        )
        # DONE in a verification-required category is NOT terminal
        # (services/tasks.py): it still awaits a verifier however old it is.
        awaiting = await _seed_task(
            db,
            farm_id=farm_id,
            title="old done cleaning",
            status=TaskStatus.DONE.value,
            category="CLEANING",
            completed_at=old,
        )
        await _seed_task(
            db,
            farm_id=farm_id,
            title="old verified",
            status=TaskStatus.VERIFIED.value,
            completed_at=old - timedelta(days=1),
            verified_at=old,
        )
        await _seed_task(
            db,
            farm_id=farm_id,
            title="old skipped",
            status=TaskStatus.SKIPPED.value,
            skipped_at=old,
        )
        # Retention ages from the terminal transition, not completion:
        # completed long ago but verified recently still survives.
        lately_verified = await _seed_task(
            db,
            farm_id=farm_id,
            title="lately verified",
            status=TaskStatus.VERIFIED.value,
            completed_at=old,
            verified_at=young,
        )
        pending = await _seed_task(
            db, farm_id=farm_id, title="old pending", status=TaskStatus.PENDING.value
        )
        survivors = {young_done.id, awaiting.id, lately_verified.id, pending.id}
        await db.commit()

    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    assert summary.terminal_tasks == 3  # old done, old verified, old skipped
    async with get_sessionmaker()() as db:
        remaining = set(
            (await db.execute(select(Task.id).where(Task.farm_id == farm_id))).scalars()
        )
    assert remaining == survivors


async def test_single_delete_batch_is_bounded_and_sweeps_make_eventual_progress(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retention-batch@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    old = utcnow() - OLD_TASK_AGE

    async with get_sessionmaker()() as db:
        for index in range(5):
            await _seed_task(
                db,
                farm_id=farm_id,
                title=f"old done {index}",
                status=TaskStatus.DONE.value,
                completed_at=old,
            )
        await db.commit()

    async with get_sessionmaker()() as db:
        candidates = select(Task.id).where(Task.farm_id == farm_id)
        first = await retention._delete_batch(
            db, table=Task, id_column=Task.id, candidates=candidates, batch_size=2
        )
        assert first == 2
        await db.commit()
    assert (await _table_counts(farm_id))["tasks"] == 3
    settings = _settings(retention_delete_batch_size=2, retention_max_batches_per_farm=1)
    async with get_sessionmaker()() as db:
        first_pass = await run_retention_sweep(db, settings)
    assert first_pass.terminal_tasks == 2
    assert (await _table_counts(farm_id))["tasks"] == 1
    async with get_sessionmaker()() as db:
        second_pass = await run_retention_sweep(db, settings)
    assert second_pass.terminal_tasks == 1
    assert (await _table_counts(farm_id))["tasks"] == 0


async def test_second_sweep_is_a_noop(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="retention-idem@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    old = utcnow() - OLD_SCREENING_AGE

    async with get_sessionmaker()() as db:
        await _seed_chain(db, farm_id=farm_id, image_created_at=old, key_suffix="old-i")
        await _seed_chain(
            db,
            farm_id=farm_id,
            image_created_at=utcnow() - YOUNG_SCREENING_AGE,
            key_suffix="young-i",
        )
        await _seed_task(
            db,
            farm_id=farm_id,
            title="old done",
            status=TaskStatus.DONE.value,
            completed_at=utcnow() - OLD_TASK_AGE,
        )
        await db.commit()

    async with get_sessionmaker()() as db:
        first = await run_retention_sweep(db, _settings())
    assert first.total_deleted == 2 + 2 + 1 + 1 + 1 + 1  # chain rows + one task

    async with get_sessionmaker()() as db:
        second = await run_retention_sweep(db, _settings())
    assert second.total_deleted == 0
    assert await _table_counts(farm_id) == {
        "findings": 2,
        "runs": 2,
        "crops": 1,
        "claims": 1,
        "images": 1,
        "tasks": 0,
    }


async def test_sweep_bounds_batches_budgets_and_notification_ledgers(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retention-ledgers@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    role = await client.post(
        "/api/team/roles",
        json={"name": "Retention recipient", "permissions": ["dashboard.view"]},
        headers=owner,
    )
    assert role.status_code == 201, role.text
    worker = await client.post(
        "/api/team/workers",
        json={
            "name": "Retention Worker",
            "email": "retention-ledger-worker@farm.in",
            "password": "worker-pass-123",
            "role_id": role.json()["id"],
        },
        headers=owner,
    )
    assert worker.status_code == 201, worker.text
    old = utcnow() - timedelta(days=500)
    young = utcnow() - timedelta(days=10)
    old_date = old.date()
    young_date = young.date()

    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        recipient = NotificationRecipient(
            farm_id=farm_id,
            membership_id=worker.json()["id"],
            phone="+919876543210",
        )
        db.add(recipient)
        await db.flush()
        old_outbox = NotificationOutbox(
            farm_id=farm_id,
            alert_class="SCREENING_FLAG",
            event_key="retention-old",
            message="old",
            created_at=old,
            due_at=old,
            completed_at=old,
        )
        young_outbox = NotificationOutbox(
            farm_id=farm_id,
            alert_class="SCREENING_FLAG",
            event_key="retention-young",
            message="young",
            created_at=young,
            due_at=young,
            completed_at=young,
        )
        db.add_all([old_outbox, young_outbox])
        await db.flush()
        db.add_all(
            [
                ScreeningBatch(
                    farm_id=farm_id,
                    created_by_id=farm.owner_id,
                    created_at=old,
                ),
                ScreeningBatch(
                    farm_id=farm_id,
                    created_by_id=farm.owner_id,
                    created_at=young,
                ),
                ScreeningDailyBudget(farm_id=farm_id, local_date=old_date, reserved_calls=1),
                ScreeningDailyBudget(farm_id=farm_id, local_date=young_date, reserved_calls=1),
                NotificationLog(
                    farm_id=farm_id,
                    recipient_id=recipient.id,
                    outbox_id=old_outbox.id,
                    alert_class="SCREENING_FLAG",
                    payload_hash="a" * 64,
                    local_date=old_date,
                    status="SENT",
                    created_at=old,
                ),
                NotificationLog(
                    farm_id=farm_id,
                    recipient_id=recipient.id,
                    alert_class="DAILY_DIGEST",
                    payload_hash="b" * 64,
                    local_date=old_date,
                    status="SENT",
                    created_at=old,
                ),
                NotificationLog(
                    farm_id=farm_id,
                    recipient_id=recipient.id,
                    outbox_id=young_outbox.id,
                    alert_class="SCREENING_FLAG",
                    payload_hash="c" * 64,
                    local_date=young_date,
                    status="SENT",
                    created_at=young,
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                ScreeningCallReservation(
                    attempt_id="retention-old-attempt",
                    farm_id=farm_id,
                    local_date=old_date,
                    provider="fake",
                    created_at=old,
                ),
                ScreeningCallReservation(
                    attempt_id="retention-young-attempt",
                    farm_id=farm_id,
                    local_date=young_date,
                    provider="fake",
                    created_at=young,
                ),
            ]
        )
        await db.commit()

    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    assert summary.screening_batches == 1
    assert summary.screening_call_reservations == 1
    assert summary.screening_daily_budgets == 1
    assert summary.notification_log == 2
    assert summary.notification_outbox == 1
    async with get_sessionmaker()() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ScreeningBatch)
                .where(ScreeningBatch.farm_id == farm_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ScreeningDailyBudget)
                .where(ScreeningDailyBudget.farm_id == farm_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(NotificationOutbox)
                .where(NotificationOutbox.farm_id == farm_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(NotificationLog)
                .where(NotificationLog.farm_id == farm_id)
            )
            == 1
        )


async def test_object_delete_failure_preserves_database_chain_for_retry(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await owner_with_farm(client, email="retention-object-failure@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    old = utcnow() - OLD_SCREENING_AGE
    async with get_sessionmaker()() as db:
        await _seed_chain(db, farm_id=farm_id, image_created_at=old, key_suffix="objects")
        image = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.farm_id == farm_id))
        ).scalar_one()
        crop = (
            await db.execute(select(ScreeningCrop).where(ScreeningCrop.farm_id == farm_id))
        ).scalar_one()
        image.normalized_key = f"normalized/{farm_id}/image.jpg"
        crop.normalized_key = f"normalized/{farm_id}/crop.jpg"
        expected = {image.s3_key, image.normalized_key, crop.normalized_key}
        await db.commit()

    attempted: list[str] = []

    secret_provider_detail = "https://storage.invalid/private?token=do-not-log"

    def fail_delete(_storage: object, keys: list[str]) -> None:
        attempted.extend(keys)
        raise ScreeningStorageError(secret_provider_detail)

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", fail_delete)
    caplog.set_level(logging.WARNING, logger=retention.__name__)
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    # One exact key is attempted per durable dispatcher transaction; the
    # full immutable manifest remains available for later retries.
    assert len(attempted) == 1
    assert set(attempted) < expected
    assert summary.failed_farms == 1
    assert summary.total_deleted == 0
    assert (await _table_counts(farm_id))["images"] == 1
    async with get_sessionmaker()() as db:
        intent = (
            await db.execute(
                select(ScreeningRetentionDeletion).where(
                    ScreeningRetentionDeletion.farm_id == farm_id
                )
            )
        ).scalar_one()
        assert intent.status == "PENDING"
        assert intent.attempt_count == 1
        assert intent.failure_count == 1
        assert intent.last_attempt_at is not None
        assert intent.next_attempt_at is not None
        assert intent.next_attempt_at >= intent.last_attempt_at + timedelta(minutes=5)
        assert intent.last_error == "OBJECT_STORE_DELETE_FAILED"
        assert set(intent.object_keys) == expected
    assert "code=OBJECT_STORE_DELETE_FAILED" in caplog.text
    assert secret_provider_detail not in caplog.text
    assert all(key not in caplog.text for key in expected)

    # The persisted due time survives process/sweep boundaries and prevents a
    # permanently broken bucket from being hit again on every maintenance
    # poll. Once made due, a second consecutive failure doubles the delay.
    async with get_sessionmaker()() as db:
        immediate = await run_retention_sweep(db, _settings())
    assert immediate.failed_farms == 0
    assert len(attempted) == 1
    async with get_sessionmaker()() as db:
        intent = (
            await db.execute(
                select(ScreeningRetentionDeletion).where(
                    ScreeningRetentionDeletion.farm_id == farm_id
                )
            )
        ).scalar_one()
        intent.next_attempt_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    async with get_sessionmaker()() as db:
        second_failure = await run_retention_sweep(db, _settings())
    assert second_failure.failed_farms == 1
    assert len(attempted) == 2
    async with get_sessionmaker()() as db:
        intent = (
            await db.execute(
                select(ScreeningRetentionDeletion).where(
                    ScreeningRetentionDeletion.farm_id == farm_id
                )
            )
        ).scalar_one()
        assert intent.failure_count == 2
        assert intent.last_attempt_at is not None
        assert intent.next_attempt_at is not None
        assert intent.next_attempt_at >= intent.last_attempt_at + timedelta(minutes=10)


async def test_bounded_version_progress_continues_within_daily_sweep(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="retention-version-progress@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        await _seed_chain(
            db,
            farm_id=farm_id,
            image_created_at=utcnow() - OLD_SCREENING_AGE,
            key_suffix="version-progress",
        )
        await db.commit()

    calls = 0

    def progress_then_complete(_storage: object, _keys: list[str]) -> None:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise ScreeningStorageDeleteInProgress("bounded progress")

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", progress_then_complete)
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    # Expected page-bounded progress remains immediately due, so the finite
    # dispatch budget completes a multi-page key in this daily pass instead
    # of stretching it over multiple 24-hour loop intervals.
    assert summary.failed_farms == 0
    assert summary.screening_images == 1
    assert calls == 3


async def test_object_failure_plus_failure_ack_error_never_logs_provider_payload(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await owner_with_farm(client, email="retention-double-failure-redaction@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    secret_key = f"raw/{farm_id}/private-location.jpg"
    secret_provider_detail = "https://storage.invalid/private?credential=never-log"
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="test-bucket",
            s3_key=secret_key,
            status="HEALTHY",
            created_at=utcnow() - OLD_SCREENING_AGE,
        )
        db.add(image)
        await db.commit()

    def fail_storage(_storage: object, _keys: list[str]) -> None:
        raise ScreeningStorageError(secret_provider_detail)

    async def fail_ack(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("sensitive database acknowledgement payload")

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", fail_storage)
    monkeypatch.setattr(retention, "_record_object_deletion_failure", fail_ack)
    caplog.set_level(logging.WARNING, logger=retention.__name__)

    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    assert summary.failed_farms == 1
    assert "code=OBJECT_STORE_DELETE_FAILED" in caplog.text
    assert "code=FAILURE_STATE_ACK_FAILED" in caplog.text
    assert secret_key not in caplog.text
    assert secret_provider_detail not in caplog.text
    assert "sensitive database acknowledgement payload" not in caplog.text


async def test_planning_database_error_never_logs_bound_object_manifest(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await owner_with_farm(client, email="retention-plan-db-redaction@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    secret_key = f"raw/{farm_id}/private-plan-location.jpg"
    async with get_sessionmaker()() as db:
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket="test-bucket",
                s3_key=secret_key,
                status="HEALTHY",
                created_at=utcnow() - OLD_SCREENING_AGE,
            )
        )
        await db.commit()

    async def fail_planning(*_args: object, **_kwargs: object) -> None:
        raise StatementError(
            "manifest insert failed",
            "INSERT INTO screening_retention_deletions (object_keys) VALUES (:object_keys)",
            {"object_keys": [secret_key]},
            RuntimeError("database rejected private manifest"),
        )

    monkeypatch.setattr(retention, "_sweep_farm", fail_planning)
    caplog.set_level(logging.ERROR, logger=retention.__name__)
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    assert summary.failed_farms == 1
    assert "error_type=StatementError" in caplog.text
    assert secret_key not in caplog.text
    assert "database rejected private manifest" not in caplog.text


async def test_finalization_database_error_never_logs_bound_object_manifest(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = await owner_with_farm(client, email="retention-final-db-redaction@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    secret_key = f"raw/{farm_id}/private-finalization-location.jpg"
    async with get_sessionmaker()() as db:
        db.add(
            ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket="test-bucket",
                s3_key=secret_key,
                status="HEALTHY",
                created_at=utcnow() - OLD_SCREENING_AGE,
            )
        )
        await db.commit()

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", lambda *_args: None)

    async def fail_finalization(*_args: object, **_kwargs: object) -> None:
        raise StatementError(
            "manifest requeue failed",
            "UPDATE screening_retention_deletions SET object_keys=:object_keys",
            {"object_keys": [secret_key]},
            RuntimeError("database rejected private finalization manifest"),
        )

    monkeypatch.setattr(retention, "_finalize_screening_deletions", fail_finalization)
    caplog.set_level(logging.ERROR, logger=retention.__name__)
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(
            db,
            _settings(retention_delete_batch_size=1, retention_max_batches_per_farm=1),
        )

    assert summary.failed_farms == 1
    assert "error_type=StatementError" in caplog.text
    assert secret_key not in caplog.text
    assert "database rejected private finalization manifest" not in caplog.text


async def test_oversized_manifest_fails_before_any_object_store_call(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="retention-oversized-manifest@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    now = utcnow()
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="test-bucket",
            s3_key=f"raw/{farm_id}/oversized.jpg",
            status="HEALTHY",
            retention_tombstoned_at=now,
        )
        db.add(image)
        await db.flush()
        db.add(
            ScreeningRetentionDeletion(
                farm_id=farm_id,
                image_id=image.id,
                s3_bucket=image.s3_bucket,
                object_keys=[f"corrupt/{index}.jpg" for index in range(33)],
                preserved_keys=[],
            )
        )
        await db.commit()

    def must_not_call_storage(_storage: object, _keys: list[str]) -> None:
        raise AssertionError("invalid oversized manifests must fail before S3")

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", must_not_call_storage)
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    assert summary.failed_farms == 1
    async with get_sessionmaker()() as db:
        intent = (
            await db.execute(
                select(ScreeningRetentionDeletion).where(
                    ScreeningRetentionDeletion.farm_id == farm_id
                )
            )
        ).scalar_one()
        assert intent.status == "PENDING"
        assert intent.last_error == "INVALID_MANIFEST"
        assert intent.deleted_keys == []


async def test_manifest_checkpoints_one_verified_key_per_transaction(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="retention-key-cursor@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    now = utcnow()
    keys = [f"raw/{farm_id}/cursor-{index}.jpg" for index in range(3)]
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="test-bucket",
            s3_key=keys[0],
            normalized_key=keys[1],
            status="HEALTHY",
            retention_tombstoned_at=now,
        )
        db.add(image)
        await db.flush()
        db.add(
            ScreeningRetentionDeletion(
                farm_id=farm_id,
                image_id=image.id,
                s3_bucket=image.s3_bucket,
                object_keys=keys,
                preserved_keys=[],
            )
        )
        await db.commit()

    calls: list[list[str]] = []
    monkeypatch.setattr(
        ScreeningStorage,
        "delete_permanently",
        lambda _storage, exact_keys: calls.append(list(exact_keys)),
    )
    async with get_sessionmaker()() as db:
        first = await retention._delete_one_pending_manifest(
            db,
            farm_id=farm_id,
            settings=_settings(),
            excluded_intent_ids=set(),
        )
        assert first is not None and not first.complete
        await db.commit()
    async with get_sessionmaker()() as db:
        checkpoint = await db.get(ScreeningRetentionDeletion, first.intent_id)
        assert checkpoint is not None
        assert checkpoint.status == "PENDING"
        assert len(checkpoint.deleted_keys) == 1
        second = await retention._delete_one_pending_manifest(
            db,
            farm_id=farm_id,
            settings=_settings(),
            excluded_intent_ids=set(),
        )
        assert second is not None and not second.complete
        await db.commit()
        third = await retention._delete_one_pending_manifest(
            db,
            farm_id=farm_id,
            settings=_settings(),
            excluded_intent_ids=set(),
        )
        assert third is not None and third.complete
        await db.commit()

    assert len(calls) == 3
    assert all(len(call) == 1 for call in calls)
    assert set().union(*map(set, calls)) == set(keys)


async def test_object_success_then_ack_failure_retries_from_durable_intent(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A crash after S3 success but before its DB acknowledgement is safe.

    The committed intent remains PENDING, the relational evidence remains,
    and the next pass repeats the idempotent purge before finalizing once.
    """
    owner = await owner_with_farm(client, email="retention-ack-failure@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        await _seed_chain(
            db,
            farm_id=farm_id,
            image_created_at=utcnow() - OLD_SCREENING_AGE,
            key_suffix="ack-failure",
        )
        await db.commit()

    object_calls: list[list[str]] = []

    def successful_delete(_storage: object, keys: list[str]) -> None:
        object_calls.append(list(keys))

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", successful_delete)
    real_dispatch = retention._delete_one_pending_manifest
    secret_ack_detail = "private object checkpoint params must not be logged"

    async def dispatch_then_lose_ack(*args: Any, **kwargs: Any) -> Any:
        dispatch = await real_dispatch(*args, **kwargs)
        if dispatch is not None:
            raise RuntimeError(secret_ack_detail)
        return None

    monkeypatch.setattr(retention, "_delete_one_pending_manifest", dispatch_then_lose_ack)
    caplog.set_level(logging.ERROR, logger=retention.__name__)
    async with get_sessionmaker()() as db:
        first = await run_retention_sweep(db, _settings())

    assert first.failed_farms == 1
    assert first.total_deleted == 0
    assert len(object_calls) == 1
    assert "error_type=RuntimeError" in caplog.text
    assert secret_ack_detail not in caplog.text
    assert (await _table_counts(farm_id))["images"] == 1
    async with get_sessionmaker()() as db:
        pending = (
            await db.execute(
                select(ScreeningRetentionDeletion).where(
                    ScreeningRetentionDeletion.farm_id == farm_id
                )
            )
        ).scalar_one()
        assert pending.status == "PENDING"
        assert pending.attempt_count == 0

    monkeypatch.setattr(retention, "_delete_one_pending_manifest", real_dispatch)
    async with get_sessionmaker()() as db:
        second = await run_retention_sweep(db, _settings())

    assert second.failed_farms == 0
    assert second.screening_images == 1
    assert len(object_calls) == 2
    assert (await _table_counts(farm_id))["images"] == 0
    async with get_sessionmaker()() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ScreeningRetentionDeletion)
                .where(ScreeningRetentionDeletion.farm_id == farm_id)
            )
            == 0
        )


async def test_object_success_then_explicit_transaction_rollback_is_retryable(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact S3-success/SQL-rollback boundary retains a PENDING intent."""
    owner = await owner_with_farm(client, email="retention-real-rollback@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    now = utcnow()
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="test-bucket",
            s3_key=f"raw/{farm_id}/rollback.jpg",
            status="HEALTHY",
            retention_tombstoned_at=now,
        )
        db.add(image)
        await db.flush()
        intent = ScreeningRetentionDeletion(
            farm_id=farm_id,
            image_id=image.id,
            s3_bucket=image.s3_bucket,
            object_keys=[image.s3_key],
            preserved_keys=[],
        )
        db.add(intent)
        await db.commit()
        intent_id = intent.id

    calls: list[list[str]] = []

    def delete_successfully(_storage: object, keys: list[str]) -> None:
        calls.append(list(keys))

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", delete_successfully)
    async with get_sessionmaker()() as db:
        dispatched = await retention._delete_one_pending_manifest(
            db,
            farm_id=farm_id,
            settings=_settings(),
            excluded_intent_ids=set(),
        )
        assert dispatched is not None and dispatched.intent_id == intent_id
        await db.rollback()  # simulate commit/connection failure after verified S3 deletion

    async with get_sessionmaker()() as db:
        durable = await db.get(ScreeningRetentionDeletion, intent_id)
        assert durable is not None
        assert durable.status == "PENDING"
        assert durable.attempt_count == 0
        dispatched = await retention._delete_one_pending_manifest(
            db,
            farm_id=farm_id,
            settings=_settings(),
            excluded_intent_ids=set(),
        )
        assert dispatched is not None and dispatched.intent_id == intent_id
        await db.commit()

    assert len(calls) == 2


async def test_finalization_failure_keeps_verified_intent_and_never_redeletes_objects(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="retention-finalize-failure@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        await _seed_chain(
            db,
            farm_id=farm_id,
            image_created_at=utcnow() - OLD_SCREENING_AGE,
            key_suffix="finalize-failure",
        )
        await db.commit()

    object_calls: list[list[str]] = []

    def successful_delete(_storage: object, keys: list[str]) -> None:
        object_calls.append(list(keys))

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", successful_delete)
    real_finalize = retention._finalize_screening_deletions

    async def fail_finalize(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated relational finalization failure")

    monkeypatch.setattr(retention, "_finalize_screening_deletions", fail_finalize)
    async with get_sessionmaker()() as db:
        first = await run_retention_sweep(db, _settings())

    assert first.failed_farms == 1
    assert len(object_calls) == 1
    assert (await _table_counts(farm_id))["images"] == 1
    async with get_sessionmaker()() as db:
        ready = (
            await db.execute(
                select(ScreeningRetentionDeletion).where(
                    ScreeningRetentionDeletion.farm_id == farm_id
                )
            )
        ).scalar_one()
        assert ready.status == "OBJECTS_DELETED"
        assert ready.objects_deleted_at is not None

    monkeypatch.setattr(retention, "_finalize_screening_deletions", real_finalize)

    def must_not_redelete(_storage: object, _keys: list[str]) -> None:
        raise AssertionError("verified manifests must not call S3 again")

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", must_not_redelete)
    async with get_sessionmaker()() as db:
        second = await run_retention_sweep(db, _settings())

    assert second.failed_farms == 0
    assert second.screening_images == 1
    assert (await _table_counts(farm_id))["images"] == 0


async def test_shared_legacy_derivative_is_preserved_for_live_chain(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_owner = await owner_with_farm(client, email="retention-shared-key-old@farm.in")
    live_owner = await owner_with_farm(client, email="retention-shared-key-live@farm.in")
    old_farm_id = int(old_owner["X-Farm-Id"])
    live_farm_id = int(live_owner["X-Farm-Id"])
    shared_key = "normalized/legacy-cross-farm-shared.jpg"
    async with get_sessionmaker()() as db:
        await _seed_chain(
            db,
            farm_id=old_farm_id,
            image_created_at=utcnow() - OLD_SCREENING_AGE,
            key_suffix="shared-old",
        )
        await _seed_chain(
            db,
            farm_id=live_farm_id,
            image_created_at=utcnow() - YOUNG_SCREENING_AGE,
            key_suffix="shared-young",
        )
        old_image = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.farm_id == old_farm_id))
        ).scalar_one()
        live_image = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.farm_id == live_farm_id))
        ).scalar_one()
        old_image.normalized_key = shared_key
        live_image.normalized_key = shared_key
        old_raw_key = old_image.s3_key
        await db.commit()

    object_calls: list[list[str]] = []

    def capture_delete(_storage: object, keys: list[str]) -> None:
        object_calls.append(list(keys))

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", capture_delete)
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    assert summary.failed_farms == 0
    assert summary.screening_images == 1
    assert object_calls == [[old_raw_key]]
    async with get_sessionmaker()() as db:
        survivor = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.farm_id == live_farm_id))
        ).scalar_one()
        assert survivor.normalized_key == shared_key


async def test_poisoned_intents_back_off_later_good_progresses_and_staging_is_bounded(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More poison rows than one dispatch batch cannot pin the farm forever."""
    owner = await owner_with_farm(client, email="retention-poison-cursor@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    old = utcnow() - OLD_SCREENING_AGE
    async with get_sessionmaker()() as db:
        for suffix in ("poison-1", "poison-2", "poison-3", "good", "poison-4", "poison-5"):
            await _seed_chain(
                db,
                farm_id=farm_id,
                image_created_at=old,
                key_suffix=suffix,
            )
        await db.commit()

    attempted: list[str] = []

    def selectively_fail(_storage: object, keys: list[str]) -> None:
        attempted.extend(keys)
        if not any("good" in key for key in keys):
            raise ScreeningStorageError("private provider failure detail")

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", selectively_fail)
    settings = _settings(retention_delete_batch_size=2, retention_max_batches_per_farm=2)
    async with get_sessionmaker()() as db:
        first = await run_retention_sweep(db, settings)
    async with get_sessionmaker()() as db:
        second = await run_retention_sweep(db, settings)
    async with get_sessionmaker()() as db:
        third = await run_retention_sweep(db, settings)

    assert first.failed_farms == 1
    assert second.failed_farms == 1
    assert second.screening_images == 1
    assert third.failed_farms == 1
    assert any("good" in key for key in attempted)
    async with get_sessionmaker()() as db:
        surviving_keys = set(
            (
                await db.execute(
                    select(ScreeningImage.s3_key).where(ScreeningImage.farm_id == farm_id)
                )
            ).scalars()
        )
        outstanding = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ScreeningRetentionDeletion)
                    .where(ScreeningRetentionDeletion.farm_id == farm_id)
                )
            ).scalar_one()
        )
    assert all("good" not in key for key in surviving_keys)
    assert outstanding == 4  # batch_size * max_batches_per_farm, never more


async def test_concurrent_planners_serialize_outstanding_cap_decision(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retention-planner-cap-race@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    now = utcnow()
    async with get_sessionmaker()() as db:
        for index in range(4):
            await _seed_chain(
                db,
                farm_id=farm_id,
                image_created_at=now - OLD_SCREENING_AGE,
                key_suffix=f"planner-race-{index}",
            )
        await db.commit()

    settings = _settings(retention_delete_batch_size=2, retention_max_batches_per_farm=1)
    plan_kwargs: dict[str, Any] = {
        "farm_id": farm_id,
        "screening_cutoff": now - timedelta(days=settings.retention_screening_days),
        "screening_batch_cutoff": now - timedelta(days=settings.retention_screening_batch_days),
        "screening_budget_cutoff": now.date()
        - timedelta(days=settings.retention_screening_budget_days),
        "notification_cutoff": now - timedelta(days=settings.retention_notification_days),
        "task_cutoff": now - timedelta(days=settings.retention_terminal_task_days),
        "batch_size": settings.retention_delete_batch_size,
        "settings": settings,
    }
    first_summary = RetentionSummary()
    second_summary = RetentionSummary()

    async with (
        get_sessionmaker()() as first_db,
        get_sessionmaker()() as second_db,
        get_sessionmaker()() as observer_db,
    ):
        await retention._set_retention_timeouts(first_db, settings)
        await retention._sweep_farm(first_db, summary=first_summary, **plan_kwargs)
        second_pid = int(await second_db.scalar(text("SELECT pg_backend_pid()")))
        await retention._set_retention_timeouts(second_db, settings)
        second_plan = asyncio.create_task(
            retention._sweep_farm(second_db, summary=second_summary, **plan_kwargs)
        )

        # Prove the second transaction reached the advisory mutex before
        # releasing the first; this pins the stale-count race deterministically.
        waiting = False
        for _ in range(100):
            wait_event = await observer_db.scalar(
                text("SELECT wait_event FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": second_pid},
            )
            if wait_event == "advisory":
                waiting = True
                break
            await asyncio.sleep(0.01)
        assert waiting
        assert not second_plan.done()

        await first_db.commit()
        await asyncio.wait_for(second_plan, timeout=1)
        await second_db.commit()

    assert first_summary.screening_deletion_intents_staged == 2
    assert second_summary.screening_deletion_intents_staged == 0
    async with get_sessionmaker()() as db:
        outstanding = await db.scalar(
            select(func.count())
            .select_from(ScreeningRetentionDeletion)
            .where(ScreeningRetentionDeletion.farm_id == farm_id)
        )
    assert outstanding == 2


async def test_retention_planning_uses_one_clock_without_moving_worker_lease(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planning timestamps the durable saga without faking worker progress."""
    owner = await owner_with_farm(client, email="retention-planning-clock@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    frozen = utcnow()
    prior_updated_at = frozen - OLD_SCREENING_AGE
    async with get_sessionmaker()() as db:
        await _seed_chain(
            db,
            farm_id=farm_id,
            image_created_at=prior_updated_at,
            key_suffix="planning-clock",
        )
        await db.commit()

    monkeypatch.setattr(retention, "utcnow", lambda: frozen)
    settings = _settings(retention_delete_batch_size=1, retention_max_batches_per_farm=1)
    summary = RetentionSummary()
    async with get_sessionmaker()() as db:
        await retention._set_retention_timeouts(db, settings)
        await retention._sweep_farm(
            db,
            farm_id=farm_id,
            screening_cutoff=frozen - timedelta(days=settings.retention_screening_days),
            screening_batch_cutoff=frozen - timedelta(days=settings.retention_screening_batch_days),
            screening_budget_cutoff=frozen.date()
            - timedelta(days=settings.retention_screening_budget_days),
            notification_cutoff=frozen - timedelta(days=settings.retention_notification_days),
            task_cutoff=frozen - timedelta(days=settings.retention_terminal_task_days),
            batch_size=settings.retention_delete_batch_size,
            settings=settings,
            summary=summary,
        )
        await db.commit()

    assert summary.screening_deletion_intents_staged == 1
    async with get_sessionmaker()() as db:
        image = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.farm_id == farm_id))
        ).scalar_one()
        intent = (
            await db.execute(
                select(ScreeningRetentionDeletion).where(
                    ScreeningRetentionDeletion.farm_id == farm_id
                )
            )
        ).scalar_one()
    assert intent.status == "PENDING"
    assert intent.next_attempt_at == frozen
    assert intent.created_at == frozen
    assert intent.updated_at == frozen
    assert image.retention_tombstoned_at == frozen
    assert image.updated_at == prior_updated_at


async def test_bucket_mismatch_isolated_to_its_own_manifest(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="retention-bucket-isolation@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    old = utcnow() - OLD_SCREENING_AGE
    async with get_sessionmaker()() as db:
        await _seed_chain(db, farm_id=farm_id, image_created_at=old, key_suffix="wrong-bucket")
        await _seed_chain(db, farm_id=farm_id, image_created_at=old, key_suffix="right-bucket")
        images = list(
            (
                await db.execute(
                    select(ScreeningImage)
                    .where(ScreeningImage.farm_id == farm_id)
                    .order_by(ScreeningImage.id)
                )
            ).scalars()
        )
        images[0].s3_bucket = "legacy-wrong-bucket"
        right_image_id = images[1].id
        await db.commit()

    calls: list[list[str]] = []
    monkeypatch.setattr(
        ScreeningStorage,
        "delete_permanently",
        lambda _storage, keys: calls.append(list(keys)),
    )
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(
            db,
            _settings(retention_delete_batch_size=2, retention_max_batches_per_farm=1),
        )

    assert summary.failed_farms == 1
    assert summary.screening_images == 1
    assert len(calls) == 1
    async with get_sessionmaker()() as db:
        assert await db.get(ScreeningImage, right_image_id) is None
        mismatch = (
            await db.execute(
                select(ScreeningRetentionDeletion).where(
                    ScreeningRetentionDeletion.farm_id == farm_id
                )
            )
        ).scalar_one()
        assert mismatch.status == "PENDING"
        assert mismatch.last_error == "CONFIGURED_BUCKET_MISMATCH"


async def test_screening_claim_ignores_committed_retention_tombstone(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retention-tombstone-claim@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    now = utcnow()
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="test-bucket",
            s3_key=f"raw/{farm_id}/claim-blocked.jpg",
            status="PENDING",
            created_at=now,
            updated_at=now,
        )
        db.add(image)
        await db.flush()
        image.retention_tombstoned_at = now
        db.add(
            ScreeningRetentionDeletion(
                farm_id=farm_id,
                image_id=image.id,
                s3_bucket=image.s3_bucket,
                object_keys=[image.s3_key],
                preserved_keys=[],
            )
        )
        await db.commit()

    async with get_sessionmaker()() as db:
        claimed, error_retries, flagged_retries = await screening_pipeline._claim_retry_rows(
            db,
            limit=10,
            now=now,
            abandoned_after=timedelta(hours=1),
            stale_after=timedelta(hours=1),
        )

    assert claimed == []
    assert error_retries == 0
    assert flagged_retries == 0


async def test_claim_skips_image_while_retention_planner_holds_root_lock(
    client: httpx.AsyncClient,
) -> None:
    """SKIP LOCKED keeps a worker out before the tombstone is even visible."""
    owner = await owner_with_farm(client, email="retention-claim-waiter@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    now = utcnow()
    async with get_sessionmaker()() as setup:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="test-bucket",
            s3_key=f"raw/{farm_id}/claim-waiter.jpg",
            status="PENDING",
            created_at=now,
            updated_at=now,
        )
        setup.add(image)
        await setup.commit()
        image_id = image.id

    async with get_sessionmaker()() as planner, get_sessionmaker()() as worker:
        locked = (
            await planner.execute(
                select(ScreeningImage).where(ScreeningImage.id == image_id).with_for_update()
            )
        ).scalar_one()
        locked.retention_tombstoned_at = now
        planner.add(
            ScreeningRetentionDeletion(
                farm_id=farm_id,
                image_id=image_id,
                s3_bucket=locked.s3_bucket,
                object_keys=[locked.s3_key],
                preserved_keys=[],
            )
        )
        await planner.flush()

        waiter = asyncio.create_task(
            screening_pipeline._claim_retry_rows(
                worker,
                limit=10,
                now=now,
                abandoned_after=timedelta(hours=1),
                stale_after=timedelta(hours=1),
            )
        )
        claimed, error_retries, flagged_retries = await asyncio.wait_for(waiter, timeout=2)
        await planner.commit()

    assert claimed == []
    assert error_retries == 0
    assert flagged_retries == 0


async def test_tombstoned_content_claim_transfers_to_fresh_identical_upload(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retention-claim-transfer@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    digest = "a" * 64
    now = utcnow()
    async with get_sessionmaker()() as db:
        old_image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="test-bucket",
            s3_key=f"raw/{farm_id}/old-canonical.jpg",
            sha256=digest,
            status="HEALTHY",
            retention_tombstoned_at=now,
        )
        fresh_image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="test-bucket",
            s3_key=f"raw/{farm_id}/fresh-identical.jpg",
            status="PROCESSING",
        )
        db.add_all([old_image, fresh_image])
        await db.flush()
        db.add(
            ScreeningContentClaim(
                farm_id=farm_id,
                image_id=old_image.id,
                sha256=digest,
            )
        )
        db.add(
            ScreeningRetentionDeletion(
                farm_id=farm_id,
                image_id=old_image.id,
                s3_bucket=old_image.s3_bucket,
                object_keys=[old_image.s3_key],
                preserved_keys=[],
            )
        )
        await db.commit()
        fresh_id = fresh_image.id

    async with get_sessionmaker()() as db:
        fresh = await db.get(ScreeningImage, fresh_id)
        assert fresh is not None
        owner_id = await screening_pipeline._reserve_normalized_content(db, fresh, digest)

    assert owner_id == fresh_id
    async with get_sessionmaker()() as db:
        claim = (
            await db.execute(
                select(ScreeningContentClaim).where(
                    ScreeningContentClaim.farm_id == farm_id,
                    ScreeningContentClaim.sha256 == digest,
                )
            )
        ).scalar_one()
        assert claim.image_id == fresh_id


async def test_detail_waiter_observes_tombstone_committed_while_waiting(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retention-detail-waiter@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as setup:
        await _seed_chain(
            setup,
            farm_id=farm_id,
            image_created_at=utcnow() - OLD_SCREENING_AGE,
            key_suffix="detail-waiter",
        )
        await setup.commit()
        image_id = int(
            (
                await setup.execute(
                    select(ScreeningImage.id).where(ScreeningImage.farm_id == farm_id)
                )
            ).scalar_one()
        )

    async with get_sessionmaker()() as planner:
        image = (
            await planner.execute(
                select(ScreeningImage).where(ScreeningImage.id == image_id).with_for_update()
            )
        ).scalar_one()
        image.retention_tombstoned_at = utcnow()
        planner.add(
            ScreeningRetentionDeletion(
                farm_id=farm_id,
                image_id=image.id,
                s3_bucket=image.s3_bucket,
                object_keys=[image.s3_key],
                preserved_keys=[],
            )
        )
        await planner.flush()

        waiter = asyncio.create_task(client.get(f"/api/screening/images/{image_id}", headers=owner))
        await asyncio.sleep(0.05)
        assert not waiter.done(), "detail route should wait on the root image fence"
        await planner.commit()
        response = await asyncio.wait_for(waiter, timeout=2)

    assert response.status_code == 404


async def test_tombstoned_chain_is_hidden_from_screening_read_and_review_routes(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retention-hidden-chain@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        await _seed_chain(
            db,
            farm_id=farm_id,
            image_created_at=utcnow() - OLD_SCREENING_AGE,
            key_suffix="hidden-chain",
        )
        await db.flush()
        image = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.farm_id == farm_id))
        ).scalar_one()
        finding = (
            (await db.execute(select(ScreeningFinding).where(ScreeningFinding.farm_id == farm_id)))
            .scalars()
            .first()
        )
        assert finding is not None
        image.retention_tombstoned_at = utcnow()
        db.add(
            ScreeningRetentionDeletion(
                farm_id=farm_id,
                image_id=image.id,
                s3_bucket=image.s3_bucket,
                object_keys=[image.s3_key],
                preserved_keys=[],
            )
        )
        await db.commit()
        image_id = image.id
        finding_id = finding.id

    listing = await client.get("/api/screening/images", headers=owner)
    assert listing.status_code == 200
    assert listing.json()["total"] == 0
    assert listing.json()["images"] == []

    detail = await client.get(f"/api/screening/images/{image_id}", headers=owner)
    assert detail.status_code == 404
    history = await client.get(f"/api/screening/findings/{finding_id}/reviews", headers=owner)
    assert history.status_code == 404
    review = await client.post(
        f"/api/screening/findings/{finding_id}/review",
        json={
            "status": "CONFIRMED",
            "expected_status": "PENDING_REVIEW",
            "expected_revision": 0,
        },
        headers=owner,
    )
    assert review.status_code == 404
    exported = await client.get("/api/screening/export", headers=owner)
    assert exported.status_code == 200
    assert exported.json()["record_count"] == 0
    stats = await client.get("/api/screening/stats?days=365", headers=owner)
    assert stats.status_code == 200
    assert stats.json()["providers"] == []
    overview = await client.get("/api/owner/overview", headers=owner)
    assert overview.status_code == 200
    overview_row = next(row for row in overview.json()["farms"] if row["farm_id"] == farm_id)
    assert overview_row["open_screening_flags"] == 0


async def test_all_shared_manifest_has_terminal_disposition_without_s3_call(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive all-preserved batches do not construct/call an S3 client."""
    owner = await owner_with_farm(client, email="retention-all-shared@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        image = ScreeningImage(
            farm_id=farm_id,
            bucket="BREEDING",
            s3_bucket="test-bucket",
            s3_key=f"raw/{farm_id}/all-shared.jpg",
            status="HEALTHY",
        )
        db.add(image)
        await db.flush()
        image.retention_tombstoned_at = utcnow()
        intent = ScreeningRetentionDeletion(
            farm_id=farm_id,
            image_id=image.id,
            s3_bucket=image.s3_bucket,
            object_keys=[image.s3_key],
            preserved_keys=[],
        )
        db.add(intent)
        await db.commit()

    async def all_shared(_db: AsyncSession, **kwargs: Any) -> set[str]:
        return set(kwargs["keys"])

    def must_not_call_s3(_storage: object, _keys: list[str]) -> None:
        raise AssertionError("an all-preserved manifest must not call S3")

    monkeypatch.setattr(retention, "_keys_referenced_by_live_chains", all_shared)
    monkeypatch.setattr(ScreeningStorage, "delete_permanently", must_not_call_s3)
    async with get_sessionmaker()() as db:
        due_at = await db.scalar(
            select(ScreeningRetentionDeletion.next_attempt_at).where(
                ScreeningRetentionDeletion.farm_id == farm_id
            )
        )
        assert due_at is not None and due_at <= utcnow()
        dispatch = await retention._delete_one_pending_manifest(
            db,
            farm_id=farm_id,
            settings=_settings(),
            excluded_intent_ids=set(),
        )
        assert dispatch is not None and dispatch.complete
        await db.commit()

    async with get_sessionmaker()() as db:
        ready = await db.get(ScreeningRetentionDeletion, dispatch.intent_id)
        assert ready is not None
        assert ready.status == "OBJECTS_DELETED"
        assert ready.preserved_keys == ready.object_keys


async def test_concurrent_dispatchers_skip_locked_and_process_distinct_manifests(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await owner_with_farm(client, email="retention-concurrent-dispatch@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    now = utcnow()
    async with get_sessionmaker()() as db:
        for suffix in ("first", "second"):
            image = ScreeningImage(
                farm_id=farm_id,
                bucket="BREEDING",
                s3_bucket="test-bucket",
                s3_key=f"raw/{farm_id}/concurrent-{suffix}.jpg",
                status="HEALTHY",
                retention_tombstoned_at=now,
            )
            db.add(image)
            await db.flush()
            db.add(
                ScreeningRetentionDeletion(
                    farm_id=farm_id,
                    image_id=image.id,
                    s3_bucket=image.s3_bucket,
                    object_keys=[image.s3_key],
                    preserved_keys=[],
                )
            )
        await db.commit()

    entered = threading.Event()
    release = threading.Event()
    call_lock = threading.Lock()
    calls: list[list[str]] = []

    def blocking_first_delete(_storage: object, keys: list[str]) -> None:
        with call_lock:
            calls.append(list(keys))
            call_number = len(calls)
        if call_number == 1:
            entered.set()
            assert release.wait(timeout=2)

    monkeypatch.setattr(ScreeningStorage, "delete_permanently", blocking_first_delete)

    async def dispatch_one(db: AsyncSession) -> int | None:
        dispatch = await retention._delete_one_pending_manifest(
            db,
            farm_id=farm_id,
            settings=_settings(),
            excluded_intent_ids=set(),
        )
        await db.commit()
        return None if dispatch is None else dispatch.intent_id

    async with get_sessionmaker()() as first_db, get_sessionmaker()() as second_db:
        first_task = asyncio.create_task(dispatch_one(first_db))
        assert await asyncio.to_thread(entered.wait, 1)
        second_id = await asyncio.wait_for(dispatch_one(second_db), timeout=2)
        release.set()
        first_id = await asyncio.wait_for(first_task, timeout=2)

    assert first_id is not None and second_id is not None and first_id != second_id
    assert len(calls) == 2
    assert calls[0] != calls[1]
    async with get_sessionmaker()() as db:
        statuses = list(
            (
                await db.execute(
                    select(ScreeningRetentionDeletion.status)
                    .where(ScreeningRetentionDeletion.farm_id == farm_id)
                    .order_by(ScreeningRetentionDeletion.id)
                )
            ).scalars()
        )
    assert statuses == ["OBJECTS_DELETED", "OBJECTS_DELETED"]


async def test_concurrent_finalizers_delete_verified_chain_once(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="retention-concurrent-finalize@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    now = utcnow()
    async with get_sessionmaker()() as db:
        await _seed_chain(
            db,
            farm_id=farm_id,
            image_created_at=now - OLD_SCREENING_AGE,
            key_suffix="concurrent-finalize",
        )
        image = (
            await db.execute(select(ScreeningImage).where(ScreeningImage.farm_id == farm_id))
        ).scalar_one()
        image.retention_tombstoned_at = now
        intent = ScreeningRetentionDeletion(
            farm_id=farm_id,
            image_id=image.id,
            s3_bucket=image.s3_bucket,
            object_keys=[image.s3_key],
            preserved_keys=[],
        )
        db.add(intent)
        await db.flush()
        intent.deleted_keys = [image.s3_key]
        intent.status = "OBJECTS_DELETED"
        intent.attempt_count = 1
        intent.last_attempt_at = now
        intent.next_attempt_at = None
        intent.objects_deleted_at = now
        await db.commit()

    first = RetentionSummary()
    second = RetentionSummary()
    async with get_sessionmaker()() as db_one, get_sessionmaker()() as db_two:
        await retention._finalize_screening_deletions(
            db_one, farm_id=farm_id, batch_size=10, summary=first
        )
        # db_one still holds the intent lock and has not committed. SKIP LOCKED
        # makes the second finalizer return without double-counting/deleting.
        await retention._finalize_screening_deletions(
            db_two, farm_id=farm_id, batch_size=10, summary=second
        )
        await db_one.commit()
        await db_two.commit()

    assert first.screening_images == 1
    assert second.screening_images == 0
    assert (await _table_counts(farm_id))["images"] == 0


async def test_a_failing_farm_is_skipped_and_does_not_starve_later_farms(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-29 audit: farms iterate in sorted order, so an unhandled
    failure on farm K would deterministically starve every farm sorted after
    K, every interval. The sweep must roll the poisoned farm back, count it
    in failed_farms, and still sweep the later farm."""
    owner_poison = await owner_with_farm(
        client, email="retention-iso-poison@farm.in", farm_name="Poison Farm"
    )
    owner_later = await owner_with_farm(
        client, email="retention-iso-later@farm.in", farm_name="Later Farm"
    )
    poison_farm = int(owner_poison["X-Farm-Id"])
    later_farm = int(owner_later["X-Farm-Id"])
    assert poison_farm < later_farm, "the poisoned farm must sort first"

    old = utcnow() - OLD_TASK_AGE
    async with get_sessionmaker()() as db:
        for farm_id, tag in ((poison_farm, "poison"), (later_farm, "later")):
            await _seed_task(
                db,
                farm_id=farm_id,
                title=f"old done {tag}",
                status=TaskStatus.DONE.value,
                completed_at=old,
            )
        await db.commit()

    real_sweep_farm = retention._sweep_farm

    async def poisoned_sweep_farm(db: AsyncSession, *, farm_id: int, **kwargs: Any) -> None:
        if farm_id == poison_farm:
            raise RuntimeError("poisoned farm (retention isolation test)")
        await real_sweep_farm(db, farm_id=farm_id, **kwargs)

    monkeypatch.setattr(retention, "_sweep_farm", poisoned_sweep_farm)
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings())

    assert summary.failed_farms == 1
    assert summary.terminal_tasks == 1  # the LATER farm was still swept
    counts = await _table_counts(later_farm)
    assert counts["tasks"] == 0, "the later farm must not be starved"
    poison_counts = await _table_counts(poison_farm)
    assert poison_counts["tasks"] == 1, "the poisoned farm's rows survive for the next pass"


def test_retention_settings_are_enabled_and_bounded_by_default() -> None:
    settings = _settings()
    assert settings.retention_sweep_enabled is True
    assert settings.retention_sweep_interval_seconds == 86_400
    assert settings.retention_screening_days == 180
    assert settings.retention_screening_batch_days == 365
    assert settings.retention_screening_budget_days == 90
    assert settings.retention_notification_days == 400
    assert settings.retention_terminal_task_days == 365
    assert settings.retention_delete_batch_size == 500

    with pytest.raises(ValidationError):
        _settings(retention_screening_days=29)
    with pytest.raises(ValidationError):
        _settings(retention_terminal_task_days=29)
    with pytest.raises(ValidationError):
        _settings(retention_delete_batch_size=0)


async def test_retention_loop_idles_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: _settings(retention_sweep_enabled=False),
    )
    # Returns immediately instead of parking on the daily interval.
    await asyncio.wait_for(main_module._retention_sweep_loop(), timeout=2)


async def test_retention_loop_sweeps_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    settings.retention_sweep_enabled = True
    settings.retention_sweep_interval_seconds = 1  # assignment bypasses validation
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    swept = asyncio.Event()
    calls: list[int] = []

    async def fake_sweep(
        _db: AsyncSession, _settings: Settings, *, after_farm_id: int = 0
    ) -> RetentionSummary:
        calls.append(1)
        swept.set()
        return RetentionSummary()

    monkeypatch.setattr(main_module, "run_retention_sweep", fake_sweep)
    worker = asyncio.create_task(main_module._retention_sweep_loop())
    try:
        await asyncio.wait_for(swept.wait(), timeout=2)
        assert calls
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
