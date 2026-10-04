"""Durable cleanup for raw objects behind expiring browser upload forms.

The screening worker removes a raw photo as soon as its sanitized derivative
is durable.  That deletion is intentionally only an eager privacy measure: a
browser may replay its presigned POST until the form expires and recreate the
same key.  Every image row therefore carries a second cleanup obligation due
after expiry plus clock/write slack.  This dispatcher claims obligations in
small ``SKIP LOCKED`` batches, commits a retry lease before touching S3, and
acknowledges only after ``delete_permanently`` has verified absence.

Deleting S3 bytes and acknowledging PostgreSQL cannot be one atomic
transaction.  The operation is consequently an idempotent saga: a crash after
S3 success leaves the row due again after its lease, and the next pass repeats
the verified purge before acknowledging it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import case, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...models import ScreeningImage
from ...utils import utcnow
from .s3 import (
    ScreeningStorage,
    ScreeningStorageDeleteInProgress,
    ScreeningStorageError,
)

logger = logging.getLogger(__name__)

# The upload/cleanup clocks can live on different hosts, and an S3-compatible
# service may still be committing the final request as its policy expires.
# This is deliberately the same one-hour safety margin used by the abandoned
# PENDING-row sweep.
RAW_UPLOAD_CLEANUP_SLACK = dt.timedelta(hours=1)
MAX_PRESIGN_EXPIRY_SECONDS = 86_400

# Claim leases also implement capped exponential retry.  They are independent
# of ``MAX_SCREENING_ATTEMPTS``: privacy cleanup must keep retrying forever,
# even for terminal, invalid, duplicate, oversized, or attempt-exhausted rows.
RAW_CLEANUP_RETRY_BASE = dt.timedelta(minutes=5)
RAW_CLEANUP_RETRY_CAP = dt.timedelta(hours=6)
# One full version page was removed successfully. Give other due keys a turn,
# then continue promptly without treating finite progress as an outage.
RAW_CLEANUP_PROGRESS_RETRY = dt.timedelta(seconds=5)
MAX_RAW_CLEANUP_BATCH_SIZE = 500
MAX_RAW_CLEANUP_ATTEMPTS_COUNTER = 9_223_372_036_854_775_807

_PURGE_FAILED = "raw object permanent purge failed"
_BUCKET_MISMATCH = "raw object bucket is not the configured screening bucket"


@dataclass(frozen=True, slots=True)
class RawCleanupClaim:
    image_id: int
    farm_id: int
    s3_bucket: str
    s3_key: str
    attempt: int


@dataclass(slots=True)
class RawCleanupSummary:
    claimed: int = 0
    completed: int = 0
    in_progress: int = 0
    failed: int = 0
    acknowledgement_failed: int = 0


def raw_cleanup_deadline(issued_at: dt.datetime, expiry_seconds: int) -> dt.datetime:
    """Return the earliest safe final-purge instant for one issued form."""
    if not 1 <= expiry_seconds <= MAX_PRESIGN_EXPIRY_SECONDS:
        raise ValueError(f"expiry_seconds must be between 1 and {MAX_PRESIGN_EXPIRY_SECONDS}")
    return issued_at + dt.timedelta(seconds=expiry_seconds) + RAW_UPLOAD_CLEANUP_SLACK


def _retry_delay(attempt: int) -> dt.timedelta:
    # Bound the exponent before shifting so corrupt/ancient retry counts
    # cannot allocate an enormous Python integer. Seven doublings already
    # exceed the six-hour cap from the five-minute base.
    multiplier = 1 << min(max(attempt - 1, 0), 7)
    return min(RAW_CLEANUP_RETRY_BASE * multiplier, RAW_CLEANUP_RETRY_CAP)


async def claim_raw_cleanup_batch(
    db: AsyncSession,
    *,
    batch_size: int,
    now: dt.datetime | None = None,
) -> list[RawCleanupClaim]:
    """Lease one finite due-time page; caller commits before object I/O."""
    if not 1 <= batch_size <= MAX_RAW_CLEANUP_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_RAW_CLEANUP_BATCH_SIZE}")
    claim_time = now or utcnow()
    candidates = (
        select(ScreeningImage.id)
        .where(
            ScreeningImage.raw_cleanup_completed_at.is_(None),
            ScreeningImage.raw_cleanup_next_attempt_at.is_not(None),
            ScreeningImage.raw_cleanup_next_attempt_at <= claim_time,
            ScreeningImage.retention_tombstoned_at.is_(None),
        )
        .order_by(ScreeningImage.raw_cleanup_next_attempt_at, ScreeningImage.id)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .cte("raw_cleanup_candidates")
    )
    old_attempts = ScreeningImage.raw_cleanup_attempts
    next_attempt = case(
        (old_attempts <= 0, claim_time + _retry_delay(1)),
        (old_attempts == 1, claim_time + _retry_delay(2)),
        (old_attempts == 2, claim_time + _retry_delay(3)),
        (old_attempts == 3, claim_time + _retry_delay(4)),
        (old_attempts == 4, claim_time + _retry_delay(5)),
        (old_attempts == 5, claim_time + _retry_delay(6)),
        (old_attempts == 6, claim_time + _retry_delay(7)),
        else_=claim_time + RAW_CLEANUP_RETRY_CAP,
    )
    incremented_attempts = case(
        (
            old_attempts < MAX_RAW_CLEANUP_ATTEMPTS_COUNTER,
            old_attempts + 1,
        ),
        else_=old_attempts,
    )
    # One statement both consumes the lock-skipping page and commits its
    # retry lease. Explicitly preserving updated_at is essential: that column
    # is the model pipeline's PROCESSING lease/ERROR retry clock, not a generic
    # timestamp for this independent privacy saga.
    rows = (
        await db.execute(
            update(ScreeningImage)
            .where(ScreeningImage.id.in_(select(candidates.c.id)))
            .values(
                raw_cleanup_attempts=incremented_attempts,
                raw_cleanup_next_attempt_at=next_attempt,
                updated_at=ScreeningImage.updated_at,
            )
            .returning(
                ScreeningImage.id,
                ScreeningImage.farm_id,
                ScreeningImage.s3_bucket,
                ScreeningImage.s3_key,
                ScreeningImage.raw_cleanup_attempts,
            )
        )
    ).all()
    return [
        RawCleanupClaim(
            image_id=int(row.id),
            farm_id=int(row.farm_id),
            s3_bucket=str(row.s3_bucket),
            s3_key=str(row.s3_key),
            attempt=int(row.raw_cleanup_attempts),
        )
        for row in rows
    ]


async def acknowledge_raw_cleanup(
    db: AsyncSession,
    claim: RawCleanupClaim,
    *,
    completed_at: dt.datetime | None = None,
) -> bool:
    """Persist successful verified deletion without reviving removed rows."""
    result = await db.execute(
        update(ScreeningImage)
        .where(
            ScreeningImage.id == claim.image_id,
            ScreeningImage.farm_id == claim.farm_id,
            ScreeningImage.s3_bucket == claim.s3_bucket,
            ScreeningImage.s3_key == claim.s3_key,
            ScreeningImage.raw_cleanup_completed_at.is_(None),
        )
        .values(
            raw_cleanup_completed_at=completed_at or utcnow(),
            raw_cleanup_next_attempt_at=None,
            raw_cleanup_last_error=None,
            updated_at=ScreeningImage.updated_at,
        )
    )
    return cast(CursorResult[Any], result).rowcount > 0


async def record_raw_cleanup_failure(
    db: AsyncSession,
    claim: RawCleanupClaim,
    *,
    reason: str,
) -> bool:
    """Record a bounded diagnostic; the committed claim already set retry."""
    if reason not in {_PURGE_FAILED, _BUCKET_MISMATCH}:
        raise ValueError("raw cleanup failure reason must be a fixed safe value")
    result = await db.execute(
        update(ScreeningImage)
        .where(
            ScreeningImage.id == claim.image_id,
            ScreeningImage.farm_id == claim.farm_id,
            ScreeningImage.s3_bucket == claim.s3_bucket,
            ScreeningImage.s3_key == claim.s3_key,
            ScreeningImage.raw_cleanup_completed_at.is_(None),
        )
        .values(
            raw_cleanup_last_error=reason,
            updated_at=ScreeningImage.updated_at,
        )
    )
    return cast(CursorResult[Any], result).rowcount > 0


async def record_raw_cleanup_progress(
    db: AsyncSession,
    claim: RawCleanupClaim,
    *,
    retry_at: dt.datetime,
) -> bool:
    """Checkpoint one successful bounded page without escalating backoff."""
    result = await db.execute(
        update(ScreeningImage)
        .where(
            ScreeningImage.id == claim.image_id,
            ScreeningImage.farm_id == claim.farm_id,
            ScreeningImage.raw_cleanup_completed_at.is_(None),
            # Do not undo a newer dispatcher's claim if an object-store call
            # somehow outlived its lease. Permanent deletion is idempotent;
            # the newer durable state wins.
            ScreeningImage.raw_cleanup_attempts == claim.attempt,
        )
        .values(
            # This was successful finite progress, not a failed attempt. Put
            # the counter back so a later real outage starts at the backoff
            # it would have had without a deep version history.
            raw_cleanup_attempts=case(
                (
                    ScreeningImage.raw_cleanup_attempts > 0,
                    ScreeningImage.raw_cleanup_attempts - 1,
                ),
                else_=0,
            ),
            raw_cleanup_next_attempt_at=retry_at,
            raw_cleanup_last_error=None,
            updated_at=ScreeningImage.updated_at,
        )
    )
    return cast(CursorResult[Any], result).rowcount > 0


async def run_raw_cleanup_batch(
    session_factory: async_sessionmaker[AsyncSession],
    storage: ScreeningStorage,
    *,
    batch_size: int,
    now: dt.datetime | None = None,
) -> RawCleanupSummary:
    """Claim, permanently purge, and acknowledge one bounded cleanup page.

    Each database phase uses a fresh transaction.  In particular no row lock
    or pooled connection is held while the synchronous S3 client runs in its
    worker thread.
    """
    claim_time = now or utcnow()
    async with session_factory() as claim_db:
        claims = await claim_raw_cleanup_batch(
            claim_db,
            batch_size=batch_size,
            now=claim_time,
        )
        await claim_db.commit()

    summary = RawCleanupSummary(claimed=len(claims))
    for claim in claims:
        failure_reason: str | None = None
        deletion_in_progress = False
        try:
            if storage.bucket != claim.s3_bucket:
                failure_reason = _BUCKET_MISMATCH
            else:
                await asyncio.to_thread(storage.delete_permanently, [claim.s3_key])
        except ScreeningStorageDeleteInProgress:
            deletion_in_progress = True
        except ScreeningStorageError:
            failure_reason = _PURGE_FAILED
            # Provider exceptions can contain endpoint topology and the
            # object key itself embeds tenant/date context. Keep both out of
            # logs; the fixed code plus durable image/farm ids are enough to
            # correlate an operator investigation.
            logger.warning(
                "raw screening cleanup purge failed farm=%s image=%s reason=PURGE_FAILED",
                claim.farm_id,
                claim.image_id,
            )
        except Exception:
            # Enforce the same redaction boundary even if an incompatible
            # storage implementation violates the documented exception
            # contract. The durable retry makes this fail closed.
            failure_reason = _PURGE_FAILED
            logger.error(
                "raw screening cleanup purge raised an unexpected error "
                "farm=%s image=%s reason=PURGE_FAILED",
                claim.farm_id,
                claim.image_id,
            )

        if deletion_in_progress:
            summary.in_progress += 1
            try:
                async with session_factory() as progress_db:
                    progress_recorded = await record_raw_cleanup_progress(
                        progress_db,
                        claim,
                        retry_at=max(utcnow(), claim_time) + RAW_CLEANUP_PROGRESS_RETRY,
                    )
                    await progress_db.commit()
            except Exception:
                # The committed claim lease remains a safe (slower) fallback
                # if this short-continuation checkpoint cannot be stored.
                logger.error(
                    "could not persist raw cleanup progress farm=%s image=%s",
                    claim.farm_id,
                    claim.image_id,
                )
                summary.acknowledgement_failed += 1
            else:
                if not progress_recorded:
                    # The object store did make progress, but this stale claim
                    # did not durably install the short continuation. The
                    # original committed lease remains the retry boundary.
                    logger.warning(
                        "raw cleanup progress changed no row farm=%s image=%s; "
                        "claim lease remains authoritative",
                        claim.farm_id,
                        claim.image_id,
                    )
                    summary.acknowledgement_failed += 1
            continue

        if failure_reason is not None:
            summary.failed += 1
            try:
                async with session_factory() as failure_db:
                    failure_recorded = await record_raw_cleanup_failure(
                        failure_db,
                        claim,
                        reason=failure_reason,
                    )
                    await failure_db.commit()
            except Exception:
                # The committed lease still makes this recoverable.  Do not
                # let one unavailable acknowledgement starve the rest of the
                # already bounded page.
                logger.error(
                    "could not persist raw cleanup failure farm=%s image=%s",
                    claim.farm_id,
                    claim.image_id,
                )
                summary.acknowledgement_failed += 1
            else:
                if not failure_recorded:
                    # Do not silently claim that the diagnostic was durable:
                    # a concurrent completion/removal or unsupported identity
                    # mutation can make the compare-and-set update a no-op.
                    logger.warning(
                        "raw cleanup failure changed no row farm=%s image=%s; "
                        "claim lease remains authoritative",
                        claim.farm_id,
                        claim.image_id,
                    )
                    summary.acknowledgement_failed += 1
            continue

        try:
            async with session_factory() as acknowledgement_db:
                acknowledged = await acknowledge_raw_cleanup(
                    acknowledgement_db,
                    claim,
                    # ``claim_time`` is normally the real current instant;
                    # accepting an injected clock makes deterministic tests
                    # and maintenance replay tooling safe without ever
                    # acknowledging before the due boundary. A slow purge
                    # still records its actual later completion time.
                    completed_at=max(utcnow(), claim_time),
                )
                await acknowledgement_db.commit()
        except Exception:
            # Object deletion succeeded but the durable acknowledgement did
            # not.  This is the critical saga boundary: leave the obligation
            # leased, then repeat the idempotent purge after backoff.
            logger.error(
                "could not acknowledge raw cleanup farm=%s image=%s; purge will retry",
                claim.farm_id,
                claim.image_id,
            )
            summary.acknowledgement_failed += 1
            continue
        if not acknowledged:
            # A concurrent retention finalizer may already have removed the
            # row, or its identity may have changed outside the supported
            # contract. In neither case did this dispatcher persist its own
            # acknowledgement, so never report the obligation as completed.
            # If the row still exists, the committed claim lease makes it
            # eligible for another verified purge; if it was removed, the
            # retention saga already owns/finalized the evidence chain.
            logger.warning(
                "raw cleanup acknowledgement changed no row farm=%s image=%s; "
                "purge will retry if the obligation remains",
                claim.farm_id,
                claim.image_id,
            )
            summary.acknowledgement_failed += 1
            continue
        summary.completed += 1
    return summary


__all__ = [
    "MAX_PRESIGN_EXPIRY_SECONDS",
    "MAX_RAW_CLEANUP_ATTEMPTS_COUNTER",
    "MAX_RAW_CLEANUP_BATCH_SIZE",
    "RAW_CLEANUP_PROGRESS_RETRY",
    "RAW_CLEANUP_RETRY_BASE",
    "RAW_CLEANUP_RETRY_CAP",
    "RAW_UPLOAD_CLEANUP_SLACK",
    "RawCleanupClaim",
    "RawCleanupSummary",
    "acknowledge_raw_cleanup",
    "claim_raw_cleanup_batch",
    "raw_cleanup_deadline",
    "record_raw_cleanup_failure",
    "record_raw_cleanup_progress",
    "run_raw_cleanup_batch",
]
