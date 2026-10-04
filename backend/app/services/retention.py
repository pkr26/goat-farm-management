"""Bounded, version-aware data retention with a durable deletion saga.

Screening chains and terminal tasks are removed in finite root cohorts.
Every cohort commits separately, SQL discovery keyset-pages distinct farm IDs,
and a per-farm budget prevents a large tenant from monopolizing a pass. Counts
reflect only committed work. Child lock contention aborts a cohort within a
short lock timeout; skipped child rows are never left for an unbounded cascade.

Deliberate scope boundaries:

* ``weight_records`` / ``feeding_records`` are explicitly OUT: the growth
  curves and feed analytics ARE their long-term value (ITEM 9.1 lists them
  only under a future archive-schema/partition design, not deletion).
* Screening deletion is a three-transaction saga: commit an exact-key
  tombstone, idempotently remove and verify objects, then remove the database
  chain. A crash at any boundary leaves enough durable state to retry safely.
* Empty aged walkthrough batches, expired call-budget receipts, and settled
  notification ledgers have explicit finite policies instead of growing
  forever.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Select, and_, delete, exists, func, or_, select, text, union, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from ..core.config import Settings
from ..models import (
    NotificationLog,
    NotificationOutbox,
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
from ..utils import utcnow
from .screening.s3 import (
    ScreeningStorage,
    ScreeningStorageDeleteInProgress,
    ScreeningStorageError,
)

logger = logging.getLogger(__name__)

_DELETION_PENDING = "PENDING"
_DELETION_OBJECTS_DELETED = "OBJECTS_DELETED"
# Object-store failures use a persisted, capped exponential due time. A
# permanently broken bucket therefore does not receive one request on every
# retention poll, and process restarts cannot reset the backoff. Successfully
# deleting one bounded version page is normal progress and stays immediately
# due. The due-time ordering plus finite dispatch budget gives other intents a
# turn while allowing one daily sweep to finish a multi-page key.
_OBJECT_DELETE_RETRY_BASE = timedelta(minutes=5)
_OBJECT_DELETE_RETRY_MAX = timedelta(hours=24)
_OBJECT_DELETE_PROGRESS_RETRY = timedelta(0)
# Two-key PostgreSQL advisory namespace dedicated to per-farm retention
# planning. It serializes the outstanding-count/cap decision across replicas;
# namespace 4719 is distinct from task/intake/quota/budget locks.
_RETENTION_PLANNER_LOCK_NAMESPACE = 4719
# The application creates at most one raw key, one normalized image key and
# 20 crop keys. Keep a little migration headroom, but reject a corrupt/manual
# manifest before an attacker-controlled list can stretch one dispatcher
# transaction without bound.
_MAX_SCREENING_MANIFEST_KEYS = 32


class _ObjectDeletionFailed(Exception):
    """Internal control flow carrying a stable, non-secret failure code."""

    def __init__(self, intent_id: int, error_code: str) -> None:
        super().__init__(error_code)
        self.intent_id = intent_id
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class _ManifestDispatch:
    intent_id: int
    complete: bool


def _object_failure_retry_delay(consecutive_failures: int) -> timedelta:
    """Return a capped exponential delay for a persisted failure count."""
    exponent = max(consecutive_failures - 1, 0)
    # Avoid constructing an enormous integer for a corrupt/manual counter;
    # 2**9 already exceeds the 24-hour cap from a five-minute base.
    multiplier = 1 << min(exponent, 9)
    return min(_OBJECT_DELETE_RETRY_BASE * multiplier, _OBJECT_DELETE_RETRY_MAX)


@dataclass
class RetentionSummary:
    """Rows deleted by one sweep, by table (the maintenance-loop metric)."""

    screening_findings: int = 0
    screening_runs: int = 0
    screening_crops: int = 0
    screening_content_claims: int = 0
    screening_images: int = 0
    screening_batches: int = 0
    screening_call_reservations: int = 0
    screening_daily_budgets: int = 0
    notification_log: int = 0
    notification_outbox: int = 0
    terminal_tasks: int = 0
    screening_deletion_intents_staged: int = 0
    screening_object_deletions_verified: int = 0
    screening_deletion_intents_requeued: int = 0
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
            + self.screening_batches
            + self.screening_call_reservations
            + self.screening_daily_budgets
            + self.notification_log
            + self.notification_outbox
            + self.terminal_tasks
        )

    @property
    def total_progress(self) -> int:
        """Committed deletion rows plus committed saga state transitions."""
        return (
            self.total_deleted
            + self.screening_deletion_intents_staged
            + self.screening_object_deletions_verified
            + self.screening_deletion_intents_requeued
        )


async def _delete_batch(
    db: AsyncSession,
    *,
    table: type[Any],
    id_column: InstrumentedAttribute[Any],
    candidates: Select[tuple[Any]],
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
    screening_batch_cutoff = utcnow() - timedelta(days=settings.retention_screening_batch_days)
    screening_budget_cutoff = utcnow().date() - timedelta(
        days=settings.retention_screening_budget_days
    )
    notification_cutoff = utcnow() - timedelta(days=settings.retention_notification_days)
    task_cutoff = utcnow() - timedelta(days=settings.retention_terminal_task_days)
    candidates = union(
        select(ScreeningImage.farm_id).where(ScreeningImage.created_at < screening_cutoff),
        select(ScreeningBatch.farm_id).where(
            ScreeningBatch.created_at < screening_batch_cutoff,
            ~exists().where(
                ScreeningImage.farm_id == ScreeningBatch.farm_id,
                ScreeningImage.batch_id == ScreeningBatch.id,
            ),
        ),
        select(ScreeningDailyBudget.farm_id).where(
            ScreeningDailyBudget.local_date < screening_budget_cutoff
        ),
        select(NotificationLog.farm_id).where(NotificationLog.created_at < notification_cutoff),
        select(NotificationOutbox.farm_id).where(
            NotificationOutbox.completed_at.is_not(None),
            NotificationOutbox.completed_at < notification_cutoff,
        ),
        select(Task.farm_id).where(_terminal_task_clause(task_cutoff)),
        # A policy/cutoff change must never strand an already committed
        # deletion saga. Pending and verified intents remain discoverable
        # until relational finalization removes them.
        select(ScreeningRetentionDeletion.farm_id),
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
            staged = RetentionSummary()
            try:
                await _set_retention_timeouts(db, settings)
                await _sweep_farm(
                    db,
                    farm_id=farm_id,
                    screening_cutoff=screening_cutoff,
                    screening_batch_cutoff=screening_batch_cutoff,
                    screening_budget_cutoff=screening_budget_cutoff,
                    notification_cutoff=notification_cutoff,
                    task_cutoff=task_cutoff,
                    batch_size=batch_size,
                    settings=settings,
                    summary=staged,
                )
                # This is the critical saga boundary: the exact object-key
                # manifest is durable before any irreversible S3 operation.
                await db.commit()
            except Exception as exc:
                await db.rollback()
                summary.failed_farms += 1
                # SQLAlchemy exception strings include bound parameters unless
                # the engine is globally configured with hide_parameters.
                # Planning writes the exact JSON key manifest, so a traceback
                # here can disclose tenant/date/object metadata. Keep only the
                # fixed tenant identifier and exception class in this privacy
                # boundary; the durable rows make the failure reproducible.
                logger.error(
                    "retention planning cohort failed for farm_id=%s error_type=%s; skipping it",
                    farm_id,
                    type(exc).__name__,
                )
                break
            _merge_committed(summary, staged)

            # Dispatch one manifest at a time. Each successful acknowledgement
            # commits separately, so a later object/SQL failure cannot make
            # already verified work ambiguous. Failed intents are skipped for
            # the rest of this pass but remain PENDING for a future retry.
            failed_intent_ids: set[int] = set()
            object_failure = False
            fatal_failure = False
            object_progress = 0
            for _dispatch_index in range(batch_size):
                try:
                    await _set_retention_timeouts(db, settings)
                    dispatch = await _delete_one_pending_manifest(
                        db,
                        farm_id=farm_id,
                        settings=settings,
                        excluded_intent_ids=failed_intent_ids,
                    )
                    await db.commit()
                except _ObjectDeletionFailed as exc:
                    await db.rollback()
                    failed_intent_ids.add(exc.intent_id)
                    object_failure = True
                    logger.warning(
                        "retention object deletion failed for farm_id=%s intent_id=%s code=%s",
                        farm_id,
                        exc.intent_id,
                        exc.error_code,
                    )
                    try:
                        await _record_object_deletion_failure(
                            db,
                            intent_id=exc.intent_id,
                            error_code=exc.error_code,
                        )
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        fatal_failure = True
                        # Do not emit a traceback here: this exception occurs
                        # while handling a sanitized storage failure, and
                        # Python exception context can otherwise render the
                        # original provider/key payload through the chain.
                        logger.error(
                            "retention could not persist object failure state for "
                            "farm_id=%s intent_id=%s code=FAILURE_STATE_ACK_FAILED",
                            farm_id,
                            exc.intent_id,
                        )
                        break
                    continue
                except Exception as exc:
                    # This includes a DB commit failure *after* S3 succeeded.
                    # Rollback leaves the already committed intent PENDING; an
                    # idempotent future dispatch verifies absence and acks it.
                    await db.rollback()
                    fatal_failure = True
                    # UPDATE parameters contain the exact per-key checkpoints;
                    # never stringify a database exception on this path.
                    logger.error(
                        "retention object acknowledgement failed for farm_id=%s; "
                        "error_type=%s intent remains retryable",
                        farm_id,
                        type(exc).__name__,
                    )
                    break
                if dispatch is None:
                    break
                object_progress += 1
                summary.screening_object_deletions_verified += int(dispatch.complete)

            finalized = RetentionSummary()
            if not fatal_failure:
                try:
                    await _set_retention_timeouts(db, settings)
                    await _finalize_screening_deletions(
                        db,
                        farm_id=farm_id,
                        batch_size=batch_size,
                        summary=finalized,
                    )
                    await db.commit()
                except Exception as exc:
                    # OBJECTS_DELETED was committed in an earlier transaction.
                    # A SQL/commit rollback here leaves the complete relational
                    # chain and its terminal intent ready for finalization, and
                    # never requires another object-store call.
                    await db.rollback()
                    fatal_failure = True
                    # Requeue updates can bind object-key arrays. As above,
                    # retain the exception class but not its parameter-bearing
                    # string or traceback.
                    logger.error(
                        "retention relational finalization failed for farm_id=%s; "
                        "error_type=%s verified intent remains retryable",
                        farm_id,
                        type(exc).__name__,
                    )
                else:
                    _merge_committed(summary, finalized)

            if fatal_failure or object_failure:
                summary.failed_farms += 1
                break
            if staged.total_progress + object_progress + finalized.total_progress == 0:
                break
    return summary


async def _set_retention_timeouts(db: AsyncSession, settings: Settings) -> None:
    """Apply bounded waits to the current transaction only."""
    await db.execute(
        text("SELECT set_config('lock_timeout', :value, true)"),
        {"value": str(settings.retention_lock_timeout_ms)},
    )
    await db.execute(
        text("SELECT set_config('statement_timeout', :value, true)"),
        {"value": str(settings.retention_statement_timeout_ms)},
    )


def _validated_manifest_keys(intent: ScreeningRetentionDeletion) -> set[str]:
    """Return a trusted exact-key manifest or fail without touching storage."""
    raw_keys = intent.object_keys
    if (
        not isinstance(raw_keys, list)
        or not raw_keys
        or len(raw_keys) > _MAX_SCREENING_MANIFEST_KEYS
    ):
        raise _ObjectDeletionFailed(intent.id, "INVALID_MANIFEST")
    keys: set[str] = set()
    for key in raw_keys:
        # S3 permits spaces (including at either edge) in an object key. Keep
        # the exact durable value; trimming here would strand legal legacy
        # evidence or, worse, delete a different object.
        if not isinstance(key, str) or not key or len(key) > 1_024:
            raise _ObjectDeletionFailed(intent.id, "INVALID_MANIFEST")
        keys.add(key)
    if not keys:
        raise _ObjectDeletionFailed(intent.id, "INVALID_MANIFEST")
    return keys


def _validated_disposition_keys(
    intent: ScreeningRetentionDeletion,
    *,
    field_name: str,
    manifest_keys: set[str],
) -> set[str]:
    """Validate a durable per-key checkpoint as a manifest subset."""
    raw_keys = getattr(intent, field_name)
    if not isinstance(raw_keys, list):
        raise _ObjectDeletionFailed(intent.id, "INVALID_MANIFEST")
    keys: set[str] = set()
    for key in raw_keys:
        if not isinstance(key, str) or key not in manifest_keys:
            raise _ObjectDeletionFailed(intent.id, "INVALID_MANIFEST")
        keys.add(key)
    return keys


async def _current_image_object_keys(db: AsyncSession, *, image: ScreeningImage) -> set[str]:
    crop_keys = (
        await db.execute(
            select(ScreeningCrop.normalized_key).where(
                ScreeningCrop.farm_id == image.farm_id,
                ScreeningCrop.image_id == image.id,
                ScreeningCrop.normalized_key.is_not(None),
            )
        )
    ).scalars()
    return {
        key
        for key in (image.s3_key, image.normalized_key, *crop_keys)
        if isinstance(key, str) and key
    }


def _image_has_no_retention_tombstone() -> ColumnElement[bool]:
    """The same-row fence updated atomically with the deletion intent.

    A correlated ``NOT EXISTS(intent)`` is insufficient for a waiter whose
    READ COMMITTED statement took its snapshot before the planner committed:
    it can wait on the image lock and then return the old-snapshot row.  The
    image-column predicate is rechecked by PostgreSQL EvalPlanQual after that
    wait because the planner updates this exact tuple.
    """
    return ScreeningImage.retention_tombstoned_at.is_(None)


async def _keys_referenced_by_live_chains(
    db: AsyncSession,
    *,
    image_id: int,
    s3_bucket: str,
    keys: set[str],
) -> set[str]:
    """Find bucket-global keys still owned by a non-tombstoned image.

    Raw keys are globally unique, but normalized/crop derivatives historically
    used content-derived names and can be shared, including by malformed
    legacy imports across farms. Object keys live in one bucket-global
    namespace, so this deliberately has no farm filter. A purge may
    terminally preserve such a key; the last owning chain removes it.
    """
    if not keys:
        return set()
    live_image = _image_has_no_retention_tombstone()
    referenced = union(
        select(ScreeningImage.s3_key.label("object_key")).where(
            ScreeningImage.id != image_id,
            ScreeningImage.s3_bucket == s3_bucket,
            ScreeningImage.s3_key.in_(keys),
            live_image,
        ),
        select(ScreeningImage.normalized_key.label("object_key")).where(
            ScreeningImage.id != image_id,
            ScreeningImage.s3_bucket == s3_bucket,
            ScreeningImage.normalized_key.in_(keys),
            live_image,
        ),
        select(ScreeningCrop.normalized_key.label("object_key"))
        .join(
            ScreeningImage,
            and_(
                ScreeningImage.farm_id == ScreeningCrop.farm_id,
                ScreeningImage.id == ScreeningCrop.image_id,
            ),
        )
        .where(
            ScreeningImage.id != image_id,
            ScreeningImage.s3_bucket == s3_bucket,
            ScreeningCrop.normalized_key.in_(keys),
            live_image,
        ),
    ).subquery()
    return {
        key
        for key in (await db.execute(select(referenced.c.object_key))).scalars()
        if isinstance(key, str)
    }


async def _delete_one_pending_manifest(
    db: AsyncSession,
    *,
    farm_id: int,
    settings: Settings,
    excluded_intent_ids: set[int],
) -> _ManifestDispatch | None:
    """Delete/verify one key and durably advance its manifest cursor."""
    now = utcnow()
    query = (
        select(ScreeningRetentionDeletion)
        .where(
            ScreeningRetentionDeletion.farm_id == farm_id,
            ScreeningRetentionDeletion.status == _DELETION_PENDING,
            ScreeningRetentionDeletion.next_attempt_at <= now,
        )
        # Persisted due time is the fairness cursor. A poisoned low id cannot
        # monopolize a bounded pass, and restart does not reset its backoff.
        .order_by(
            ScreeningRetentionDeletion.next_attempt_at,
            ScreeningRetentionDeletion.id,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if excluded_intent_ids:
        query = query.where(ScreeningRetentionDeletion.id.not_in(excluded_intent_ids))
    intent = (await db.execute(query)).scalar_one_or_none()
    if intent is None:
        return None

    keys = _validated_manifest_keys(intent)
    deleted = _validated_disposition_keys(
        intent,
        field_name="deleted_keys",
        manifest_keys=keys,
    )
    previously_preserved = _validated_disposition_keys(
        intent,
        field_name="preserved_keys",
        manifest_keys=keys,
    )
    if deleted & previously_preserved:
        raise _ObjectDeletionFailed(intent.id, "INVALID_MANIFEST")
    configured_bucket = settings.s3_bucket
    if not configured_bucket or intent.s3_bucket != configured_bucket:
        raise _ObjectDeletionFailed(intent.id, "CONFIGURED_BUCKET_MISMATCH")
    remaining = keys - deleted - previously_preserved
    newly_preserved = await _keys_referenced_by_live_chains(
        db,
        image_id=intent.image_id,
        s3_bucket=intent.s3_bucket,
        keys=remaining,
    )
    preserved = previously_preserved | newly_preserved
    delete_keys = sorted(remaining - newly_preserved)
    if delete_keys:
        delete_key = delete_keys[0]
        try:
            # One exact key per database transaction bounds the lock duration.
            # This primitive itself page-bounds version history and performs a
            # post-delete absence check. A crash after it returns but before
            # the following checkpoint commit is safe: PENDING retries it.
            await asyncio.to_thread(
                ScreeningStorage(settings).delete_permanently,
                [delete_key],
            )
        except ScreeningStorageDeleteInProgress:
            # Removing one complete version page is expected finite progress,
            # not an outage. Persist an immediately due continuation without
            # poisoning observability or escalating operational backoff; the
            # finite dispatch loop remains the global work bound.
            attempted_at = utcnow()
            intent.attempt_count += 1
            intent.failure_count = 0
            intent.last_attempt_at = attempted_at
            intent.next_attempt_at = attempted_at + _OBJECT_DELETE_PROGRESS_RETRY
            intent.last_error = None
            intent.preserved_keys = sorted(preserved)
            intent.deleted_keys = sorted(deleted)
            intent.updated_at = attempted_at
            await db.flush()
            return _ManifestDispatch(intent_id=intent.id, complete=False)
        except ScreeningStorageError:
            # Provider exceptions frequently carry exact keys, endpoints, or
            # response bodies. The control-flow exception deliberately has no
            # cause/context visible to outer persistence-error logging.
            raise _ObjectDeletionFailed(intent.id, "OBJECT_STORE_DELETE_FAILED") from None
        deleted.add(delete_key)

    attempted_at = utcnow()
    if delete_keys:
        intent.attempt_count += 1
        intent.failure_count = 0
        intent.last_attempt_at = attempted_at
    intent.last_error = None
    intent.preserved_keys = sorted(preserved)
    intent.deleted_keys = sorted(deleted)
    complete = deleted | preserved == keys
    if complete:
        intent.status = _DELETION_OBJECTS_DELETED
        intent.objects_deleted_at = attempted_at
        intent.next_attempt_at = None
    else:
        intent.next_attempt_at = attempted_at
    intent.updated_at = attempted_at
    await db.flush()
    return _ManifestDispatch(intent_id=intent.id, complete=complete)


async def _record_object_deletion_failure(
    db: AsyncSession, *, intent_id: int, error_code: str
) -> None:
    """Commit stable retry metadata without persisting raw provider detail."""
    intent = (
        await db.execute(
            select(ScreeningRetentionDeletion)
            .where(
                ScreeningRetentionDeletion.id == intent_id,
                ScreeningRetentionDeletion.status == _DELETION_PENDING,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if intent is None:
        # A concurrent retry may already have completed it after our rollback.
        return
    attempted_at = utcnow()
    intent.attempt_count += 1
    intent.failure_count += 1
    intent.last_attempt_at = attempted_at
    intent.next_attempt_at = attempted_at + _object_failure_retry_delay(intent.failure_count)
    intent.last_error = error_code
    intent.updated_at = attempted_at
    await db.flush()


async def _finalize_screening_deletions(
    db: AsyncSession,
    *,
    farm_id: int,
    batch_size: int,
    summary: RetentionSummary,
) -> None:
    """Remove relational chains only from durably verified manifests."""
    intents = list(
        (
            await db.execute(
                select(ScreeningRetentionDeletion)
                .where(
                    ScreeningRetentionDeletion.farm_id == farm_id,
                    ScreeningRetentionDeletion.status == _DELETION_OBJECTS_DELETED,
                )
                .order_by(ScreeningRetentionDeletion.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    finalized_image_ids: list[int] = []
    finalized_intent_ids: list[int] = []
    for intent in intents:
        image = (
            await db.execute(
                select(ScreeningImage)
                .where(
                    ScreeningImage.farm_id == intent.farm_id,
                    ScreeningImage.id == intent.image_id,
                )
                .with_for_update()
            )
        ).scalar_one()
        if image.s3_bucket != intent.s3_bucket:
            raise RuntimeError("screening retention intent bucket changed before finalization")
        manifest_keys = _validated_manifest_keys(intent)
        deleted_keys = _validated_disposition_keys(
            intent,
            field_name="deleted_keys",
            manifest_keys=manifest_keys,
        )
        current_keys = await _current_image_object_keys(db, image=image)
        preserved_keys = _validated_disposition_keys(
            intent,
            field_name="preserved_keys",
            manifest_keys=manifest_keys,
        )
        if deleted_keys & preserved_keys or deleted_keys | preserved_keys != manifest_keys:
            raise RuntimeError("screening retention intent has incomplete key dispositions")
        still_shared = await _keys_referenced_by_live_chains(
            db,
            image_id=intent.image_id,
            s3_bucket=intent.s3_bucket,
            keys=preserved_keys,
        )
        new_keys = current_keys - manifest_keys
        no_longer_shared = preserved_keys - still_shared
        if new_keys or no_longer_shared:
            # A defensively detected new reference or a formerly shared key
            # that this chain now owns alone must pass through deletion again.
            intent.object_keys = sorted(manifest_keys | current_keys)
            intent.deleted_keys = sorted(deleted_keys)
            intent.preserved_keys = sorted(still_shared)
            intent.status = _DELETION_PENDING
            intent.objects_deleted_at = None
            intent.last_error = None
            intent.failure_count = 0
            requeued_at = utcnow()
            intent.next_attempt_at = requeued_at
            intent.updated_at = requeued_at
            summary.screening_deletion_intents_requeued += 1
            continue
        finalized_image_ids.append(image.id)
        finalized_intent_ids.append(intent.id)

    if not finalized_image_ids:
        await db.flush()
        return

    old_crop_ids = select(ScreeningCrop.id).where(
        ScreeningCrop.farm_id == farm_id,
        ScreeningCrop.image_id.in_(finalized_image_ids),
    )
    old_run_ids = select(ScreeningRun.id).where(
        ScreeningRun.farm_id == farm_id,
        ScreeningRun.image_id.in_(finalized_image_ids),
    )
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
                ScreeningRun.image_id.in_(finalized_image_ids),
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
            ScreeningCrop.image_id.in_(finalized_image_ids),
        ),
    )
    summary.screening_content_claims += await _delete_chain_rows(
        db,
        table=ScreeningContentClaim,
        id_column=ScreeningContentClaim.id,
        candidates=select(ScreeningContentClaim.id).where(
            ScreeningContentClaim.farm_id == farm_id,
            ScreeningContentClaim.image_id.in_(finalized_image_ids),
        ),
    )
    # Remove the restrictive tombstone immediately before its image. Both
    # statements commit or roll back together, so the manifest is never lost
    # while its relational chain survives a failed finalization transaction.
    await db.execute(
        delete(ScreeningRetentionDeletion).where(
            ScreeningRetentionDeletion.id.in_(finalized_intent_ids)
        )
    )
    summary.screening_images += await _delete_chain_rows(
        db,
        table=ScreeningImage,
        id_column=ScreeningImage.id,
        candidates=select(ScreeningImage.id).where(
            ScreeningImage.farm_id == farm_id,
            ScreeningImage.id.in_(finalized_image_ids),
        ),
    )


async def _sweep_farm(
    db: AsyncSession,
    *,
    farm_id: int,
    screening_cutoff: datetime,
    screening_batch_cutoff: datetime,
    screening_budget_cutoff: date,
    notification_cutoff: datetime,
    task_cutoff: datetime,
    batch_size: int,
    settings: Settings,
    summary: RetentionSummary,
) -> None:
    """Commit bounded deletion intents and sweep non-object ledgers.

    Screening chains are deliberately *not* removed here. This transaction
    snapshots their complete object manifests and commits those tombstones;
    dispatch and relational finalization happen in later transactions.
    """
    # Counting unfinished intents and staging the remaining capacity must be
    # one serialized decision. Row-level SKIP LOCKED alone is insufficient:
    # two replicas can count the same backlog, lock disjoint image rows, and
    # both fill the advertised per-farm cap. The caller already installed a
    # bounded statement timeout, so a wedged peer fails this cohort rather
    # than waiting forever. The xact lock releases at this planning commit.
    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock(CAST(:namespace AS integer), CAST(:farm_id AS integer))"
        ),
        {"namespace": _RETENTION_PLANNER_LOCK_NAMESPACE, "farm_id": farm_id},
    )
    # Bound durable-but-unfinished manifests per farm. A persistent object
    # failure can otherwise make every daily pass add another full cohort and
    # grow the tombstone backlog without limit. The cap is derived from the
    # already bounded per-pass knobs: enough room for one complete farm budget,
    # but never an unbounded queue.
    outstanding_intents = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ScreeningRetentionDeletion)
                .where(ScreeningRetentionDeletion.farm_id == farm_id)
            )
        ).scalar_one()
    )
    outstanding_cap = batch_size * settings.retention_max_batches_per_farm
    staging_limit = min(batch_size, max(0, outstanding_cap - outstanding_intents))
    old_images = list(
        (
            await db.execute(
                select(ScreeningImage)
                .where(
                    ScreeningImage.farm_id == farm_id,
                    ScreeningImage.created_at < screening_cutoff,
                    ScreeningImage.status != "PROCESSING",
                    ScreeningImage.retention_tombstoned_at.is_(None),
                )
                .order_by(ScreeningImage.id)
                .limit(staging_limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    old_image_ids = [image.id for image in old_images]
    if old_images:
        crop_keys_by_image: dict[int, set[str]] = {}
        # ``.all()`` is required here (not ``.scalars()``): the manifest is
        # per image and must never accidentally merge another image's key.
        crop_key_rows = list(
            (
                await db.execute(
                    select(ScreeningCrop.image_id, ScreeningCrop.normalized_key).where(
                        ScreeningCrop.farm_id == farm_id,
                        ScreeningCrop.image_id.in_(old_image_ids),
                        ScreeningCrop.normalized_key.is_not(None),
                    )
                )
            ).all()
        )
        for image_id, key in crop_key_rows:
            if key:
                crop_keys_by_image.setdefault(image_id, set()).add(key)
        # One service clock owns every timestamp at this atomic planning
        # boundary. In particular, every newly committed intent must be due
        # to the dispatch phase of this same sweep when utcnow() is frozen in
        # a deterministic run.
        staged_at = utcnow()
        for image in old_images:
            object_keys = sorted(
                {key for key in (image.s3_key, image.normalized_key) if key}
                | crop_keys_by_image.get(image.id, set())
            )
            if not object_keys:
                raise RuntimeError(
                    f"screening image {image.id} has no object key to retain in its manifest"
                )
            db.add(
                ScreeningRetentionDeletion(
                    farm_id=image.farm_id,
                    image_id=image.id,
                    s3_bucket=image.s3_bucket,
                    object_keys=object_keys,
                    preserved_keys=[],
                    status=_DELETION_PENDING,
                    next_attempt_at=staged_at,
                    created_at=staged_at,
                    updated_at=staged_at,
                )
            )
        # Same-row concurrency fence: every screening claim/read/mutation
        # predicate observes this update after waiting on the image locks. It
        # commits atomically with the exact-key manifests above. Use one Core
        # UPDATE and explicitly preserve the pipeline lease clock: assigning
        # the mapped attribute would invoke ScreeningImage's Python
        # ``updated_at`` onupdate even though the database trigger
        # intentionally excludes privacy bookkeeping columns.
        tombstoned_ids = set(
            (
                await db.execute(
                    update(ScreeningImage)
                    .where(
                        ScreeningImage.farm_id == farm_id,
                        ScreeningImage.id.in_(old_image_ids),
                        ScreeningImage.retention_tombstoned_at.is_(None),
                    )
                    .values(
                        retention_tombstoned_at=staged_at,
                        updated_at=ScreeningImage.updated_at,
                    )
                    .returning(ScreeningImage.id)
                    .execution_options(synchronize_session=False)
                )
            ).scalars()
        )
        if tombstoned_ids != set(old_image_ids):
            raise RuntimeError("retention tombstone update missed a locked image")
        await db.flush()
        summary.screening_deletion_intents_staged += len(old_images)
    summary.screening_batches += await _delete_batch(
        db,
        table=ScreeningBatch,
        id_column=ScreeningBatch.id,
        candidates=select(ScreeningBatch.id).where(
            ScreeningBatch.farm_id == farm_id,
            ScreeningBatch.created_at < screening_batch_cutoff,
            ~exists().where(
                ScreeningImage.farm_id == farm_id,
                ScreeningImage.batch_id == ScreeningBatch.id,
            ),
        ),
        batch_size=batch_size,
    )
    budget_dates = list(
        (
            await db.execute(
                select(ScreeningDailyBudget.local_date)
                .where(
                    ScreeningDailyBudget.farm_id == farm_id,
                    ScreeningDailyBudget.local_date < screening_budget_cutoff,
                )
                .order_by(ScreeningDailyBudget.local_date)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if budget_dates:
        summary.screening_call_reservations += await _delete_chain_rows(
            db,
            table=ScreeningCallReservation,
            id_column=ScreeningCallReservation.attempt_id,
            candidates=select(ScreeningCallReservation.attempt_id).where(
                ScreeningCallReservation.farm_id == farm_id,
                ScreeningCallReservation.local_date.in_(budget_dates),
            ),
        )
        removed_budgets = await db.execute(
            delete(ScreeningDailyBudget).where(
                ScreeningDailyBudget.farm_id == farm_id,
                ScreeningDailyBudget.local_date.in_(budget_dates),
            )
        )
        summary.screening_daily_budgets += cast(CursorResult[Any], removed_budgets).rowcount

    completed_outbox_ids = list(
        (
            await db.execute(
                select(NotificationOutbox.id)
                .where(
                    NotificationOutbox.farm_id == farm_id,
                    NotificationOutbox.completed_at.is_not(None),
                    NotificationOutbox.completed_at < notification_cutoff,
                )
                .order_by(NotificationOutbox.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    if completed_outbox_ids:
        summary.notification_log += await _delete_chain_rows(
            db,
            table=NotificationLog,
            id_column=NotificationLog.id,
            candidates=select(NotificationLog.id).where(
                NotificationLog.farm_id == farm_id,
                NotificationLog.outbox_id.in_(completed_outbox_ids),
            ),
        )
        summary.notification_outbox += await _delete_chain_rows(
            db,
            table=NotificationOutbox,
            id_column=NotificationOutbox.id,
            candidates=select(NotificationOutbox.id).where(
                NotificationOutbox.farm_id == farm_id,
                NotificationOutbox.id.in_(completed_outbox_ids),
            ),
        )
    summary.notification_log += await _delete_batch(
        db,
        table=NotificationLog,
        id_column=NotificationLog.id,
        candidates=select(NotificationLog.id).where(
            NotificationLog.farm_id == farm_id,
            NotificationLog.outbox_id.is_(None),
            NotificationLog.created_at < notification_cutoff,
        ),
        batch_size=batch_size,
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
    id_column: InstrumentedAttribute[Any],
    candidates: Select[tuple[Any]],
) -> int:
    """Delete descendants of the already bounded root-image cohort."""
    removed = await db.execute(delete(table).where(id_column.in_(candidates)))
    # Descendant counts can exceed the bounded root count. Do not transfer
    # every deleted descendant ID merely to report an aggregate.
    return cast(CursorResult[Any], removed).rowcount
