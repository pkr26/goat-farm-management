"""Advance a bounded farm sweep only after its work finishes successfully.

The checkpoint transaction owns a separate session from the per-farm work.
Business commits therefore cannot release its lock or partially advance the
checkpoint. A crash can repeat a page; existing per-farm fact deduplication
makes that retry safe. Farm failures isolated by the page worker still count
as visited, and are retried when the cursor wraps.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.maintenance import MaintenanceProgress
from ..utils import utcnow

type MaintenanceJob = Literal["cadence", "notification_alerts"]

# The two-int advisory namespace is independent of per-farm duty locks.
# Transaction scope releases it on commit, rollback, cancellation or exit.
_LOCK_NAMESPACE = 0x48455244
_JOB_LOCK_KEYS: dict[MaintenanceJob, int] = {"cadence": 1, "notification_alerts": 2}


@dataclass(frozen=True)
class MaintenancePage:
    processed: int
    after_farm_id: int
    exhausted: bool


async def run_maintenance_page(
    db: AsyncSession,
    job: MaintenanceJob,
    work: Callable[[int], Awaitable[MaintenancePage]],
    *,
    sweep_hour: datetime | None = None,
) -> MaintenancePage | None:
    """Run one page, or return None if another scheduler owns it/already ran it.

    ``work`` must use independent business sessions, and the caller supplies
    a fresh checkpoint session. The transaction spans exactly one bounded
    page. The nonblocking advisory lock also serializes checkpoint creation;
    the row lock protects the saved position. No cursor state lives solely in
    the scheduler process, so a restart resumes at the last completed page.
    """
    if (job == "notification_alerts") != (sweep_hour is not None):
        raise ValueError("only notification_alerts requires a sweep_hour")
    if sweep_hour is not None:
        if sweep_hour.tzinfo is None or sweep_hour.utcoffset() is None:
            raise ValueError("sweep_hour must be timezone-aware")
        sweep_hour = sweep_hour.astimezone(UTC)
        if sweep_hour != sweep_hour.replace(minute=0, second=0, microsecond=0):
            raise ValueError("sweep_hour must identify the beginning of an hour")

    async with db.begin():
        acquired = (
            await db.execute(
                text("SELECT pg_try_advisory_xact_lock(:namespace, :job_key)"),
                {"namespace": _LOCK_NAMESPACE, "job_key": _JOB_LOCK_KEYS[job]},
            )
        ).scalar_one()
        if not acquired:
            return None
        # The migration seeds both jobs. Recreate a missing row under the
        # same advisory exclusion rather than lose maintenance indefinitely.
        await db.execute(
            insert(MaintenanceProgress)
            .values(job_name=job, after_farm_id=0)
            .on_conflict_do_nothing(index_elements=["job_name"])
        )
        progress = (
            await db.execute(
                select(MaintenanceProgress)
                .where(MaintenanceProgress.job_name == job)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if progress is None or (sweep_hour is not None and progress.completed_hour == sweep_hour):
            return None

        page = await work(progress.after_farm_id)
        if page.processed < 0 or page.after_farm_id < progress.after_farm_id:
            raise ValueError("maintenance work returned an invalid page")
        if not page.exhausted and (
            page.processed == 0 or page.after_farm_id == progress.after_farm_id
        ):
            raise ValueError("a nonempty unfinished page must advance the farm cursor")
        progress.after_farm_id = 0 if page.exhausted else page.after_farm_id
        if sweep_hour is not None and page.exhausted:
            progress.completed_hour = sweep_hour
        progress.updated_at = utcnow().replace(tzinfo=UTC)
    return page
