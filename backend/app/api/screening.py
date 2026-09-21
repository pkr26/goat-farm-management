"""Screening: review queue for the disease-detection photo pipeline.

The worker writes, vets review. Screening is health data, so the
endpoints ride the existing health permission codes: roles with
health.view read the queue, while every write — walkthrough batches,
presigned uploads, batch submission and finding verdicts (the
training-label corpus) — demands health.manage. That keeps the seeded
read-only Auditor preset out of the write paths without inventing a
screening-specific permission.
"""

import datetime as dt
import secrets
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, literal, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.config import get_settings
from ..deps import CurrentFarm, CurrentUser, DbSession, require_perm
from ..models import (
    ScreeningBatch,
    ScreeningCrop,
    ScreeningFinding,
    ScreeningImage,
    ScreeningRun,
)
from ..models.enums import (
    ScreeningFindingStatus,
    ScreeningImageStatus,
    ScreeningRunStatus,
    ScreeningStage,
)
from ..schemas.common import COMMON_ERROR_RESPONSES, MAX_INT32_ID, MAX_PAGE_OFFSET
from ..schemas.screening import (
    MAX_SCREENING_UPLOAD_BYTES,
    ScreeningBatchBucketProgressOut,
    ScreeningBatchListOut,
    ScreeningBatchOut,
    ScreeningBucketStr,
    ScreeningCropOut,
    ScreeningDatasetExportOut,
    ScreeningDatasetRecordOut,
    ScreeningFindingOut,
    ScreeningFindingReviewIn,
    ScreeningFindingReviewOut,
    ScreeningFindingStatusStr,
    ScreeningImageDetailOut,
    ScreeningImageListOut,
    ScreeningImageRowOut,
    ScreeningImageStatusStr,
    ScreeningProviderStatsOut,
    ScreeningRunOut,
    ScreeningSeverityStr,
    ScreeningStatsOut,
    ScreeningUploadIn,
    ScreeningUploadOut,
)
from ..services.screening import ScreeningStorageError, storage_for_settings
from ..utils import today, utcnow

router = APIRouter(prefix="/api/screening", tags=["screening"], responses=COMMON_ERROR_RESPONSES)

VIEW = Annotated[set[str], Depends(require_perm("health.view"))]
MANAGE = Annotated[set[str], Depends(require_perm("health.manage"))]

SCREENING_LIST_DEFAULT_LIMIT = 25
SCREENING_LIST_MAX_LIMIT = 200

# Hard server-enforced intake ceilings.  They intentionally live beside the
# mutation paths rather than in a client hint: a compromised health manager
# can mint forms directly, so the API must bound both the number of open
# walkthroughs and the model-bound work each farm can queue.
MAX_OPEN_SCREENING_BATCHES_PER_FARM = 5
MAX_SCREENING_IMAGES_PER_BATCH = 100
MAX_IN_FLIGHT_SCREENING_IMAGES_PER_FARM = 250
# An open walkthrough may request forms for up to a day (the maximum S3
# policy lifetime) and gets one small hand-off grace period.  Once stale it
# no longer consumes the five-batch farm capacity and cannot be revived to
# mint fresh uploads.  Existing rows can still be submitted/reviewed.
MAX_OPEN_SCREENING_BATCH_AGE = dt.timedelta(hours=25)
_IN_FLIGHT_SCREENING_STATUSES = (
    ScreeningImageStatus.PENDING.value,
    ScreeningImageStatus.PROCESSING.value,
    ScreeningImageStatus.ERROR.value,
)
# Advisory-lock namespace for farm-level screening intake serialization.
# Inserting any farm-scoped child row takes FOR KEY SHARE on that Farm row,
# so a Farm row lock held across intake checks and batch locks inverts
# against every animal-first write in other modules and PostgreSQL
# deadlocks (the identical reasoning that moved simulation.py and
# planner.py off Farm-row locks). The advisory lock self-conflicts exactly
# as the row lock did and never conflicts with an FK key-share lock.
SCREENING_INTAKE_LOCK_NAMESPACE = 4716


