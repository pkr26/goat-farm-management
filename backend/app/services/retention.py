"""Data retention sweep (2026-09-28 audit, ITEM 9.1).

Screening fact chains (``screening_images`` ← crops/runs/content_claims ←
findings) and long-terminal duties grow without bound otherwise. The opt-in
sweep deletes them in bounded, farm-scoped batches — set-based
``DELETE ... WHERE id IN (SELECT ... LIMIT n)`` in FK-safe child-first
order — and commits per farm, so one farm's backlog never holds another
tenant's row locks and a crash mid-sweep simply resumes at the next interval.

Deliberate scope boundaries:

* ``weight_records`` / ``feeding_records`` are explicitly OUT: the growth
  curves and feed analytics ARE their long-term value (ITEM 9.1 lists them
  only under a future archive-schema/partition design, not deletion).
* S3 objects are NOT touched: the screening storage wrapper
  (``services/screening/s3.py``) has no delete path at all, and the pipeline
  already treats "an object deleted by a bucket lifecycle rule"
  (``pipeline.MAX_SCREENING_ATTEMPTS``) and "lifecycle-expired" objects
  (``pipeline._note_object_absent``) as first-class storage-level facts.
  Object expiry is the bucket lifecycle policy's job — operators pair
  ``GOATFARM_RETENTION_SCREENING_DAYS`` with a lifecycle rule on the
  screening prefix.
* ``screening_batches`` rows stay: one row per walkthrough, the
  upload-session audit anchor (its created_by FK is RESTRICT) — not part of
  the ITEM 9.1 fact set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from ..core.config import Settings
from ..models import (
    ScreeningContentClaim,
    ScreeningCrop,
    ScreeningFinding,
    ScreeningImage,
    ScreeningRun,
    Task,
    TaskStatus,
)
from ..utils import utcnow


@dataclass
class RetentionSummary:
    """Rows deleted by one sweep, by table (the maintenance-loop metric)."""

    screening_findings: int = 0
    screening_runs: int = 0
    screening_crops: int = 0
    screening_content_claims: int = 0
    screening_images: int = 0
    terminal_tasks: int = 0

    @property
    def total_deleted(self) -> int:
        return (
            self.screening_findings
            + self.screening_runs
            + self.screening_crops
            + self.screening_content_claims
            + self.screening_images
            + self.terminal_tasks
        )


async def _delete_in_batches(
    db: AsyncSession,
    *,
    table: type[Any],
    id_column: InstrumentedAttribute[int],
    candidates: Select[tuple[int]],
    batch_size: int,
) -> int:
    """Drain one deletion cohort in finite, lock-skipping batches.

    The bounded, locked candidate set is materialized before each DELETE:
    PostgreSQL may otherwise re-evaluate a LIMIT subquery embedded in a
    data-modifying statement and advance past ``batch_size`` (the same shape
    as the idempotency/refresh-session purges). STOP when a batch returns
    fewer rows than requested — the cohort is drained.
    """
    removed_total = 0
    while True:
        candidate_ids = list(
            (
                await db.execute(
                    candidates.order_by(id_column)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        if not candidate_ids:
            return removed_total
        removed = await db.execute(
            delete(table).where(id_column.in_(candidate_ids)).returning(id_column)
        )
        removed_count = len(removed.scalars().all())
        removed_total += removed_count
        if removed_count < batch_size:
            return removed_total


def _terminal_task_clause(cutoff: datetime) -> ColumnElement[bool]:
    """Long-terminal duties eligible for deletion.

    VERIFIED duties age from verified_at, SKIPPED from skipped_at and DONE
    from completed_at — the timestamp of the transition that made the row
    terminal (every one is NOT NULL for its status per the model CHECKs).
    PENDING rows are never candidates, and DONE rows in verification-required
    categories are NOT terminal either: they still await a verifier
    (``services.tasks`` — "DONE is NOT terminal" for those categories), so
    the model's shared awaiting-verification clause excludes them here
    exactly as it does on the board.
    """
    return or_(
        and_(
            Task.status == TaskStatus.DONE.value,
            ~Task.awaiting_verification_clause(),
            Task.completed_at < cutoff,
        ),
        and_(Task.status == TaskStatus.SKIPPED.value, Task.skipped_at < cutoff),
        and_(Task.status == TaskStatus.VERIFIED.value, Task.verified_at < cutoff),
    )


async def run_retention_sweep(db: AsyncSession, settings: Settings) -> RetentionSummary:
    """Delete aged screening chains and long-terminal duties for every farm.

    Farms are processed one at a time and each farm's deletes are committed
    before the next begins: a daily sweep never holds one tenant's row locks
    while draining another's, and every committed farm makes the sweep
    idempotent-resumable (a crash leaves the remaining farms for the next
    interval). The caller's trailing commit is then a no-op.
    """
    batch_size = settings.retention_delete_batch_size
    if not 1 <= batch_size <= 10_000:
        raise ValueError("retention_delete_batch_size must be between 1 and 10000")
    screening_cutoff = utcnow() - timedelta(days=settings.retention_screening_days)
    task_cutoff = utcnow() - timedelta(days=settings.retention_terminal_task_days)

    screening_farms = (
        await db.execute(
            select(ScreeningImage.farm_id).where(ScreeningImage.created_at < screening_cutoff)
        )
    ).scalars()
    task_farms = (
        await db.execute(select(Task.farm_id).where(_terminal_task_clause(task_cutoff)))
    ).scalars()

    summary = RetentionSummary()
    for farm_id in sorted(set(screening_farms).union(task_farms)):
        await _sweep_farm(
            db,
            farm_id=farm_id,
            screening_cutoff=screening_cutoff,
            task_cutoff=task_cutoff,
            batch_size=batch_size,
            summary=summary,
        )
        await db.commit()
    return summary


async def _sweep_farm(
    db: AsyncSession,
    *,
    farm_id: int,
    screening_cutoff: datetime,
    task_cutoff: datetime,
    batch_size: int,
    summary: RetentionSummary,
) -> None:
    """Delete one farm's retention cohort, child-first, in bounded batches.

    Chain eligibility anchors on the ROOT image's created_at, never on each
    row's own: the worker can re-claim an aged photo (ERROR retry, FLAGGED
    re-screen), so a 200-day-old image can carry a day-old run — deleting by
    per-row created_at would try to remove the image while younger children
    still reference it. The OR probes below (run_id OR crop_id, image_id OR
    crop_id) use the cascade-reverse indexes added for exactly this job
    (ix_screening_findings_farm_run / ix_screening_findings_farm_crop /
    ix_screening_runs_farm_crop — 2026-09-28 audit, D6), so FK safety never
    depends on the pipeline's crop↔image denormalization invariant. A row a
    SKIP LOCKED probe declines (a live worker re-claiming it) is cleaned by
    the ondelete=CASCADE foreign keys when its parent's batch commits; those
    cascade probes are served by the same indexes.
    """
    old_image_ids = select(ScreeningImage.id).where(
        ScreeningImage.farm_id == farm_id,
        ScreeningImage.created_at < screening_cutoff,
    )
    old_crop_ids = select(ScreeningCrop.id).where(
        ScreeningCrop.farm_id == farm_id,
        ScreeningCrop.image_id.in_(old_image_ids),
    )
    old_run_ids = select(ScreeningRun.id).where(
        ScreeningRun.farm_id == farm_id,
        ScreeningRun.image_id.in_(old_image_ids),
    )

    # FK-safe child-first order: findings → runs → crops → content_claims →
    # images (every child table of screening_images is covered).
    summary.screening_findings += await _delete_in_batches(
        db,
        table=ScreeningFinding,
        id_column=ScreeningFinding.id,
        candidates=select(ScreeningFinding.id).where(
            ScreeningFinding.farm_id == farm_id,
            or_(
                ScreeningFinding.run_id.in_(old_run_ids),
                and_(
                    ScreeningFinding.crop_id.is_not(None),
                    ScreeningFinding.crop_id.in_(old_crop_ids),
                ),
            ),
        ),
        batch_size=batch_size,
    )
    summary.screening_runs += await _delete_in_batches(
        db,
        table=ScreeningRun,
        id_column=ScreeningRun.id,
        candidates=select(ScreeningRun.id).where(
            ScreeningRun.farm_id == farm_id,
            or_(
                ScreeningRun.image_id.in_(old_image_ids),
                and_(
                    ScreeningRun.crop_id.is_not(None),
                    ScreeningRun.crop_id.in_(old_crop_ids),
                ),
            ),
        ),
        batch_size=batch_size,
    )
    summary.screening_crops += await _delete_in_batches(
        db,
        table=ScreeningCrop,
        id_column=ScreeningCrop.id,
        candidates=select(ScreeningCrop.id).where(
            ScreeningCrop.farm_id == farm_id,
            ScreeningCrop.image_id.in_(old_image_ids),
        ),
        batch_size=batch_size,
    )
    summary.screening_content_claims += await _delete_in_batches(
        db,
        table=ScreeningContentClaim,
        id_column=ScreeningContentClaim.id,
        candidates=select(ScreeningContentClaim.id).where(
            ScreeningContentClaim.farm_id == farm_id,
            ScreeningContentClaim.image_id.in_(old_image_ids),
        ),
        batch_size=batch_size,
    )
    summary.screening_images += await _delete_in_batches(
        db,
        table=ScreeningImage,
        id_column=ScreeningImage.id,
        candidates=select(ScreeningImage.id).where(
            ScreeningImage.farm_id == farm_id,
            ScreeningImage.created_at < screening_cutoff,
        ),
        batch_size=batch_size,
    )
    summary.terminal_tasks += await _delete_in_batches(
        db,
        table=Task,
        id_column=Task.id,
        candidates=select(Task.id).where(
            Task.farm_id == farm_id,
            _terminal_task_clause(task_cutoff),
        ),
        batch_size=batch_size,
    )
