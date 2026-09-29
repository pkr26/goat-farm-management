"""Data retention sweep (2026-09-28 audit, ITEM 9.1).

Covers the FK-safe child-first deletion of aged screening chains (including
the chain-root anchoring that keeps a re-claimed old photo's young runs
deletable), the terminal-task cutoff (PENDING and awaiting-verification DONE
rows are never candidates), the batch-size bound and its stop rule, the
disabled-by-default configuration, sweep idempotency, and the lifespan
loop's opt-in gating.
"""

import asyncio
import hashlib
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.main as main_module
from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import (
    ScreeningContentClaim,
    ScreeningCrop,
    ScreeningFinding,
    ScreeningImage,
    ScreeningRun,
    Task,
    TaskStatus,
)
from app.services import retention
from app.services.retention import RetentionSummary, run_retention_sweep
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
                    select(func.count()).select_from(model).where(model.farm_id == farm_id)
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
    return Settings(environment="development", **overrides)


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


async def test_batch_size_bound_is_respected_and_still_drains_fully(
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

    # One helper call never deletes more than batch_size, and a short batch
    # stops the loop (the sibling cleanup loops' stop rule).
    async with get_sessionmaker()() as db:
        candidates = select(Task.id).where(
            Task.farm_id == farm_id,
            Task.status == TaskStatus.DONE.value,
        )
        first = await retention._delete_in_batches(
            db, table=Task, id_column=Task.id, candidates=candidates, batch_size=2
        )
        assert first == 2
        await db.commit()

    # The sweep loops past full batches until the cohort is drained.
    async with get_sessionmaker()() as db:
        summary = await run_retention_sweep(db, _settings(retention_delete_batch_size=2))
    assert summary.terminal_tasks == 3
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


def test_retention_settings_are_disabled_and_bounded_by_default() -> None:
    settings = _settings()
    assert settings.retention_sweep_enabled is False
    assert settings.retention_sweep_interval_seconds == 86_400
    assert settings.retention_screening_days == 180
    assert settings.retention_terminal_task_days == 365
    assert settings.retention_delete_batch_size == 500

    with pytest.raises(ValidationError):
        _settings(retention_screening_days=29)
    with pytest.raises(ValidationError):
        _settings(retention_terminal_task_days=29)
    with pytest.raises(ValidationError):
        _settings(retention_delete_batch_size=0)


async def test_retention_loop_idles_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "get_settings", _settings)
    # Returns immediately instead of parking on the daily interval.
    await asyncio.wait_for(main_module._retention_sweep_loop(), timeout=2)


async def test_retention_loop_sweeps_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()
    settings.retention_sweep_enabled = True
    settings.retention_sweep_interval_seconds = 0.01  # assignment bypasses validation
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    swept = asyncio.Event()
    calls: list[int] = []

    async def fake_sweep(_db, _settings) -> RetentionSummary:
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
