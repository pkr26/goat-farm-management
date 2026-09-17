"""Screening: review queue for the disease-detection photo pipeline.

The worker writes, vets review. Screening is health data, so the
endpoints ride the existing health permission codes: workers (roles with
health.view) see the queue, health.manage roles confirm/reject findings
(the training-label corpus).
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, func, select
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
from ..services.screening import ScreeningStorage
from ..utils import today, utcnow

router = APIRouter(prefix="/api/screening", tags=["screening"], responses=COMMON_ERROR_RESPONSES)

VIEW = Annotated[set[str], Depends(require_perm("health.view"))]
MANAGE = Annotated[set[str], Depends(require_perm("health.manage"))]

SCREENING_LIST_DEFAULT_LIMIT = 25
SCREENING_LIST_MAX_LIMIT = 200


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
    limit: Annotated[
        int, Query(ge=1, le=SCREENING_LIST_MAX_LIMIT)
    ] = SCREENING_LIST_DEFAULT_LIMIT,
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
    storage = ScreeningStorage(settings) if settings.screening_enabled else None
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

    ``expected_status`` is optimistic concurrency: a review that races
    another reviewer (or a re-screen) fails with 409 instead of silently
    overwriting the corpus. Re-reviewing a settled finding re-submits with
    its current status as ``expected_status``.
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
    if finding.status != payload.expected_status:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Finding was already reviewed (status {finding.status}); "
                "reload and re-submit with the current status as expected_status"
            ),
        )
    finding.status = payload.status
    finding.reviewed_by_id = user.id
    finding.reviewed_at = utcnow()
    finding.review_note = payload.review_note
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
                func.sum(cast(Integer, ScreeningRun.verdict == "flagged")).label("gate_flagged"),
                func.sum(cast(Integer, ScreeningRun.run_status == "ERROR")).label("gate_errors"),
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
                func.sum(cast(Integer, ScreeningRun.verdict == "flagged")).label("agreements"),
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
                    cast(Integer, ScreeningFinding.status == ScreeningFindingStatus.CONFIRMED.value)
                ).label("confirmed"),
                func.sum(
                    cast(Integer, ScreeningFinding.status == ScreeningFindingStatus.REJECTED.value)
                ).label("rejected"),
                func.sum(
                    cast(
                        Integer,
                        ScreeningFinding.status == ScreeningFindingStatus.PENDING_REVIEW.value,
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
        assert isinstance(runs, int)  # gate stats are COUNT()s
        return (-runs, item[0][0], item[0][1])

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
    reviewed finding; pass vet_status=ALL to include the pending queue."""
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
            image_s3_key=image.s3_key,
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
        bucket_key = bucket or "UNKNOWN"
        bucket_progress = progress["buckets"].setdefault(
            bucket_key, {"uploaded": 0, "screened": 0, "flagged": 0}
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
            created_at=dt.datetime.now(dt.UTC),  # replaced by caller with the row
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
    _perms: VIEW,
) -> ScreeningBatchOut:
    """Start a disease-check walkthrough: photograph every pen, then submit
    the batch for screening."""
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
    _perms: VIEW,
) -> ScreeningBatchOut:
    """Finish a walkthrough ("process them"): locks further uploads and the
    worker screens every photo in the batch as the bytes land."""
    if not 1 <= batch_id <= MAX_INT32_ID:
        raise HTTPException(status_code=404, detail="Screening batch not found")
    batch = (
        await db.execute(
            select(ScreeningBatch).where(
                ScreeningBatch.farm_id == farm.id, ScreeningBatch.id == batch_id
            )
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
    _perms: VIEW,
) -> ScreeningUploadOut:
    """Mint a presigned PUT for one pen photo.

    The server builds the key (``raw/<farm>/<date>/<bucket>/<batch>-<id>``)
    — the client never chooses where a photo lands — pre-creates the
    PENDING image row so the walkthrough shows live progress, and the
    phone uploads its bytes straight to S3. No AWS credential ever
    reaches the device."""
    settings = get_settings()
    if not settings.screening_enabled:
        raise HTTPException(
            status_code=503,
            detail="Screening storage is not configured on this deployment",
        )
    batch = (
        await db.execute(
            select(ScreeningBatch).where(
                ScreeningBatch.farm_id == farm.id, ScreeningBatch.id == payload.batch_id
            )
        )
    ).scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Screening batch not found")
    if batch.submitted_at is not None:
        raise HTTPException(status_code=409, detail="Batch already submitted")

    extension = _UPLOAD_EXTENSION_BY_CONTENT_TYPE[payload.content_type]
    key = (
        f"{settings.screening_s3_prefix}/{farm.id}/{today().isoformat()}/"
        f"{payload.bucket}/{batch.id}-{uuid.uuid4().hex[:12]}{extension}"
    )
    image = ScreeningImage(
        farm_id=farm.id,
        bucket=payload.bucket,
        batch_id=batch.id,
        s3_bucket=settings.s3_bucket,
        s3_key=key,
        captured_date=today(),
        status=ScreeningImageStatus.PENDING.value,
    )
    db.add(image)
    await db.commit()
    await db.refresh(image)

    upload_url = ScreeningStorage(settings).presign_put(key, payload.content_type)
    return ScreeningUploadOut(
        image_id=image.id,
        s3_key=key,
        upload_url=upload_url,
        expires_in_seconds=settings.screening_presign_expiry_seconds,
    )