async def _lock_farm_intake(db: AsyncSession, farm_id: int) -> None:
    """Serialize farm-level intake checks without locking the Farm row."""
    await db.execute(
        select(
            func.pg_advisory_xact_lock(literal(SCREENING_INTAKE_LOCK_NAMESPACE), literal(farm_id))
        )
    )


async def _latest_runs_by_image(
    db: AsyncSession, farm_id: int, image_ids: list[int]
) -> dict[int, ScreeningRun]:
    if not image_ids:
        return {}
    latest = (
        select(ScreeningRun, ScreeningRun.image_id)
        .distinct(ScreeningRun.image_id)
        .where(
            ScreeningRun.farm_id == farm_id,
            ScreeningRun.image_id.in_(image_ids),
        )
        .order_by(ScreeningRun.image_id, ScreeningRun.created_at.desc(), ScreeningRun.id.desc())
    )
    return {image_id: run for run, image_id in (await db.execute(latest)).all()}


async def _pending_finding_counts(
    db: AsyncSession, farm_id: int, image_ids: list[int]
) -> dict[int, int]:
    if not image_ids:
        return {}
    result = await db.execute(
        select(ScreeningRun.image_id, func.count(ScreeningFinding.id))
        .join(
            ScreeningFinding,
            (ScreeningFinding.run_id == ScreeningRun.id)
            & (ScreeningFinding.farm_id == ScreeningRun.farm_id),
        )
        .where(
            ScreeningRun.farm_id == farm_id,
            ScreeningRun.image_id.in_(image_ids),
            ScreeningFinding.status == ScreeningFindingStatus.PENDING_REVIEW.value,
        )
        .group_by(ScreeningRun.image_id)
    )
    return {int(image_id): int(count) for image_id, count in result.all()}


