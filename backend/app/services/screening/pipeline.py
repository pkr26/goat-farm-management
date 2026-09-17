"""The screening cycle: list S3, claim new objects, normalize, cascade.

Cascade (Phase 3): a detection call splits multi-goat photos into
per-goat crops; each crop (or the whole photo when detection finds nothing)
runs gate → (if flagged) specialists + cross-check. Healthy verdicts stop
the cascade per goat; findings land in the vet review queue.

One cycle is bounded (``screening_max_images_per_cycle``) and idempotent
(the (bucket, key) unique constraint is the claim). New keys, stale
PROCESSING rows (a crashed worker) and ERROR rows whose last attempt is at
least an hour old are (re)claimed, so transient provider outages self-heal
without operator action. PENDING upload rows whose presigned URL expired
long ago are swept to SKIPPED — an abandoned walkthrough must not occupy
the cycle budget forever. Claims are committed durably with
``FOR UPDATE SKIP LOCKED``, so even two workers sharing the database
never process the same photo twice.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import Settings
from ...models import Farm, ScreeningCrop, ScreeningFinding, ScreeningImage, ScreeningRun
from ...models.enums import (
    Bucket,
    ScreeningFindingStatus,
    ScreeningImageStatus,
    ScreeningRunStatus,
    ScreeningStage,
)
from ...utils import utcnow
from .detect import (
    DETECT_PROMPT_VERSION,
    DETECT_SYSTEM_PROMPT,
    DetectionBox,
    DetectionParseError,
    parse_detection_response,
)
from .gate import GATE_PROMPT_VERSION
from .images import (
    CropError,
    CroppedImage,
    ImageNormalizationError,
    NormalizedImage,
    crop_image,
    normalize_image,
)
from .providers import ProviderError, VisionProvider
from .providers import gate as run_gate
from .rotation import GateExhaustedError, GateOutcome, ProviderRotation
from .s3 import ScreeningObjectMissingError, ScreeningStorage, ScreeningStorageError
from .specialists import (
    SPECIALIST_PROMPT_VERSIONS,
    SpecialistCallResult,
    SpecialistCondition,
    SpecialistKind,
    run_specialist,
    specialist_for_region,
)

logger = logging.getLogger(__name__)

# Bounded listing per cycle: keeps the existing-keys lookup finite while a
# year of daily uploads accumulates; scale-out is S3 event notifications.
MAX_LISTED_KEYS_PER_CYCLE = 5_000

# A crashed worker leaves PROCESSING rows behind; reclaim after this long.
STALE_PROCESSING_AFTER = dt.timedelta(minutes=10)

# Provider blips (timeouts, 5xx) are common; permanent failure is rare.
# ERROR rows are retried once the last attempt ages past this horizon.
ERROR_RETRY_AFTER = dt.timedelta(hours=1)

# Slack on top of the presign expiry before an un-PUT upload row is
# terminalized: clock skew between the API host (row created_at) and the
# database, plus a slow final S3 write, must not expire a live upload.
PENDING_SWEEP_SLACK = dt.timedelta(hours=1)


def pending_upload_abandoned_after(settings: Settings) -> dt.timedelta:
    """How long a PENDING row may wait for its bytes.

    A presigned PUT can only ever write bytes while the URL is alive; once
    it has expired (plus slack) the row is permanently unfulfillable and is
    swept to SKIPPED so it cannot occupy the cycle budget forever.
    """
    return (
        dt.timedelta(seconds=settings.screening_presign_expiry_seconds)
        + PENDING_SWEEP_SLACK
    )

# raw/<farm_id>/<YYYY-MM-DD>/[<BUCKET>/]<filename> — farm id, capture date
# and (since the disease-check upload flow) the herd bucket ride the key
# itself, because objects arrive by direct upload, not through the API.
# The bucket segment is optional: legacy keys and no-bucket uploads screen
# with bucket NULL.
_RAW_KEY_PATTERN = re.compile(
    r"^(\d{1,10})/(\d{4}-\d{2}-\d{2})/(?:(?P<bucket>[A-Z][A-Z_]{1,28})/)?[^/]+$"
)


_BUCKET_CODES = frozenset(member.value for member in Bucket)


class ParsedRawKey(NamedTuple):
    farm_id: int
    captured_date: dt.date
    bucket: str | None = None


def parse_raw_key(key: str, prefix: str) -> ParsedRawKey | None:
    """Key under the configured prefix → (farm_id, captured_date, bucket)
    or None for anything malformed."""
    if not key.startswith(f"{prefix}/"):
        return None
    match = _RAW_KEY_PATTERN.fullmatch(key[len(prefix) + 1 :])
    if match is None:
        return None
    bucket = match.group("bucket")
    if bucket is not None and bucket not in _BUCKET_CODES:
        # A bucket-shaped segment that is not a herd bucket code is a
        # misplaced key, not a whole-farm photo with a slashed filename.
        return None
    try:
        return ParsedRawKey(
            farm_id=int(match.group(1)),
            captured_date=dt.date.fromisoformat(match.group(2)),
            bucket=bucket,
        )
    except ValueError:
        # A malformed date segment is a misplaced key, not a crash.
        return None


def normalized_derivative_key(farm_id: int, captured_date: dt.date, sha256: str) -> str:
    """Deterministic key for the model-sized derivative of a raw upload."""
    return f"screening/{farm_id}/{captured_date.isoformat()}/{sha256[:16]}.jpg"


@dataclass
class CycleSummary:
    listed: int = 0
    unparseable_keys: int = 0
    unknown_farm_keys: int = 0
    claimed: int = 0
    healthy: int = 0
    flagged: int = 0
    skipped: int = 0
    errors: int = 0
    retried_errors: int = 0
    expired_uploads: int = 0
    notes: list[str] = field(default_factory=list)


async def _expire_abandoned_uploads(
    db: AsyncSession, now: dt.datetime, abandoned_after: dt.timedelta
) -> int:
    """Terminalize PENDING rows whose presigned URL expired long ago.

    Without this, every abandoned walkthrough (URL minted, phone never
    uploaded) permanently occupies a slot in the per-cycle budget — old
    rows sort first, so once they reach ``max_images_per_cycle`` no new
    key is ever claimed again. SKIPPED rows are terminal: the bytes can
    no longer arrive, because the URL that could write them is dead.
    """
    result = cast(
        CursorResult[Any],
        await db.execute(
            update(ScreeningImage)
            .where(
                ScreeningImage.status == ScreeningImageStatus.PENDING.value,
                ScreeningImage.created_at < now - abandoned_after,
            )
            .values(
                status=ScreeningImageStatus.SKIPPED.value,
                error="presigned upload never arrived (URL expired)",
            )
        ),
    )
    return int(result.rowcount or 0)


async def _claim_retry_rows(
    db: AsyncSession,
    limit: int,
    now: dt.datetime,
    abandoned_after: dt.timedelta,
) -> tuple[list[ScreeningImage], int]:
    """PENDING uploads, stale PROCESSING claims and aged ERROR rows, oldest first.

    PENDING rows are how the presigned-upload flow pre-registers photos:
    the API inserts the row when it mints the URL, the phone PUTs the
    bytes, and the next cycle claims the row (skipping it quietly if the
    object has not landed yet). PENDING rows older than the presign
    expiry are left for ``_expire_abandoned_uploads`` instead.

    The claim is a durable commit (rows go to PROCESSING inside this
    transaction): two workers running side by side cannot claim the same
    row, because the second one's ``FOR UPDATE SKIP LOCKED`` scan skips
    rows the first still holds and no longer sees the ones it committed.
    A worker that crashes mid-cycle leaves PROCESSING rows that the stale
    reclaim below picks up.

    Returns the claimed rows plus how many of them were ERROR retries.
    """
    retry_horizon = now - ERROR_RETRY_AFTER
    stale_horizon = now - STALE_PROCESSING_AFTER
    pending_horizon = now - abandoned_after
    latest_run = (
        select(
            ScreeningRun.image_id.label("image_id"),
            func.max(ScreeningRun.created_at).label("last_at"),
        )
        .where(ScreeningRun.stage == ScreeningStage.GATE.value)
        .group_by(ScreeningRun.image_id)
        .subquery()
    )
    result = await db.execute(
        select(ScreeningImage)
        .outerjoin(latest_run, latest_run.c.image_id == ScreeningImage.id)
        .where(
            (
                (ScreeningImage.status == ScreeningImageStatus.PENDING.value)
                & (ScreeningImage.created_at >= pending_horizon)
            )
            | (
                (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value)
                & (ScreeningImage.updated_at < stale_horizon)
            )
            | (
                (ScreeningImage.status == ScreeningImageStatus.ERROR.value)
                & (latest_run.c.last_at.is_(None) | (latest_run.c.last_at < retry_horizon))
            )
        )
        .order_by(ScreeningImage.created_at.asc())
        .limit(limit)
        # Only screening_images rows are locked; the outer-joined subquery
        # stays unlocked (PostgreSQL forbids locking its nullable side).
        .with_for_update(skip_locked=True, of=ScreeningImage)
    )
    rows = list(result.scalars())
    error_retries = sum(
        1 for row in rows if row.status == ScreeningImageStatus.ERROR.value
    )
    for row in rows:
        row.status = ScreeningImageStatus.PROCESSING.value
        row.error = None
    await db.flush()
    await db.commit()  # durable claim: released lock, visible PROCESSING
    return rows, error_retries


async def run_screening_cycle(
    db: AsyncSession,
    settings: Settings,
    storage: ScreeningStorage,
    rotation: ProviderRotation,
) -> CycleSummary:
    summary = CycleSummary()

    try:
        keys = await asyncio.to_thread(
            storage.list_object_keys,
            settings.screening_s3_prefix,
            MAX_LISTED_KEYS_PER_CYCLE,
        )
    except ScreeningStorageError as exc:
        summary.notes.append(f"listing failed: {exc}")
        return summary
    summary.listed = len(keys)

    parsed: dict[str, ParsedRawKey] = {}
    for key in keys:
        if (candidate := parse_raw_key(key, settings.screening_s3_prefix)) is not None:
            parsed[key] = candidate
        else:
            summary.unparseable_keys += 1

    # Only keys whose farm exists are ever inserted (FK); a typo'd farm id in
    # the key stays a warning instead of a poison row.
    candidate_farm_ids = {candidate.farm_id for candidate in parsed.values()}
    known_farm_ids: set[int] = set()
    if candidate_farm_ids:
        known_farm_ids = set(
            (await db.execute(select(Farm.id).where(Farm.id.in_(candidate_farm_ids)))).scalars()
        )
    claimable_keys = [k for k, c in parsed.items() if c.farm_id in known_farm_ids]
    summary.unknown_farm_keys = len(parsed) - len(claimable_keys)
    if summary.unknown_farm_keys:
        summary.notes.append(
            f"{summary.unknown_farm_keys} keys under unknown farm ids were ignored"
        )

    budget = settings.screening_max_images_per_cycle
    abandoned_after = pending_upload_abandoned_after(settings)
    # Expire abandoned uploads BEFORE claiming (same transaction): without
    # the sweep, stale PENDING rows would monopolize the claim below.
    expired = await _expire_abandoned_uploads(db, utcnow(), abandoned_after)
    if expired:
        summary.expired_uploads = expired
        summary.notes.append(
            f"{expired} abandoned PENDING uploads expired to SKIPPED "
            f"(presigned URL older than {abandoned_after})"
        )
    # Pre-registered rows first (fresh PENDING uploads, stale claims, aged
    # errors). The claim is committed durably and new rows below insert
    # straight to PROCESSING, so no row can ever be processed twice — not
    # within one cycle, and not across two workers sharing the database.
    claimed, error_retries = await _claim_retry_rows(db, budget, utcnow(), abandoned_after)
    summary.retried_errors += error_retries

    already_claimed: set[str] = set()
    if claimable_keys:
        already_claimed = set(
            (
                await db.execute(
                    select(ScreeningImage.s3_key).where(
                        ScreeningImage.s3_bucket == storage.bucket,
                        ScreeningImage.s3_key.in_(claimable_keys),
                    )
                )
            ).scalars()
        )

    new_keys = [key for key in claimable_keys if key not in already_claimed][
        : max(0, budget - len(claimed))
    ]
    if new_keys:
        # ON CONFLICT DO NOTHING: a second worker (or a pre-registered
        # upload row for the same key) loses the race silently instead of
        # failing the cycle. Surviving rows insert directly as PROCESSING —
        # the durable claim — so the commit below publishes them claimed.
        inserted = await db.execute(
            pg_insert(ScreeningImage)
            .values(
                [
                    {
                        "farm_id": parsed[key].farm_id,
                        "bucket": parsed[key].bucket,
                        "s3_bucket": storage.bucket,
                        "s3_key": key,
                        "captured_date": parsed[key].captured_date,
                        "status": ScreeningImageStatus.PROCESSING.value,
                    }
                    for key in new_keys
                ]
            )
            .on_conflict_do_nothing(index_elements=["s3_bucket", "s3_key"])
            .returning(ScreeningImage.id)
        )
        inserted_ids = list(inserted.scalars())
        await db.commit()
        if inserted_ids:
            claimed.extend(
                (
                    await db.execute(
                        select(ScreeningImage).where(ScreeningImage.id.in_(inserted_ids))
                    )
                ).scalars()
            )

    summary.claimed = len(claimed)
    for image in claimed:
        try:
            await _process_image(db, settings, storage, rotation, image, summary)
        except Exception as exc:
            logger.exception("screening image %s failed unexpectedly", image.id)
            image.status = ScreeningImageStatus.ERROR.value
            image.error = f"unexpected pipeline failure: {exc}"
            summary.errors += 1
        await db.commit()

    return summary


def _record_run(
    image: ScreeningImage,
    *,
    stage: str,
    provider: str,
    model: str,
    prompt_version: str,
    run_status: str = ScreeningRunStatus.OK.value,
    verdict: str | None = None,
    confidence: float | None = None,
    latency_ms: int | None = None,
    detail: dict[str, object] | None = None,
    error: str | None = None,
    crop_id: int | None = None,
) -> ScreeningRun:
    return ScreeningRun(
        farm_id=image.farm_id,
        image_id=image.id,
        crop_id=crop_id,
        stage=stage,
        run_status=run_status,
        verdict=verdict,
        confidence=None if confidence is None else round(confidence, 3),
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        latency_ms=latency_ms,
        detail=detail,
        error=None if error is None else error[:2000],
    )


def _add_finding(
    image: ScreeningImage,
    run_id: int,
    *,
    region: str | None,
    label: str,
    confidence: float | None,
    severity: str | None = None,
    note: str | None,
    crop_id: int | None = None,
) -> ScreeningFinding:
    return ScreeningFinding(
        farm_id=image.farm_id,
        run_id=run_id,
        crop_id=crop_id,
        region=region,
        label=label[:80],
        confidence=None if confidence is None else round(confidence, 3),
        severity=severity,
        note=note,
        status=ScreeningFindingStatus.PENDING_REVIEW.value,
    )


async def _detect_with_fallback(
    db: AsyncSession,
    rotation: ProviderRotation,
    primary: VisionProvider,
    image: ScreeningImage,
    jpeg: bytes,
    max_crops: int,
) -> list[DetectionBox] | None:
    """Detection via the primary with the rotation as fallback.

    Returns boxes (possibly empty: a genuine no-goat photo), or None when
    every provider failed — the caller then screens the whole photo."""
    chain = [primary, *rotation.fallbacks_for(primary)]
    failures: list[str] = []
    last_error: Exception | None = None
    for provider in chain:
        try:
            answer = await provider.complete(jpeg, DETECT_SYSTEM_PROMPT)
            boxes = parse_detection_response(answer.text, max_crops)
        except (ProviderError, DetectionParseError) as exc:
            failures.append(provider.name)
            last_error = exc
            continue
        db.add(
            _record_run(
                image,
                stage=ScreeningStage.DETECT.value,
                provider=answer.provider,
                model=answer.model,
                prompt_version=DETECT_PROMPT_VERSION,
                latency_ms=answer.latency_ms,
                detail={
                    "goats": len(boxes),
                    "served_by": provider.name,
                    "fallbacks_failed": failures,
                },
            )
        )
        return boxes
    assert last_error is not None
    db.add(
        _record_run(
            image,
            stage=ScreeningStage.DETECT.value,
            provider=primary.name,
            model=primary.model,
            prompt_version=DETECT_PROMPT_VERSION,
            run_status=ScreeningRunStatus.ERROR.value,
            error=str(last_error),
        )
    )
    return None


async def _run_cascade(
    db: AsyncSession,
    settings: Settings,
    rotation: ProviderRotation,
    image: ScreeningImage,
    crop: ScreeningCrop | None,
    jpeg: bytes,
    summary: CycleSummary,
) -> str:
    """Gate → (if flagged) specialists + cross-check over one photo or crop.

    Runs and findings record ``crop`` when given; returns the terminal
    status string (HEALTHY / FLAGGED / ERROR) for the caller to apply.
    """
    today = dt.date.today()
    primary = rotation.primary_for(today)
    crop_id = crop.id if crop is not None else None
    try:
        outcome: GateOutcome = await rotation.gate_with_fallback(primary, jpeg)
    except GateExhaustedError as exc:
        db.add(
            _record_run(
                image,
                stage=ScreeningStage.GATE.value,
                provider=primary.name,
                model=primary.model,
                prompt_version=GATE_PROMPT_VERSION,
                run_status=ScreeningRunStatus.ERROR.value,
                error=str(exc),
                crop_id=crop_id,
            )
        )
        return ScreeningImageStatus.ERROR.value

    gate_result = outcome.result
    gate_run = _record_run(
        image,
        stage=ScreeningStage.GATE.value,
        provider=gate_result.provider,
        model=gate_result.model,
        prompt_version=gate_result.prompt_version,
        verdict="flagged" if gate_result.response.flagged else "healthy",
        confidence=gate_result.response.confidence,
        latency_ms=gate_result.latency_ms,
        detail={
            "served_by": outcome.served_by,
            "fallbacks_failed": list(outcome.failed_providers),
            "quality_problem": gate_result.response.quality_problem,
            "observation_count": len(gate_result.response.observations),
        },
        crop_id=crop_id,
    )
    db.add(gate_run)

    if not gate_result.response.flagged:
        # The cascade stops here: one call, filed as healthy.
        return ScreeningImageStatus.HEALTHY.value

    await db.flush()  # gate_run.id backs the cross-check provenance below

    observations = gate_result.response.observations
    # One specialist call per body-region kind per photo/crop: kind per
    # observation, order preserved, duplicates collapsed.
    kinds: list[tuple[SpecialistKind, str | None]] = []
    seen_kinds: set[SpecialistKind] = set()
    for observation in observations:
        kind = specialist_for_region(observation.region)
        if kind not in seen_kinds:
            seen_kinds.add(kind)
            kinds.append((kind, observation.region))
    if not kinds:
        # Flagged with no observations: still worth the whole-animal look.
        kinds = [(SpecialistKind.GENERAL, None)]

    # ---- specialists on the provider that served the gate ---------------
    serving = rotation.provider_named(gate_result.provider)
    specialist_conditions: list[tuple[int, str | None, SpecialistCondition]] = []
    for kind, region in kinds:
        if serving is None:
            continue
        try:
            specialist: SpecialistCallResult = await run_specialist(serving, jpeg, kind)
        except ProviderError as exc:
            db.add(
                _record_run(
                    image,
                    stage=kind.value,
                    provider=serving.name,
                    model=serving.model,
                    prompt_version=SPECIALIST_PROMPT_VERSIONS[kind],
                    run_status=ScreeningRunStatus.ERROR.value,
                    error=str(exc),
                    crop_id=crop_id,
                )
            )
            summary.notes.append(f"specialist {kind.value} failed for image {image.id}")
            continue
        specialist_run = _record_run(
            image,
            stage=kind.value,
            provider=specialist.provider,
            model=specialist.model,
            prompt_version=specialist.prompt_version,
            latency_ms=specialist.latency_ms,
            detail={"condition_count": len(specialist.response.conditions)},
            crop_id=crop_id,
        )
        db.add(specialist_run)
        await db.flush()
        for condition in specialist.response.conditions:
            specialist_conditions.append((specialist_run.id, region, condition))

    if specialist_conditions:
        for run_id, region, condition in specialist_conditions:
            db.add(
                _add_finding(
                    image,
                    run_id,
                    region=region,
                    label=condition.disease,
                    confidence=condition.confidence,
                    severity=condition.severity,
                    note=condition.note,
                    crop_id=crop_id,
                )
            )
    else:
        # No specialist output survived: the gate's own observations remain
        # the findings so the review queue is never silently empty.
        for observation in observations or []:
            db.add(
                _add_finding(
                    image,
                    gate_run.id,
                    region=observation.region,
                    label=observation.label,
                    confidence=observation.confidence,
                    note=observation.note,
                    crop_id=crop_id,
                )
            )

    # ---- cross-check from the next provider in the rotation -------------
    secondary = rotation.secondary_for(today)
    if secondary is not None and secondary.name != gate_result.provider:
        try:
            check = await run_gate(secondary, jpeg)
            db.add(
                _record_run(
                    image,
                    stage=ScreeningStage.CROSS_CHECK.value,
                    provider=check.provider,
                    model=check.model,
                    prompt_version=check.prompt_version,
                    verdict="flagged" if check.response.flagged else "healthy",
                    confidence=check.response.confidence,
                    latency_ms=check.latency_ms,
                    detail={"cross_check_of": gate_run.id, "agrees": check.response.flagged},
                    crop_id=crop_id,
                )
            )
        except ProviderError as exc:
            db.add(
                _record_run(
                    image,
                    stage=ScreeningStage.CROSS_CHECK.value,
                    provider=secondary.name,
                    model=secondary.model,
                    prompt_version=GATE_PROMPT_VERSION,
                    run_status=ScreeningRunStatus.ERROR.value,
                    error=str(exc),
                    crop_id=crop_id,
                )
            )

    return ScreeningImageStatus.FLAGGED.value


def _aggregate_crop_statuses(statuses: list[str]) -> str:
    """Photo status from its crops': any flag wins, then any error (retry
    re-screens just the errored crops), else healthy."""
    if ScreeningImageStatus.FLAGGED.value in statuses:
        return ScreeningImageStatus.FLAGGED.value
    if ScreeningImageStatus.ERROR.value in statuses:
        return ScreeningImageStatus.ERROR.value
    return ScreeningImageStatus.HEALTHY.value if statuses else ScreeningImageStatus.ERROR.value


async def _process_image(
    db: AsyncSession,
    settings: Settings,
    storage: ScreeningStorage,
    rotation: ProviderRotation,
    image: ScreeningImage,
    summary: CycleSummary,
) -> None:
    # The claim already committed the row as PROCESSING (error cleared);
    # re-asserting here keeps this function safe if it is ever handed a
    # PENDING row directly.
    image.status = ScreeningImageStatus.PROCESSING.value
    image.error = None
    await db.flush()

    try:
        raw = await asyncio.to_thread(storage.download, image.s3_key)
    except ScreeningObjectMissingError:
        # A presigned-upload row whose bytes have not landed yet (phone
        # still on a slow link, or an abandoned URL): quietly stay PENDING
        # and let a later cycle look again — never an error.
        image.status = ScreeningImageStatus.PENDING.value
        image.error = None
        summary.notes.append(f"object not uploaded yet: {image.s3_key!r}")
        return
    except ScreeningStorageError as exc:
        image.status = ScreeningImageStatus.ERROR.value
        image.error = str(exc)
        summary.errors += 1
        return

    try:
        normalized: NormalizedImage = await asyncio.to_thread(
            normalize_image, raw, settings.screening_image_max_edge_px
        )
    except ImageNormalizationError as exc:
        image.status = ScreeningImageStatus.ERROR.value
        image.error = f"normalization failed: {exc}"
        summary.errors += 1
        return

    # Identical bytes re-uploaded under a new key are not re-billed.
    duplicate = (
        await db.execute(
            select(func.count())
            .select_from(ScreeningImage)
            .where(
                ScreeningImage.farm_id == image.farm_id,
                ScreeningImage.sha256 == normalized.sha256,
                ScreeningImage.id != image.id,
                ScreeningImage.status.in_(
                    [
                        ScreeningImageStatus.HEALTHY.value,
                        ScreeningImageStatus.FLAGGED.value,
                        ScreeningImageStatus.SKIPPED.value,
                    ]
                ),
            )
        )
    ).scalar_one()
    if duplicate:
        image.status = ScreeningImageStatus.SKIPPED.value
        image.error = "duplicate: identical bytes already screened for this farm"
        image.sha256 = normalized.sha256
        summary.skipped += 1
        return

    captured = image.captured_date or dt.date.today()
    derivative_key = normalized_derivative_key(image.farm_id, captured, normalized.sha256)
    try:
        await asyncio.to_thread(storage.upload, derivative_key, normalized.data, "image/jpeg")
    except ScreeningStorageError as exc:
        # The gate could still run on the raw bytes, but then the review UI
        # would have no bounded image to show; fail and retry the whole image.
        image.status = ScreeningImageStatus.ERROR.value
        image.error = str(exc)
        summary.errors += 1
        return

    image.sha256 = normalized.sha256
    image.width = normalized.width
    image.height = normalized.height
    image.byte_size = normalized.byte_size
    image.normalized_key = derivative_key

    today = dt.date.today()
    primary = rotation.primary_for(today)

    # ---- Phase 3: split multi-goat photos, or screen the whole photo ----
    existing_crops = list(
        (
            await db.execute(
                select(ScreeningCrop)
                .where(
                    ScreeningCrop.farm_id == image.farm_id,
                    ScreeningCrop.image_id == image.id,
                )
                .order_by(ScreeningCrop.crop_index)
            )
        ).scalars()
    )

    if not settings.screening_crop_detection_enabled and not existing_crops:
        status = await _run_cascade(db, settings, rotation, image, None, normalized.data, summary)
        image.status = status
        if status == ScreeningImageStatus.FLAGGED.value:
            summary.flagged += 1
        elif status == ScreeningImageStatus.HEALTHY.value:
            summary.healthy += 1
        else:
            image.error = "gate stage failed for every provider"
            summary.errors += 1
        return

    boxes: list[DetectionBox] | None
    if existing_crops:
        # A retried photo keeps its original boxes; only non-terminal crops
        # re-screen, so already-flagged goats never produce duplicate findings.
        boxes = [
            DetectionBox(crop.box_x, crop.box_y, crop.box_w, crop.box_h) for crop in existing_crops
        ]
    else:
        boxes = await _detect_with_fallback(
            db, rotation, primary, image, normalized.data, settings.screening_max_crops_per_image
        )

    if not boxes:
        # No goats detected (or detection failed on every provider): the
        # whole photo screens as one unit — a detection miss must never
        # leave a herd un-screened.
        status = await _run_cascade(db, settings, rotation, image, None, normalized.data, summary)
        image.status = status
        if status == ScreeningImageStatus.FLAGGED.value:
            summary.flagged += 1
        elif status == ScreeningImageStatus.HEALTHY.value:
            summary.healthy += 1
        else:
            image.error = "gate stage failed for every provider"
            summary.errors += 1
        return

    crops = existing_crops or [
        ScreeningCrop(
            farm_id=image.farm_id,
            image_id=image.id,
            crop_index=index,
            box_x=box.x,
            box_y=box.y,
            box_w=box.w,
            box_h=box.h,
            status=ScreeningImageStatus.PENDING.value,
        )
        for index, box in enumerate(boxes)
    ]
    for crop in crops:
        if crop not in db:
            db.add(crop)
    await db.flush()

    crop_statuses: list[str] = []
    for crop, box in zip(crops, boxes, strict=True):
        if crop.status not in (
            ScreeningImageStatus.PENDING.value,
            ScreeningImageStatus.ERROR.value,
        ):
            crop_statuses.append(crop.status)
            continue
        crop.status = ScreeningImageStatus.PROCESSING.value
        crop.error = None
        await db.flush()
        try:
            cropped: CroppedImage = await asyncio.to_thread(
                crop_image, normalized.data, (box.x, box.y, box.w, box.h)
            )
        except CropError as exc:
            crop.status = ScreeningImageStatus.ERROR.value
            crop.error = str(exc)
            crop_statuses.append(ScreeningImageStatus.ERROR.value)
            continue
        crop_derivative = (
            f"screening/{image.farm_id}/{captured.isoformat()}/"
            f"{normalized.sha256[:16]}-c{crop.crop_index}.jpg"
        )
        try:
            await asyncio.to_thread(storage.upload, crop_derivative, cropped.data, "image/jpeg")
        except ScreeningStorageError as exc:
            crop.status = ScreeningImageStatus.ERROR.value
            crop.error = str(exc)
            crop_statuses.append(ScreeningImageStatus.ERROR.value)
            continue
        crop.sha256 = cropped.sha256
        crop.normalized_key = crop_derivative
        status = await _run_cascade(db, settings, rotation, image, crop, cropped.data, summary)
        crop.status = status
        if status == ScreeningImageStatus.ERROR.value:
            crop.error = "gate stage failed for every provider"
        crop_statuses.append(status)

    image.status = _aggregate_crop_statuses(crop_statuses)
    if image.status == ScreeningImageStatus.FLAGGED.value:
        summary.flagged += 1
    elif image.status == ScreeningImageStatus.HEALTHY.value:
        summary.healthy += 1
    else:
        image.error = "one or more goats could not be screened (see crop rows)"
        summary.errors += 1
