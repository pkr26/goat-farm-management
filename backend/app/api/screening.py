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

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.sql.elements import ColumnElement

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
from ..models.screening import HEALTHY_CONTROL_LABEL, ScreeningFindingReview
from ..schemas.common import (
    COMMON_ERROR_RESPONSES,
    MAX_INT32_ID,
    MAX_PAGE_OFFSET,
    lifecycle_conflict,
    stale_state_conflict,
    standing_quota,
)
from ..schemas.screening import (
    MAX_SCREENING_UPLOAD_BYTES,
    ScreeningBatchBucketProgressOut,
    ScreeningBatchCreateIn,
    ScreeningBatchListOut,
    ScreeningBatchOut,
    ScreeningBucketStr,
    ScreeningCropOut,
    ScreeningDatasetExportOut,
    ScreeningDatasetRecordOut,
    ScreeningFindingOut,
    ScreeningFindingReviewHistoryListOut,
    ScreeningFindingReviewHistoryOut,
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
from ..services.idempotency import IdempotencyKey, execute_idempotent
from ..services.screening import ScreeningStorageError, storage_for_settings
from ..services.screening.raw_cleanup import raw_cleanup_deadline
from ..utils import today, utcnow
from ._shared import sms_safe_text

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


def _has_live_screening_image(farm_id: Any, image_id: Any) -> ColumnElement[bool]:
    """Correlate a child row to an image outside the retention fence."""
    return exists(
        select(ScreeningImage.id).where(
            ScreeningImage.farm_id == farm_id,
            ScreeningImage.id == image_id,
            ScreeningImage.retention_tombstoned_at.is_(None),
        )
    )


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
) -> dict[int, tuple[int, int]]:
    if not image_ids:
        return {}
    result = await db.execute(
        select(
            ScreeningRun.image_id,
            func.count(ScreeningFinding.id),
            func.count(ScreeningFinding.id).filter(ScreeningFinding.label == HEALTHY_CONTROL_LABEL),
        )
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
    return {
        int(image_id): (int(count), int(healthy_controls))
        for image_id, count, healthy_controls in result.all()
    }


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
    effective_status = _effective_image_status()
    filters = [
        ScreeningImage.farm_id == farm.id,
        ScreeningImage.retention_tombstoned_at.is_(None),
    ]
    if status is not None:
        filters.append(effective_status == status)
    if bucket is not None:
        filters.append(ScreeningImage.bucket == bucket)
    total = (
        await db.execute(select(func.count()).select_from(ScreeningImage).where(*filters))
    ).scalar_one()
    images = list(
        (
            await db.execute(
                select(ScreeningImage, effective_status)
                .where(*filters)
                .order_by(ScreeningImage.created_at.desc(), ScreeningImage.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    image_ids = [image.id for image, _status in images]
    latest = await _latest_runs_by_image(db, farm.id, image_ids)
    pending = await _pending_finding_counts(db, farm.id, image_ids)
    rows = [
        ScreeningImageRowOut(
            id=image.id,
            status=cast(ScreeningImageStatusStr, status_value),
            bucket=cast(ScreeningBucketStr | None, image.bucket),
            batch_id=image.batch_id,
            s3_key=image.s3_key,
            captured_date=image.captured_date,
            width=image.width,
            height=image.height,
            byte_size=image.byte_size,
            error=(
                "Photo cannot be assessed; upload a clearer photo (QUALITY_PROBLEM)"
                if status_value == "UNASSESSABLE"
                else image.error
            ),
            created_at=image.created_at,
            latest_run=(
                ScreeningRunOut.model_validate(latest[image.id]) if image.id in latest else None
            ),
            pending_findings=pending.get(image.id, (0, 0))[0],
            pending_healthy_controls=pending.get(image.id, (0, 0))[1],
        )
        for image, status_value in images
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
    # screening_images.id is bigint: the int8 ceiling bounds the id guard now, not the lifted int4
    # MAX_INT32_ID.
    if not 1 <= image_id <= 9_223_372_036_854_775_807:
        raise HTTPException(status_code=404, detail="Screening image not found")
    image = (
        await db.execute(
            select(ScreeningImage)
            .options(
                selectinload(ScreeningImage.runs).selectinload(ScreeningRun.findings),
                selectinload(ScreeningImage.crops),
            )
            .where(
                ScreeningImage.farm_id == farm.id,
                ScreeningImage.id == image_id,
            )
            # Keep the exact row live until presigned derivative URLs are
            # assembled. Retention's FOR UPDATE SKIP LOCKED planner will leave
            # a concurrently viewed image for the next pass instead of
            # tombstoning/deleting its object midway through this response.
            .with_for_update(read=True, of=ScreeningImage)
        )
    ).scalar_one_or_none()
    if image is None or image.retention_tombstoned_at is not None:
        raise HTTPException(status_code=404, detail="Screening image not found")

    settings = get_settings()
    # Process-wide storage instance: constructing ScreeningStorage per request minted a fresh boto3
    # client (and TLS/session setup) on every detail view and upload registration for nothing.
    storage = storage_for_settings(settings) if settings.screening_enabled else None
    # SigV4 signing only — no network call, safe inside a request. L-3: only the worker-produced
    # immutable derivative is reviewable. The raw upload key stays client-writable until its
    # presigned POST policy expires, so presigning it for a not-yet-normalized row would let the vet
    # approve bytes the uploader can still swap — the same reason the dataset export refuses to emit
    # raw keys. Rows without a derivative answer image_url=None and the client renders the
    # processing state.
    image_url = (
        storage.presign_get(image.normalized_key)
        if storage and image.normalized_key is not None
        else None
    )

    detail = ScreeningImageDetailOut.model_validate(image)
    latest_gates: dict[int | None, ScreeningRun] = {}
    for run in sorted(image.runs, key=lambda run: (run.created_at, run.id)):
        if run.stage == "GATE" and run.run_status == "OK":
            latest_gates[run.crop_id] = run
    quality_crops = {
        crop_id
        for crop_id, run in latest_gates.items()
        if run.detail is not None and run.detail.get("quality_problem") is True
    }
    if image.status == "HEALTHY" and quality_crops:
        detail.status = "UNASSESSABLE"
        detail.error = "Photo cannot be assessed; upload a clearer photo (QUALITY_PROBLEM)"
    detail.image_url = image_url
    detail.crops = [
        ScreeningCropOut.model_validate(crop).model_copy(
            update={
                "image_url": (
                    storage.presign_get(crop.normalized_key)
                    if storage and crop.normalized_key
                    else None
                ),
                "status": (
                    "UNASSESSABLE"
                    if crop.status == "HEALTHY" and crop.id in quality_crops
                    else crop.status
                ),
                "error": (
                    "Photo cannot be assessed; upload a clearer photo (QUALITY_PROBLEM)"
                    if crop.status == "HEALTHY" and crop.id in quality_crops
                    else crop.error
                ),
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

    The locked revision detects same-status edits and ABA transitions. Each
    accepted decision appends an audit event in the same transaction.
    """
    # screening_findings.id is bigint: the int8 ceiling bounds the id guard now, like the image_id
    # guard above.
    if not 1 <= finding_id <= 9_223_372_036_854_775_807:
        raise HTTPException(status_code=404, detail="Screening finding not found")
    image_id_for_finding = (
        await db.execute(
            select(ScreeningRun.image_id)
            .join(
                ScreeningFinding,
                (ScreeningFinding.farm_id == ScreeningRun.farm_id)
                & (ScreeningFinding.run_id == ScreeningRun.id),
            )
            .where(
                ScreeningFinding.farm_id == farm.id,
                ScreeningFinding.id == finding_id,
            )
        )
    ).scalar_one_or_none()
    if image_id_for_finding is None:
        raise HTTPException(status_code=404, detail="Screening finding not found")
    image = (
        await db.execute(
            select(ScreeningImage)
            .where(
                ScreeningImage.farm_id == farm.id,
                ScreeningImage.id == image_id_for_finding,
            )
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if image is None or image.retention_tombstoned_at is not None:
        raise HTTPException(status_code=404, detail="Screening finding not found")
    finding = (
        await db.execute(
            select(ScreeningFinding)
            .join(
                ScreeningRun,
                (ScreeningRun.farm_id == ScreeningFinding.farm_id)
                & (ScreeningRun.id == ScreeningFinding.run_id),
            )
            .where(
                ScreeningFinding.farm_id == farm.id,
                ScreeningFinding.id == finding_id,
                ScreeningRun.image_id == image.id,
            )
            .with_for_update(of=ScreeningFinding)
        )
    ).scalar_one_or_none()
    if finding is None:
        # Missing and cross-farm ids deliberately share one response.
        raise HTTPException(status_code=404, detail="Screening finding not found")
    if (
        finding.review_revision != payload.expected_revision
        or finding.status != payload.expected_status
    ):
        raise stale_state_conflict(
            detail="Finding review changed; reload its current revision before reviewing again",
        )
    reviewed_at = utcnow()
    revision = finding.review_revision + 1
    # The old reviewer/time/note are known facts. Preserve them before
    # replacing the current projection, including imported legacy rows
    # created after the forward migration's populated-data snapshot.
    if (
        finding.review_revision == 0
        and finding.status in ("CONFIRMED", "REJECTED")
        and await db.get(ScreeningFindingReview, (finding.id, 0)) is None
    ):
        db.add(
            ScreeningFindingReview(
                farm_id=farm.id,
                finding_id=finding.id,
                revision=0,
                previous_status=finding.status,
                status=finding.status,
                reviewed_by_id=finding.reviewed_by_id,
                reviewed_at=finding.reviewed_at,
                review_note=finding.review_note,
            )
        )
    db.add(
        ScreeningFindingReview(
            farm_id=farm.id,
            finding_id=finding.id,
            revision=revision,
            previous_status=finding.status,
            status=payload.status,
            reviewed_by_id=user.id,
            reviewed_at=reviewed_at,
            review_note=payload.review_note,
        )
    )
    finding.status = payload.status
    finding.reviewed_by_id = user.id
    finding.reviewed_at = reviewed_at
    finding.review_note = payload.review_note
    finding.review_revision = revision
    is_healthy_control = finding.label == HEALTHY_CONTROL_LABEL
    alertable_review = (
        payload.status == "REJECTED" if is_healthy_control else payload.status == "CONFIRMED"
    )
    if is_healthy_control:
        alert_message = (
            f"Herdly: healthy-verdict quality-control review #{finding.id} "
            "found a visible abnormality."
        )
    else:
        alert_message = (
            f"Herdly: screening finding #{finding.id} "
            f"({sms_safe_text(finding.label)}) was CONFIRMED by the vet."
        )
    alert_event_key = f"finding:{finding.id}:review:{revision}:{payload.status}"
    if alertable_review:
        from ..services.notifications.outbox import enqueue_alert

        await enqueue_alert(
            db,
            farm.id,
            "SCREENING_FLAG",
            alert_message,
            alert_event_key,
        )
    await db.commit()
    await db.refresh(finding)
    if alertable_review:
        # Alert hook: a vet-confirmed screening finding is the same-day signal the owner opted into.
        # Best-effort, own session. the model-authored label is free text interpolated into the SMS
        # body — URL-ish tokens are neutralized before they reach emit_alert.
        from ..services.notifications import emit_alert

        await emit_alert(
            farm.id,
            "SCREENING_FLAG",
            alert_message,
            alert_event_key,
        )
    return ScreeningFindingReviewOut.model_validate(finding)


@router.get("/findings/{finding_id}/reviews")
async def finding_review_history(
    finding_id: int,
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
    limit: Annotated[int, Query(ge=1, le=SCREENING_LIST_MAX_LIMIT)] = SCREENING_LIST_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> ScreeningFindingReviewHistoryListOut:
    if not 1 <= finding_id <= 9_223_372_036_854_775_807:
        raise HTTPException(status_code=404, detail="Screening finding not found")
    image_id_for_finding = (
        await db.execute(
            select(ScreeningRun.image_id)
            .join(
                ScreeningFinding,
                (ScreeningFinding.farm_id == ScreeningRun.farm_id)
                & (ScreeningFinding.run_id == ScreeningRun.id),
            )
            .where(
                ScreeningFinding.farm_id == farm.id,
                ScreeningFinding.id == finding_id,
            )
        )
    ).scalar_one_or_none()
    if image_id_for_finding is None:
        raise HTTPException(status_code=404, detail="Screening finding not found")
    image = (
        await db.execute(
            select(ScreeningImage)
            .where(
                ScreeningImage.farm_id == farm.id,
                ScreeningImage.id == image_id_for_finding,
            )
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if image is None or image.retention_tombstoned_at is not None:
        raise HTTPException(status_code=404, detail="Screening finding not found")
    finding = (
        await db.execute(
            select(ScreeningFinding)
            .join(
                ScreeningRun,
                (ScreeningRun.farm_id == ScreeningFinding.farm_id)
                & (ScreeningRun.id == ScreeningFinding.run_id),
            )
            .where(
                ScreeningFinding.id == finding_id,
                ScreeningFinding.farm_id == farm.id,
                ScreeningRun.image_id == image.id,
            )
        )
    ).scalar_one_or_none()
    if finding is None:
        raise HTTPException(status_code=404, detail="Screening finding not found")
    filters = (
        ScreeningFindingReview.farm_id == farm.id,
        ScreeningFindingReview.finding_id == finding_id,
    )
    total = (
        await db.execute(select(func.count()).select_from(ScreeningFindingReview).where(*filters))
    ).scalar_one()
    reviews = (
        await db.execute(
            select(ScreeningFindingReview)
            .where(*filters)
            .order_by(ScreeningFindingReview.revision.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    return ScreeningFindingReviewHistoryListOut(
        finding_id=finding_id,
        review_revision=finding.review_revision,
        legacy_review=(finding.review_revision == 0 and finding.reviewed_at is not None)
        or bool(
            (
                await db.execute(
                    select(exists().where(*filters, ScreeningFindingReview.revision == 0))
                )
            ).scalar_one()
        ),
        reviews=[ScreeningFindingReviewHistoryOut.model_validate(row) for row in reviews],
        total=total,
        limit=limit,
        offset=offset,
    )


SCREENING_STATS_MAX_DAYS = 365
SCREENING_EXPORT_MAX_RECORDS = 5_000


def _wilson_interval(successes: int, total: int) -> tuple[Decimal, Decimal] | None:
    """95% Wilson score interval, rounded to the API's three-decimal precision."""
    if total <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + (z * z / total)
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * ((proportion * (1 - proportion) / total + z * z / (4 * total * total)) ** 0.5)
        / denominator
    )
    return (
        Decimal(str(max(0.0, centre - margin))).quantize(Decimal("0.001")),
        Decimal(str(min(1.0, centre + margin))).quantize(Decimal("0.001")),
    )


@router.get("/stats")
async def provider_stats(
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
    days: Annotated[int, Query(ge=1, le=SCREENING_STATS_MAX_DAYS)] = 30,
) -> ScreeningStatsOut:
    """The rotation scoreboard: call volume, verdict behavior, conditional
    positive precision, sampled healthy false-negative rate, and cross-check
    agreement per provider over the window.

    This is the feedback loop that turns the round-robin from vendor
    insurance into a measured comparison on your own photos. The positive
    metric is conditional on emitted findings; the healthy metric covers the
    deterministic quality-control sample and is not full-population
    sensitivity or specificity.
    """
    window_start = utcnow() - dt.timedelta(days=days)
    run_window = (
        (ScreeningRun.farm_id == farm.id)
        & (ScreeningRun.created_at >= window_start)
        & _has_live_screening_image(ScreeningRun.farm_id, ScreeningRun.image_id)
    )
    gate_rows = (
        await db.execute(
            select(
                ScreeningRun.provider,
                ScreeningRun.model,
                func.count().label("gate_runs"),
                func.sum(case((ScreeningRun.verdict == "flagged", 1), else_=0)).label(
                    "gate_flagged"
                ),
                func.sum(case((ScreeningRun.verdict == "unassessable", 1), else_=0)).label(
                    "gate_unassessable"
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
                        (
                            (ScreeningFinding.label != HEALTHY_CONTROL_LABEL)
                            & (ScreeningFinding.status == ScreeningFindingStatus.CONFIRMED.value),
                            1,
                        ),
                        else_=0,
                    )
                ).label("confirmed"),
                func.sum(
                    case(
                        (
                            (ScreeningFinding.label != HEALTHY_CONTROL_LABEL)
                            & (ScreeningFinding.status == ScreeningFindingStatus.REJECTED.value),
                            1,
                        ),
                        else_=0,
                    )
                ).label("rejected"),
                func.sum(
                    case(
                        (
                            (ScreeningFinding.label != HEALTHY_CONTROL_LABEL)
                            & (
                                ScreeningFinding.status
                                == ScreeningFindingStatus.PENDING_REVIEW.value
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("pending"),
                func.sum(
                    case(
                        (
                            (ScreeningFinding.label == HEALTHY_CONTROL_LABEL)
                            & (ScreeningFinding.status == ScreeningFindingStatus.CONFIRMED.value),
                            1,
                        ),
                        else_=0,
                    )
                ).label("healthy_confirmed"),
                func.sum(
                    case(
                        (
                            (ScreeningFinding.label == HEALTHY_CONTROL_LABEL)
                            & (ScreeningFinding.status == ScreeningFindingStatus.REJECTED.value),
                            1,
                        ),
                        else_=0,
                    )
                ).label("healthy_rejected"),
                func.sum(
                    case(
                        (
                            (ScreeningFinding.label == HEALTHY_CONTROL_LABEL)
                            & (
                                ScreeningFinding.status
                                == ScreeningFindingStatus.PENDING_REVIEW.value
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("healthy_pending"),
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
                "gate_unassessable": 0,
                "gate_errors": 0,
                "avg_gate_latency_ms": None,
                "avg_gate_confidence": None,
                "cross_checks": 0,
                "cross_check_agreements": 0,
                "findings_confirmed": 0,
                "findings_rejected": 0,
                "findings_pending": 0,
                "positive_precision": None,
                "positive_precision_ci_low": None,
                "positive_precision_ci_high": None,
                "healthy_controls_confirmed": 0,
                "healthy_controls_rejected": 0,
                "healthy_controls_pending": 0,
                "healthy_controls_reviewed": 0,
                "healthy_false_negative_rate": None,
                "healthy_false_negative_ci_low": None,
                "healthy_false_negative_ci_high": None,
            },
        )

    for (
        provider,
        model,
        runs,
        flagged,
        unassessable,
        errors,
        avg_latency,
        avg_confidence,
    ) in gate_rows:
        slot = _slot(provider, model)
        slot["gate_runs"] = int(runs or 0)
        slot["gate_flagged"] = int(flagged or 0)
        slot["gate_unassessable"] = int(unassessable or 0)
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
    for (
        provider,
        model,
        confirmed,
        rejected,
        pending,
        healthy_confirmed,
        healthy_rejected,
        healthy_pending,
    ) in finding_rows:
        slot = _slot(provider, model)
        slot["findings_confirmed"] = int(confirmed or 0)
        slot["findings_rejected"] = int(rejected or 0)
        slot["findings_pending"] = int(pending or 0)
        slot["healthy_controls_confirmed"] = int(healthy_confirmed or 0)
        slot["healthy_controls_rejected"] = int(healthy_rejected or 0)
        slot["healthy_controls_pending"] = int(healthy_pending or 0)

    for slot in merged.values():
        positive_reviewed = int(slot["findings_confirmed"] or 0) + int(
            slot["findings_rejected"] or 0
        )
        if positive_reviewed:
            confirmed = int(slot["findings_confirmed"] or 0)
            slot["positive_precision"] = (Decimal(confirmed) / Decimal(positive_reviewed)).quantize(
                Decimal("0.001")
            )
            interval = _wilson_interval(confirmed, positive_reviewed)
            if interval is not None:
                slot["positive_precision_ci_low"], slot["positive_precision_ci_high"] = interval
        healthy_reviewed = int(slot["healthy_controls_confirmed"] or 0) + int(
            slot["healthy_controls_rejected"] or 0
        )
        slot["healthy_controls_reviewed"] = healthy_reviewed
        if healthy_reviewed:
            false_negatives = int(slot["healthy_controls_rejected"] or 0)
            slot["healthy_false_negative_rate"] = (
                Decimal(false_negatives) / Decimal(healthy_reviewed)
            ).quantize(Decimal("0.001"))
            interval = _wilson_interval(false_negatives, healthy_reviewed)
            if interval is not None:
                (
                    slot["healthy_false_negative_ci_low"],
                    slot["healthy_false_negative_ci_high"],
                ) = interval

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
    REJECTED for reviewed-only exports. Healthy-control records use CONFIRMED
    for no visible abnormality and REJECTED when the reviewer found one."""
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
            .where(
                *filters,
                ScreeningImage.retention_tombstoned_at.is_(None),
            )
            .order_by(ScreeningFinding.id)
            .limit(limit)
        )
    ).all()
    records = [
        ScreeningDatasetRecordOut(
            finding_id=finding.id,
            example_kind=cast(
                Literal["POSITIVE_FINDING", "HEALTHY_CONTROL"], finding.evaluation_kind
            ),
            model_verdict=cast(
                Literal["healthy", "flagged", "unassessable"] | None,
                (
                    "healthy"
                    if finding.evaluation_kind == "HEALTHY_CONTROL"
                    else run.verdict or "flagged"
                ),
            ),
            prompt_version=run.prompt_version,
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
_TERMINAL_SCREENED_STATUSES = ("HEALTHY", "FLAGGED")


def _effective_image_status() -> ColumnElement[str]:
    """Read legacy quality evidence truthfully without rewriting historical rows."""
    gate = aliased(ScreeningRun)
    newer = aliased(ScreeningRun)
    bad_latest_gate = exists(
        select(gate.id).where(
            gate.image_id == ScreeningImage.id,
            gate.farm_id == ScreeningImage.farm_id,
            gate.stage == "GATE",
            gate.run_status == "OK",
            gate.detail["quality_problem"].astext == "true",
            ~exists(
                select(newer.id).where(
                    newer.image_id == gate.image_id,
                    newer.crop_id.is_not_distinct_from(gate.crop_id),
                    newer.stage == "GATE",
                    newer.run_status == "OK",
                    or_(
                        newer.created_at > gate.created_at,
                        (newer.created_at == gate.created_at) & (newer.id > gate.id),
                    ),
                )
            ),
        )
    )
    return case(
        ((ScreeningImage.status == "HEALTHY") & bad_latest_gate, literal("UNASSESSABLE")),
        else_=ScreeningImage.status,
    )


async def _batch_progress(
    db: AsyncSession, farm_id: int, batch_ids: list[int]
) -> dict[int, ScreeningBatchOut]:
    """Hydrate batch outs with per-bucket progress from the image rows."""
    if not batch_ids:
        return {}
    effective_status = _effective_image_status()
    rows = (
        await db.execute(
            select(
                ScreeningImage.batch_id,
                ScreeningImage.bucket,
                effective_status,
                func.count(),
            )
            .where(
                ScreeningImage.farm_id == farm_id,
                ScreeningImage.batch_id.in_(batch_ids),
                ScreeningImage.retention_tombstoned_at.is_(None),
            )
            .group_by(ScreeningImage.batch_id, ScreeningImage.bucket, effective_status)
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
                "images_unassessable": 0,
                "buckets": {},
            },
        )
        progress["images_uploaded"] += int(count)
        if status in _TERMINAL_SCREENED_STATUSES:
            progress["images_screened"] += int(count)
        if status == "FLAGGED":
            progress["images_flagged"] += int(count)
        if status == "UNASSESSABLE":
            progress["images_unassessable"] += int(count)
        # Batch-linked rows always carry a bucket (the upload flow requires
        # it); a NULL-bucket row (hand-written data, a future writer bug)
        # still counts toward the totals but has no per-pen entry — "UNKNOWN"
        # is not a bucket and would fail the output vocabulary.
        if bucket is not None:
            bucket_progress = progress["buckets"].setdefault(
                bucket, {"uploaded": 0, "screened": 0, "flagged": 0, "unassessable": 0}
            )
            bucket_progress["uploaded"] += int(count)
            if status in _TERMINAL_SCREENED_STATUSES:
                bucket_progress["screened"] += int(count)
            if status == "FLAGGED":
                bucket_progress["flagged"] += int(count)
            if status == "UNASSESSABLE":
                bucket_progress["unassessable"] += int(count)

    outs: dict[int, ScreeningBatchOut] = {}
    for batch_id, progress in aggregates.items():
        outs[batch_id] = ScreeningBatchOut(
            id=batch_id,
            created_at=utcnow(),  # replaced by the caller with the row
            submitted_at=None,
            images_uploaded=progress["images_uploaded"],
            images_screened=progress["images_screened"],
            images_flagged=progress["images_flagged"],
            images_unassessable=progress["images_unassessable"],
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
    response: Response,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    _perms: MANAGE,
    idempotency_key: IdempotencyKey,
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

    async def mutate() -> ScreeningBatchOut:
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
            # A standing quota, not a request-rate throttle: 409 like every other capacity cap in
            # the API.
            raise standing_quota(
                detail=(
                    "Too many open screening walkthroughs for this farm; submit or let an "
                    "abandoned walkthrough expire before starting another"
                ),
            )
        batch = ScreeningBatch(farm_id=farm.id, created_by_id=user.id)
        db.add(batch)
        await db.flush()
        return ScreeningBatchOut(id=batch.id, created_at=batch.created_at, submitted_at=None)

    # A retried walkthrough-open minted a second (third, …) open batch every time. The key is
    # optional so existing keyless clients keep working; the claim is scoped to actor+farm+route
    # like every other idempotent mutation.
    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/screening/batches",
        payload=ScreeningBatchCreateIn(),
        path_identity={},
        success_status=201,
        response_type=ScreeningBatchOut,
        mutate=mutate,
    )


@router.get("/batches")
async def list_batches(
    db: DbSession,
    farm: CurrentFarm,
    _perms: VIEW,
    limit: Annotated[
        int, Query(ge=1, le=SCREENING_BATCH_LIST_MAX_LIMIT)
    ] = SCREENING_BATCH_LIST_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)] = 0,
) -> ScreeningBatchListOut:
    """A page of disease-check walkthroughs, newest first, with per-pen progress.

    ``total`` is the farm's full batch count so clients can page beyond the
    newest screen."""
    total = (
        await db.execute(
            select(func.count())
            .select_from(ScreeningBatch)
            .where(ScreeningBatch.farm_id == farm.id)
        )
    ).scalar_one()
    batches = list(
        (
            await db.execute(
                select(ScreeningBatch)
                .where(ScreeningBatch.farm_id == farm.id)
                .order_by(ScreeningBatch.created_at.desc(), ScreeningBatch.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
    )
    progress = await _batch_progress(db, farm.id, [batch.id for batch in batches])
    return ScreeningBatchListOut(
        batches=[_batch_out_with_row(batch, progress) for batch in batches],
        total=total,
        limit=limit,
        offset=offset,
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
        raise lifecycle_conflict(detail="Batch already submitted")
    uploaded = (
        await db.execute(
            select(func.count())
            .select_from(ScreeningImage)
            .where(
                ScreeningImage.farm_id == farm.id,
                ScreeningImage.batch_id == batch.id,
                ScreeningImage.retention_tombstoned_at.is_(None),
            )
        )
    ).scalar_one()
    if uploaded == 0:
        raise lifecycle_conflict(detail="Batch has no photos to screen")
    batch.submitted_at = utcnow()
    await db.commit()
    await db.refresh(batch)
    progress = await _batch_progress(db, farm.id, [batch.id])
    return _batch_out_with_row(batch, progress)


@router.post("/uploads", status_code=201)
async def request_upload(
    payload: ScreeningUploadIn,
    response: Response,
    db: DbSession,
    farm: CurrentFarm,
    user: CurrentUser,
    _perms: MANAGE,
    idempotency_key: IdempotencyKey,
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

    async def mutate() -> ScreeningUploadOut:
        # Use the same lock order as submit_batch.  In particular, do not read
        # submitted_at outside a lock then create a row later: that allowed an
        # upload registration to commit after a concurrent submit closed a batch.
        await _lock_farm_intake(db, farm.id)
        batch = (
            await db.execute(
                select(ScreeningBatch)
                .where(
                    ScreeningBatch.farm_id == farm.id,
                    ScreeningBatch.id == payload.batch_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if batch is None:
            raise HTTPException(status_code=404, detail="Screening batch not found")
        if batch.submitted_at is not None:
            raise lifecycle_conflict(detail="Batch already submitted")
        if batch.created_at < utcnow() - MAX_OPEN_SCREENING_BATCH_AGE:
            # Do not let an old row fall out of the capacity count and then be
            # reused indefinitely to evade the open-walkthrough quota.  We leave
            # pre-existing rows intact so their already-issued forms can drain
            # and the batch can still be submitted for review.
            raise lifecycle_conflict(
                detail="Batch upload window expired; start a new walkthrough",
            )

        images_in_batch = (
            await db.execute(
                select(func.count())
                .select_from(ScreeningImage)
                .where(
                    ScreeningImage.farm_id == farm.id,
                    ScreeningImage.batch_id == batch.id,
                    ScreeningImage.retention_tombstoned_at.is_(None),
                )
            )
        ).scalar_one()
        if images_in_batch >= MAX_SCREENING_IMAGES_PER_BATCH:
            # Standing per-walkthrough quota → 409.
            raise standing_quota(
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
                    ScreeningImage.retention_tombstoned_at.is_(None),
                )
            )
        ).scalar_one()
        if in_flight >= MAX_IN_FLIGHT_SCREENING_IMAGES_PER_FARM:
            # Standing in-flight quota → 409; 429 stays reserved for request-rate throttles that
            # carry Retry-After.
            raise standing_quota(
                detail=(
                    "Too many screening photos are already awaiting processing for this "
                    "farm; wait for the queue to drain"
                ),
            )

        extension = _UPLOAD_EXTENSION_BY_CONTENT_TYPE[payload.content_type]
        captured_date = today(farm.timezone)
        key = (
            f"{settings.screening_s3_prefix}/{farm.id}/{captured_date.isoformat()}/"
            f"{payload.bucket}/{batch.id}-{uuid.uuid4().hex}{extension}"
        )
        upload_token = secrets.token_urlsafe(32)
        # Capture issuance immediately before local SigV4 generation.  The
        # durable final-purge deadline includes the exact configured form
        # lifetime plus clock/final-write slack; eager deletion by the worker
        # never clears this independent obligation because the same form can
        # recreate the raw key until it expires.
        upload_issued_at = utcnow()
        cleanup_after = raw_cleanup_deadline(
            upload_issued_at,
            settings.screening_presign_expiry_seconds,
        )
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
            raw_cleanup_after=cleanup_after,
            raw_cleanup_next_attempt_at=cleanup_after,
        )
        db.add(image)
        # Obtain an id before signing so the form can be bound to this durable
        # pre-registration.  Presigning is local SigV4 work; if it fails, roll
        # back rather than leaving a row whose form was never handed to a
        # client.  The rollback also drops the idempotency claim, so the
        # worker's retry with the same key re-claims cleanly.
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
        return ScreeningUploadOut(
            image_id=image.id,
            s3_key=key,
            upload_url=upload.url,
            upload_method="POST",
            upload_fields=upload.fields,
            max_upload_bytes=MAX_SCREENING_UPLOAD_BYTES,
            expires_in_seconds=settings.screening_presign_expiry_seconds,
        )

    # A retried form mint pre-registered a second PENDING image row every time. The key is optional
    # so existing keyless clients keep working. A replay returns the ORIGINAL presigned form: once
    # it has aged past its own expiry the client simply retries with a fresh key — the deduplication
    # target is the image row, not the form.
    return await execute_idempotent(
        db,
        http_response=response,
        key=idempotency_key,
        farm_id=farm.id,
        actor_id=user.id,
        operation="POST /api/screening/uploads",
        payload=payload,
        path_identity={},
        success_status=201,
        response_type=ScreeningUploadOut,
        mutate=mutate,
    )