@router.get("/images")
async def list_images(
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
    status: ScreeningImageStatusStr | None = None,
    bucket: ScreeningBucketStr | None = None,
    limit: Annotated[int, Query(ge=1, le=SCREENING_LIST_MAX_LIMIT)] = SCREENING_LIST_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> ScreeningImageListOut:
    """A page of screening images, newest first, with each image's latest
    gate verdict and pending-review finding count."""
    filters = [ScreeningImage.farm_id == farm.id]
    if status is not None:
        filters.append(ScreeningImage.status == status)
    if bucket is not None:
        filters.append(ScreeningImage.bucket == bucket)
    total = (
        await db.execute(select(func.count()).select_from(ScreeningImage).where(*filters))
    ).scalar_one()
    images = list(
        (
            await db.execute(
                select(ScreeningImage)
                .where(*filters)
                .order_by(ScreeningImage.created_at.desc(), ScreeningImage.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
    )
    image_ids = [image.id for image in images]
    latest = await _latest_runs_by_image(db, farm.id, image_ids)
    pending = await _pending_finding_counts(db, farm.id, image_ids)
    rows = [
        ScreeningImageRowOut(
            id=image.id,
            status=cast(ScreeningImageStatusStr, image.status),
            bucket=cast(ScreeningBucketStr | None, image.bucket),
            batch_id=image.batch_id,
            s3_key=image.s3_key,
            captured_date=image.captured_date,
            width=image.width,
            height=image.height,
            byte_size=image.byte_size,
            error=image.error,
            created_at=image.created_at,
            latest_run=(
                ScreeningRunOut.model_validate(latest[image.id]) if image.id in latest else None
            ),
            pending_findings=pending.get(image.id, 0),
        )
        for image in images
    ]
    return ScreeningImageListOut(images=rows, total=total, limit=limit, offset=offset)


@router.get("/images/{image_id}")
async def get_image(
    image_id: int,
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
) -> ScreeningImageDetailOut:
    """One image's full review payload: bounded image URL, every run, every
    finding. Missing and cross-farm ids deliberately share one 404."""
    if not 1 <= image_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Screening image not found")
    image = (
        await db.execute(
            select(ScreeningImage)
            .options(
                selectinload(ScreeningImage.runs).selectinload(ScreeningRun.findings),
                selectinload(ScreeningImage.crops),
            )
            .where(ScreeningImage.farm_id == farm.id, ScreeningImage.id == image_id)
        )
    ).scalar_one_or_none()
    if image is None:
        raise HTTPException(status_code=404, detail="Screening image not found")

    settings = get_settings()
    # Process-wide storage instance: constructing ScreeningStorage per
    # request minted a fresh boto3 client (and TLS/session setup) on every
    # detail view and upload registration for nothing (P3, 2026-09-20
    # audit).
    storage = storage_for_settings(settings) if settings.screening_enabled else None
    # SigV4 signing only — no network call, safe inside a request.
    image_url = storage.presign_get(image.normalized_key or image.s3_key) if storage else None

    detail = ScreeningImageDetailOut.model_validate(image)
    detail.image_url = image_url
    detail.crops = [
        ScreeningCropOut.model_validate(crop).model_copy(
            update={
                "image_url": (
                    storage.presign_get(crop.normalized_key)
                    if storage and crop.normalized_key
                    else None
                )
            }
        )
        for crop in sorted(image.crops, key=lambda crop: crop.crop_index)
    ]
    detail.runs = sorted(
        (ScreeningRunOut.model_validate(run) for run in image.runs),
        key=lambda run: run.created_at,
    )
    findings = [
        ScreeningFindingOut.model_validate(finding)
        for run in image.runs
        for finding in run.findings
    ]
    detail.findings = sorted(findings, key=lambda finding: finding.created_at)
    return detail


@router.post("/findings/{finding_id}/review", status_code=200)
async def review_finding(
    finding_id: int,
    payload: ScreeningFindingReviewIn,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    _perms: MANAGE,
) -> ScreeningFindingReviewOut:
    """Record a vet verdict on one finding (confirm / reject).

    ``expected_status`` is optimistic concurrency: the transition runs as
    one guarded UPDATE (``WHERE status = expected_status``), so a review
    racing another reviewer — or a re-screen — fails with 409 instead of
    silently overwriting the corpus, no matter how the requests interleave.
    Re-reviewing a settled finding re-submits with its current status as
    ``expected_status``.
    """
    if not 1 <= finding_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Screening finding not found")
    finding = (
        await db.execute(
            select(ScreeningFinding).where(
                ScreeningFinding.farm_id == farm.id,
                ScreeningFinding.id == finding_id,
            )
        )
    ).scalar_one_or_none()
    if finding is None:
        # Missing and cross-farm ids deliberately share one response.
        raise HTTPException(status_code=404, detail="Screening finding not found")
    result = cast(
        CursorResult[Any],
        await db.execute(
            update(ScreeningFinding)
            .where(
                ScreeningFinding.farm_id == farm.id,
                ScreeningFinding.id == finding_id,
                ScreeningFinding.status == payload.expected_status,
            )
            .values(
                status=payload.status,
                reviewed_by_id=user.id,
                reviewed_at=utcnow(),
                review_note=payload.review_note,
            )
        ),
    )
    if result.rowcount != 1:
        # The row exists (checked above) but its status moved between the
        # read and the write: another reviewer won the race.
        raise HTTPException(
            status_code=409,
            detail=(
                f"Finding was already reviewed (status {finding.status}); "
                "reload and re-submit with the current status as expected_status"
            ),
        )
    await db.commit()
    await db.refresh(finding)
    return ScreeningFindingReviewOut.model_validate(finding)


SCREENING_STATS_MAX_DAYS = 365
SCREENING_EXPORT_MAX_RECORDS = 5_000


@router.get("/stats")
async def provider_stats(
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
    days: Annotated[int, Query(ge=1, le=SCREENING_STATS_MAX_DAYS)] = 30,
) -> ScreeningStatsOut:
    """The rotation scoreboard: call volume, verdict behavior, vet-labeled
    precision and cross-check agreement per provider over the window.

    This is the feedback loop that turns the round-robin from vendor
    insurance into a measured comparison on your own photos.
    """
    window_start = utcnow() - dt.timedelta(days=days)
    run_window = (ScreeningRun.farm_id == farm.id) & (ScreeningRun.created_at >= window_start)
    gate_rows = (
        await db.execute(
            select(
                ScreeningRun.provider,
                ScreeningRun.model,
                func.count().label("gate_runs"),
                func.sum(case((ScreeningRun.verdict == "flagged", 1), else_=0)).label(
                    "gate_flagged"
                ),
                func.sum(case((ScreeningRun.run_status == "ERROR", 1), else_=0)).label(
                    "gate_errors"
                ),
                func.avg(ScreeningRun.latency_ms).label("avg_latency"),
                func.avg(ScreeningRun.confidence).label("avg_confidence"),
            )
            .where(run_window, ScreeningRun.stage == ScreeningStage.GATE.value)
            .group_by(ScreeningRun.provider, ScreeningRun.model)
        )
    ).all()
    check_rows = (
        await db.execute(
            select(
                ScreeningRun.provider,
                ScreeningRun.model,
                func.count().label("checks"),
                func.sum(case((ScreeningRun.verdict == "flagged", 1), else_=0)).label("agreements"),
            )
            .where(
                run_window,
                ScreeningRun.stage == ScreeningStage.CROSS_CHECK.value,
                ScreeningRun.run_status == ScreeningRunStatus.OK.value,
            )
            .group_by(ScreeningRun.provider, ScreeningRun.model)
        )
    ).all()
    finding_rows = (
        await db.execute(
            select(
                ScreeningRun.provider,
                ScreeningRun.model,
                func.sum(
                    case(
                        (ScreeningFinding.status == ScreeningFindingStatus.CONFIRMED.value, 1),
                        else_=0,
                    )
                ).label("confirmed"),
                func.sum(
                    case(
                        (ScreeningFinding.status == ScreeningFindingStatus.REJECTED.value, 1),
                        else_=0,
                    )
                ).label("rejected"),
                func.sum(
                    case(
                        (ScreeningFinding.status == ScreeningFindingStatus.PENDING_REVIEW.value, 1),
                        else_=0,
                    )
                ).label("pending"),
            )
            .join(
                ScreeningFinding,
                (ScreeningFinding.run_id == ScreeningRun.id)
                & (ScreeningFinding.farm_id == ScreeningRun.farm_id),
            )
            .where(run_window, ScreeningRun.stage != ScreeningStage.DETECT.value)
            .group_by(ScreeningRun.provider, ScreeningRun.model)
        )
    ).all()

    merged: dict[tuple[str, str], dict[str, int | Decimal | None]] = {}

    def _slot(provider: str, model: str) -> dict[str, int | Decimal | None]:
        return merged.setdefault(
            (provider, model),
            {
                "gate_runs": 0,
                "gate_flagged": 0,
                "gate_errors": 0,
                "avg_gate_latency_ms": None,
                "avg_gate_confidence": None,
                "cross_checks": 0,
                "cross_check_agreements": 0,
                "findings_confirmed": 0,
                "findings_rejected": 0,
                "findings_pending": 0,
            },
        )

    for provider, model, runs, flagged, errors, avg_latency, avg_confidence in gate_rows:
        slot = _slot(provider, model)
        slot["gate_runs"] = int(runs or 0)
        slot["gate_flagged"] = int(flagged or 0)
        slot["gate_errors"] = int(errors or 0)
        slot["avg_gate_latency_ms"] = int(avg_latency) if avg_latency is not None else None
        slot["avg_gate_confidence"] = (
            Decimal(avg_confidence).quantize(Decimal("0.001"))
            if avg_confidence is not None
            else None
        )
    for provider, model, checks, agreements in check_rows:
        slot = _slot(provider, model)
        slot["cross_checks"] = int(checks or 0)
        slot["cross_check_agreements"] = int(agreements or 0)
    for provider, model, confirmed, rejected, pending in finding_rows:
        slot = _slot(provider, model)
        slot["findings_confirmed"] = int(confirmed or 0)
        slot["findings_rejected"] = int(rejected or 0)
        slot["findings_pending"] = int(pending or 0)

    def _sort_key(
        item: tuple[tuple[str, str], dict[str, int | Decimal | None]],
    ) -> tuple[int, str, str]:
        runs = item[1]["gate_runs"]
        # ``_slot`` seeds this from COUNT() and every merge writes an int,
        # but preserve a stable response even if a hand-repaired row leaves
        # the aggregate mapping malformed.
        return (-int(runs or 0), item[0][0], item[0][1])

    providers = [
        ScreeningProviderStatsOut(provider=provider, model=model, **values)  # type: ignore[arg-type]
        for (provider, model), values in sorted(merged.items(), key=_sort_key)
    ]
    return ScreeningStatsOut(window_days=days, providers=providers)


@router.get("/export")
async def export_dataset(
    db: DbSession,
    farm: CurrentFarm,
    _perms: MANAGE,
    vet_status: Literal["CONFIRMED", "REJECTED", "ALL"] = "ALL",
    limit: Annotated[
        int, Query(ge=1, le=SCREENING_EXPORT_MAX_RECORDS)
    ] = SCREENING_EXPORT_MAX_RECORDS,
) -> ScreeningDatasetExportOut:
    """The fine-tuning corpus: findings with image/crop references and the
    vet verdict that makes each label trustworthy. Defaults to every
    finding INCLUDING the pending review queue (each record carries
    ``vet_status`` so consumers can filter); pass vet_status=CONFIRMED or
    REJECTED for reviewed-only exports."""
    filters = [ScreeningFinding.farm_id == farm.id]
    if vet_status != "ALL":
        filters.append(ScreeningFinding.status == vet_status)
    rows = (
        await db.execute(
            select(ScreeningFinding, ScreeningRun, ScreeningImage, ScreeningCrop)
            .join(
                ScreeningRun,
                (ScreeningRun.id == ScreeningFinding.run_id)
                & (ScreeningRun.farm_id == ScreeningFinding.farm_id),
            )
            .join(
                ScreeningImage,
                (ScreeningImage.id == ScreeningRun.image_id)
                & (ScreeningImage.farm_id == ScreeningRun.farm_id),
            )
            .outerjoin(
                ScreeningCrop,
                (ScreeningCrop.id == ScreeningFinding.crop_id)
                & (ScreeningCrop.farm_id == ScreeningFinding.farm_id),
            )
            .where(*filters)
            .order_by(ScreeningFinding.id)
            .limit(limit)
        )
    ).all()
    records = [
        ScreeningDatasetRecordOut(
            finding_id=finding.id,
            vet_status=cast(ScreeningFindingStatusStr, finding.status),
            label=finding.label,
            confidence=finding.confidence,
            severity=cast(ScreeningSeverityStr | None, finding.severity),
            region=finding.region,
            captured_date=image.captured_date,
            # Dataset consumers need the worker-owned normalized derivative
            # whose bytes produced ``image.sha256``. The direct browser POST
            # target remains mutable until its policy expires, so exporting
            # that raw key would make a reviewed training record point at
            # different bytes after a valid replay. A legacy row can lack a
            # derivative; retain its raw key as the only available fallback.
            image_s3_key=image.normalized_key or image.s3_key,
            image_sha256=image.sha256,
            crop_index=crop.crop_index if crop is not None else None,
            crop_box_1000=(
                [crop.box_x, crop.box_y, crop.box_w, crop.box_h] if crop is not None else None
            ),
            crop_s3_key=crop.normalized_key if crop is not None else None,
            detected_by=f"{run.provider}/{run.model}",
            reviewed_at=finding.reviewed_at,
        )
        for finding, run, image, crop in rows
    ]
    return ScreeningDatasetExportOut(
        farm_id=farm.id,
        generated_at=utcnow(),
        record_count=len(records),
        records=records,
    )


SCREENING_BATCH_LIST_DEFAULT_LIMIT = 10
SCREENING_BATCH_LIST_MAX_LIMIT = 50
# The S3 key generator guarantees uniqueness per photo; the extension is
# derived from the declared content type, never taken verbatim.
_UPLOAD_EXTENSION_BY_CONTENT_TYPE = {"image/jpeg": ".jpg", "image/png": ".png"}
_TERMINAL_SCREENED_STATUSES = ("HEALTHY", "FLAGGED", "SKIPPED", "ERROR")


async def _batch_progress(
    db: AsyncSession, farm_id: int, batch_ids: list[int]
) -> dict[int, ScreeningBatchOut]:
    """Hydrate batch outs with per-bucket progress from the image rows."""
    if not batch_ids:
        return {}
    rows = (
        await db.execute(
            select(
                ScreeningImage.batch_id,
                ScreeningImage.bucket,
                ScreeningImage.status,
                func.count(),
            )
            .where(
                ScreeningImage.farm_id == farm_id,
                ScreeningImage.batch_id.in_(batch_ids),
            )
            .group_by(ScreeningImage.batch_id, ScreeningImage.bucket, ScreeningImage.status)
        )
    ).all()
    aggregates: dict[int, dict[str, Any]] = {}
    for batch_id, bucket, status, count in rows:
        progress = aggregates.setdefault(
            batch_id,
            {
                "images_uploaded": 0,
                "images_screened": 0,
                "images_flagged": 0,
                "buckets": {},
            },
        )
        progress["images_uploaded"] += int(count)
        if status in _TERMINAL_SCREENED_STATUSES:
            progress["images_screened"] += int(count)
        if status == "FLAGGED":
            progress["images_flagged"] += int(count)
        # Batch-linked rows always carry a bucket (the upload flow requires
        # it); a NULL-bucket row (hand-written data, a future writer bug)
        # still counts toward the totals but has no per-pen entry — "UNKNOWN"
        # is not a bucket and would fail the output vocabulary.
        if bucket is not None:
            bucket_progress = progress["buckets"].setdefault(
                bucket, {"uploaded": 0, "screened": 0, "flagged": 0}
            )
            bucket_progress["uploaded"] += int(count)
            if status in _TERMINAL_SCREENED_STATUSES:
                bucket_progress["screened"] += int(count)
            if status == "FLAGGED":
                bucket_progress["flagged"] += int(count)

    outs: dict[int, ScreeningBatchOut] = {}
    for batch_id, progress in aggregates.items():
        outs[batch_id] = ScreeningBatchOut(
            id=batch_id,
            created_at=utcnow(),  # replaced by the caller with the row
            submitted_at=None,
            images_uploaded=progress["images_uploaded"],
            images_screened=progress["images_screened"],
            images_flagged=progress["images_flagged"],
            buckets=[
                ScreeningBatchBucketProgressOut(
                    bucket=cast(ScreeningBucketStr, bucket),
                    **counts,
                )
                for bucket, counts in progress["buckets"].items()
            ],
        )
    return outs


def _batch_out_with_row(
    batch: ScreeningBatch, progress: dict[int, ScreeningBatchOut]
) -> ScreeningBatchOut:
    computed = progress.get(batch.id)
    if computed is None:
        return ScreeningBatchOut(
            id=batch.id,
            created_at=batch.created_at,
            submitted_at=batch.submitted_at,
        )
    computed.created_at = batch.created_at
    computed.submitted_at = batch.submitted_at
    return computed


@router.post("/batches", status_code=201)
async def create_batch(
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    _perms: MANAGE,
) -> ScreeningBatchOut:
    """Start a disease-check walkthrough: photograph every pen, then submit
    the batch for screening."""
    settings = get_settings()
    if not settings.screening_enabled:
        # Same gate as request_upload: a half-open walkthrough (batch minted,
        # uploads refused) would strand the worker mid-pen.
        raise HTTPException(
            status_code=503,
            detail="Screening storage is not configured on this deployment",
        )
    # Serialize farm-level intake checks with upload/submission mutations.
    # Without this lock, parallel requests can each observe spare capacity
    # and collectively create an unbounded set of open walkthroughs.
    await _lock_farm_intake(db, farm.id)
    active_batch_after = utcnow() - MAX_OPEN_SCREENING_BATCH_AGE
    open_batches = (
        await db.execute(
            select(func.count())
            .select_from(ScreeningBatch)
            .where(
                ScreeningBatch.farm_id == farm.id,
                ScreeningBatch.submitted_at.is_(None),
                ScreeningBatch.created_at >= active_batch_after,
            )
        )
    ).scalar_one()
    if open_batches >= MAX_OPEN_SCREENING_BATCHES_PER_FARM:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many open screening walkthroughs for this farm; submit or let an "
                "abandoned walkthrough expire before starting another"
            ),
        )
    batch = ScreeningBatch(farm_id=farm.id, created_by_id=user.id)
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    return ScreeningBatchOut(id=batch.id, created_at=batch.created_at, submitted_at=None)


@router.get("/batches")
async def list_batches(
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
    limit: Annotated[
        int, Query(ge=1, le=SCREENING_BATCH_LIST_MAX_LIMIT)
    ] = SCREENING_BATCH_LIST_DEFAULT_LIMIT,
) -> ScreeningBatchListOut:
    """Recent disease-check walkthroughs with per-pen progress."""
    batches = list(
        (
            await db.execute(
                select(ScreeningBatch)
                .where(ScreeningBatch.farm_id == farm.id)
                .order_by(ScreeningBatch.created_at.desc(), ScreeningBatch.id.desc())
                .limit(limit)
            )
        ).scalars()
    )
    progress = await _batch_progress(db, farm.id, [batch.id for batch in batches])
    return ScreeningBatchListOut(
        batches=[_batch_out_with_row(batch, progress) for batch in batches]
    )


@router.post("/batches/{batch_id}/submit")
async def submit_batch(
    batch_id: int,
    db: DbSession,
    farm: CurrentFarm,
    _perms: MANAGE,
) -> ScreeningBatchOut:
    """Finish a walkthrough ("process them"): locks further uploads and the
    worker screens every photo in the batch as the bytes land."""
    settings = get_settings()
    if not settings.screening_enabled:
        # Same gate as request_upload: submitting into a deployment whose
        # worker cannot screen would promise progress that never comes.
        raise HTTPException(
            status_code=503,
            detail="Screening storage is not configured on this deployment",
        )
    if not 1 <= batch_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Screening batch not found")
    # Lock ordering is always farm → batch (also used by request_upload), so
    # an upload racing submit has one serial outcome: either the upload is
    # registered before submission or it is rejected as too late.  There is
    # no gap where a row can commit after the batch has been closed.
    await _lock_farm_intake(db, farm.id)
    batch = (
        await db.execute(
            select(ScreeningBatch)
            .where(ScreeningBatch.farm_id == farm.id, ScreeningBatch.id == batch_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Screening batch not found")
    if batch.submitted_at is not None:
        raise HTTPException(status_code=409, detail="Batch already submitted")
    uploaded = (
        await db.execute(
            select(func.count())
            .select_from(ScreeningImage)
            .where(
                ScreeningImage.farm_id == farm.id,
                ScreeningImage.batch_id == batch.id,
            )
        )
    ).scalar_one()
    if uploaded == 0:
        raise HTTPException(status_code=409, detail="Batch has no photos to screen")
    batch.submitted_at = utcnow()
    await db.commit()
    await db.refresh(batch)
    progress = await _batch_progress(db, farm.id, [batch.id])
    return _batch_out_with_row(batch, progress)


@router.post("/uploads", status_code=201)
async def request_upload(
    payload: ScreeningUploadIn,
    db: DbSession,
    farm: CurrentFarm,
    _perms: MANAGE,
) -> ScreeningUploadOut:
    """Mint a constrained presigned POST for one pen photo.

    The server builds the key (``raw/<farm>/<date>/<bucket>/<batch>-<id>``)
    — the client never chooses where a photo lands — pre-creates the
    PENDING image row so the walkthrough shows live progress, and returns a
    policy that binds MIME, size and a row-specific metadata token.  No AWS
    credential ever reaches the device.
    """
    settings = get_settings()
    if not settings.screening_enabled:
        raise HTTPException(
            status_code=503,
            detail="Screening storage is not configured on this deployment",
        )
    # Use the same lock order as submit_batch.  In particular, do not read
    # submitted_at outside a lock then create a row later: that allowed an
    # upload registration to commit after a concurrent submit closed a batch.
    await _lock_farm_intake(db, farm.id)
    batch = (
        await db.execute(
            select(ScreeningBatch)
            .where(ScreeningBatch.farm_id == farm.id, ScreeningBatch.id == payload.batch_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Screening batch not found")
    if batch.submitted_at is not None:
        raise HTTPException(status_code=409, detail="Batch already submitted")
    if batch.created_at < utcnow() - MAX_OPEN_SCREENING_BATCH_AGE:
        # Do not let an old row fall out of the capacity count and then be
        # reused indefinitely to evade the open-walkthrough quota.  We leave
        # pre-existing rows intact so their already-issued forms can drain
        # and the batch can still be submitted for review.
        raise HTTPException(
            status_code=409,
            detail="Batch upload window expired; start a new walkthrough",
        )

    images_in_batch = (
        await db.execute(
            select(func.count())
            .select_from(ScreeningImage)
            .where(
                ScreeningImage.farm_id == farm.id,
                ScreeningImage.batch_id == batch.id,
            )
        )
    ).scalar_one()
    if images_in_batch >= MAX_SCREENING_IMAGES_PER_BATCH:
        raise HTTPException(
            status_code=429,
            detail=(
                "A screening walkthrough may contain at most "
                f"{MAX_SCREENING_IMAGES_PER_BATCH} photos"
            ),
        )
    in_flight = (
        await db.execute(
            select(func.count())
            .select_from(ScreeningImage)
            .where(
                ScreeningImage.farm_id == farm.id,
                ScreeningImage.status.in_(_IN_FLIGHT_SCREENING_STATUSES),
            )
        )
    ).scalar_one()
    if in_flight >= MAX_IN_FLIGHT_SCREENING_IMAGES_PER_FARM:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many screening photos are already awaiting processing for this farm; "
                "wait for the queue to drain"
            ),
        )

    extension = _UPLOAD_EXTENSION_BY_CONTENT_TYPE[payload.content_type]
    captured_date = today(farm.timezone)
    key = (
        f"{settings.screening_s3_prefix}/{farm.id}/{captured_date.isoformat()}/"
        f"{payload.bucket}/{batch.id}-{uuid.uuid4().hex[:12]}{extension}"
    )
    upload_token = secrets.token_urlsafe(32)
    image = ScreeningImage(
        farm_id=farm.id,
        bucket=payload.bucket,
        batch_id=batch.id,
        s3_bucket=settings.s3_bucket,
        s3_key=key,
        upload_content_type=payload.content_type,
        upload_token=upload_token,
        captured_date=captured_date,
        status=ScreeningImageStatus.PENDING.value,
    )
    db.add(image)
    # Obtain an id before signing so the form can be bound to this durable
    # pre-registration.  Presigning is local SigV4 work; if it fails, roll
    # back rather than leaving a row whose form was never handed to a client.
    await db.flush()
    try:
        upload = storage_for_settings(settings).presign_post(
            key,
            content_type=payload.content_type,
            upload_token=upload_token,
            max_bytes=MAX_SCREENING_UPLOAD_BYTES,
        )
    except ScreeningStorageError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Could not prepare screening upload storage; try again later",
        ) from exc
    await db.commit()
    await db.refresh(image)

    return ScreeningUploadOut(
        image_id=image.id,
        s3_key=key,
        upload_url=upload.url,
        upload_method="POST",
        upload_fields=upload.fields,
        max_upload_bytes=MAX_SCREENING_UPLOAD_BYTES,
        expires_in_seconds=settings.screening_presign_expiry_seconds,
    )
