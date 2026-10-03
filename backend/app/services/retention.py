"""Data retention sweep (2026-09-28 audit, ITEM 9.1).

Screening chains and terminal tasks are removed in finite root cohorts.
Every cohort commits separately, SQL discovery keyset-pages distinct farm IDs,
and a per-farm budget prevents a large tenant from monopolizing a pass. Counts
reflect only committed work. Child lock contention aborts a cohort within a
short lock timeout; skipped child rows are never left for an unbounded cascade.

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

import logging
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, and_, delete, or_, select, text, union
from sqlalchemy.engine import CursorResult
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

logger = logging.getLogger(__name__)


@dataclass
class RetentionSummary:
    """Rows deleted by one sweep, by table (the maintenance-loop metric)."""

    screening_findings: int = 0
    screening_runs: int = 0
    screening_crops: int = 0
    screening_content_claims: int = 0
    screening_images: int = 0
    terminal_tasks: int = 0
    failed_farms: int = 0
    last_farm_id: int = 0
    exhausted: bool = True

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


async def _delete_batch(
    db: AsyncSession,
    *,
    table: type[Any],
    id_column: InstrumentedAttribute[int],
    candidates: Select[tuple[int]],
    batch_size: int,
) -> int:
    """Delete a single locked, bounded cohort; never drain a backlog."""
    candidate_ids = list(
        (
            await db.execute(
                candidates.order_by(id_column).limit(batch_size).with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if not candidate_ids:
        return 0
    removed = await db.execute(
        delete(table).where(id_column.in_(candidate_ids)).returning(id_column)
    )
    return len(removed.scalars().all())


def _merge_committed(summary: RetentionSummary, cohort: RetentionSummary) -> None:
    for field in fields(RetentionSummary):
        if field.name not in {"last_farm_id", "exhausted", "failed_farms"}:
            setattr(summary, field.name, getattr(summary, field.name) + getattr(cohort, field.name))


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


async def run_retention_sweep(
    db: AsyncSession, settings: Settings, *, after_farm_id: int = 0
) -> RetentionSummary:
    """Process a bounded distinct-farm page with separately committed cohorts.

    Persist ``last_farm_id`` across loop ticks and wrap to zero only when
    ``exhausted`` is true. A farm is advanced even on failure; a later wrap
    retries it without starving higher IDs. Backlogged farms get another
    finite budget on each complete traversal.
    """
    batch_size = settings.retention_delete_batch_size
    if not 1 <= batch_size <= 10_000:
        raise ValueError("retention_delete_batch_size must be between 1 and 10000")
    if after_farm_id < 0:
        raise ValueError("after_farm_id must be nonnegative")
    screening_cutoff = utcnow() - timedelta(days=settings.retention_screening_days)
    task_cutoff = utcnow() - timedelta(days=settings.retention_terminal_task_days)
    candidates = union(
        select(ScreeningImage.farm_id).where(ScreeningImage.created_at < screening_cutoff),
        select(Task.farm_id).where(_terminal_task_clause(task_cutoff)),
    ).subquery()
    farm_ids = list(
        (
            await db.execute(
                select(candidates.c.farm_id)
                .where(candidates.c.farm_id > after_farm_id)
                .order_by(candidates.c.farm_id)
                .limit(settings.retention_farm_batch_size + 1)
            )
        ).scalars()
    )
    summary = RetentionSummary(
        last_farm_id=after_farm_id,
        exhausted=len(farm_ids) <= settings.retention_farm_batch_size,
    )
    # Release the discovery transaction before acquiring deletion-cohort locks.
    await db.commit()
    for farm_id in farm_ids[: settings.retention_farm_batch_size]:
        summary.last_farm_id = farm_id
        for _ in range(settings.retention_max_batches_per_farm):
            cohort = RetentionSummary()
            try:
                # SET LOCAL affects only this transaction, including FK cascades.
                await db.execute(
                    text("SELECT set_config('lock_timeout', :value, true)"),
                    {"value": str(settings.retention_lock_timeout_ms)},
                )
                await db.execute(
                    text("SELECT set_config('statement_timeout', :value, true)"),
                    {"value": str(settings.retention_statement_timeout_ms)},
                )
                await _sweep_farm(
                    db,
                    farm_id=farm_id,
                    screening_cutoff=screening_cutoff,
                    task_cutoff=task_cutoff,
                    batch_size=batch_size,
                    summary=cohort,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                summary.failed_farms += 1
                logger.exception("retention cohort failed for farm_id=%s; skipping it", farm_id)
                break
            _merge_committed(summary, cohort)
            if cohort.total_deleted == 0:
                break
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
    """Delete the complete dependent chain of a bounded, locked root set.

    Children are deleted directly without SKIP LOCKED. A locked child causes
    the transaction to fail within the configured lock timeout, so deleting
    a parent cannot silently wait on a child that an earlier probe skipped.
    Eligibility follows root-image age, including recently re-screened runs.
    """
    old_image_ids = list(
        (
            await db.execute(
                select(ScreeningImage.id)
                .where(
                    ScreeningImage.farm_id == farm_id,
                    ScreeningImage.created_at < screening_cutoff,
                    ScreeningImage.status != "PROCESSING",
                )
                .order_by(ScreeningImage.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
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
    summary.screening_findings += await _delete_chain_rows(
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
    )
    summary.screening_runs += await _delete_chain_rows(
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
    )
    summary.screening_crops += await _delete_chain_rows(
        db,
        table=ScreeningCrop,
        id_column=ScreeningCrop.id,
        candidates=select(ScreeningCrop.id).where(
            ScreeningCrop.farm_id == farm_id,
            ScreeningCrop.image_id.in_(old_image_ids),
        ),
    )
    summary.screening_content_claims += await _delete_chain_rows(
        db,
        table=ScreeningContentClaim,
        id_column=ScreeningContentClaim.id,
        candidates=select(ScreeningContentClaim.id).where(
            ScreeningContentClaim.farm_id == farm_id,
            ScreeningContentClaim.image_id.in_(old_image_ids),
        ),
    )
    summary.screening_images += await _delete_chain_rows(
        db,
        table=ScreeningImage,
        id_column=ScreeningImage.id,
        candidates=select(ScreeningImage.id).where(
            ScreeningImage.farm_id == farm_id, ScreeningImage.id.in_(old_image_ids)
        ),
    )
    summary.terminal_tasks += await _delete_batch(
        db,
        table=Task,
        id_column=Task.id,
        candidates=select(Task.id).where(
            Task.farm_id == farm_id,
            _terminal_task_clause(task_cutoff),
        ),
        batch_size=batch_size,
    )


async def _delete_chain_rows(
    db: AsyncSession,
    *,
    table: type[Any],
    id_column: InstrumentedAttribute[int],
    candidates: Select[tuple[int]],
) -> int:
    """Delete descendants of the already bounded root-image cohort."""
    removed = await db.execute(delete(table).where(id_column.in_(candidates)))
    # Descendant counts can exceed the bounded root count. Do not transfer
    # every deleted descendant ID merely to report an aggregate.
    return cast(CursorResult[Any], removed).rowcount
