"""The screening cycle: claim registered uploads, normalize, cascade.

Cascade (Phase 3): a detection call splits multi-goat photos into
per-goat crops; each crop (or the whole photo when detection finds nothing)
runs gate → (if flagged) specialists + cross-check. Healthy verdicts stop
the cascade per goat; findings land in the vet review queue.

One cycle is bounded (``screening_max_images_per_cycle``) and idempotent.
The API pre-registers every accepted object; raw-prefix listing is never an
intake authority, so a bucket writer cannot invent a tenant image row. Stale
PROCESSING rows (a crashed worker), ERROR rows whose last attempt is at least
an hour old, and FLAGGED rows with an errored crop are (re)claimed so transient
failures self-heal without hiding a valid flag. PENDING direct-POST rows whose
presigned form expired long ago are swept to SKIPPED — an abandoned walkthrough
must not occupy the cycle budget forever. Tokenless historical rows remain
eligible to drain. Claims are committed durably with
``FOR UPDATE SKIP LOCKED``, so even two workers sharing the database never
process the same photo twice.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hmac
import logging
import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple, cast

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import ScreeningRuntimeSettings
from ...models import (
    Farm,
    ScreeningContentClaim,
    ScreeningCrop,
    ScreeningFinding,
    ScreeningImage,
    ScreeningRun,
)
from ...models.enums import (
    Bucket,
    ScreeningFindingStatus,
    ScreeningImageStatus,
    ScreeningRunStatus,
    ScreeningStage,
)
from ...utils import today, utcnow
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
from .s3 import (
    ScreeningObjectChangedError,
    ScreeningObjectMissingError,
    ScreeningObjectTooLargeError,
    ScreeningStorage,
    ScreeningStorageError,
)
from .specialists import (
    SPECIALIST_PROMPT_VERSIONS,
    SpecialistCallResult,
    SpecialistCondition,
    SpecialistKind,
    run_specialist,
    specialist_for_region,
)

logger = logging.getLogger(__name__)

# A crashed worker leaves PROCESSING rows behind; reclaim after this long.
STALE_PROCESSING_AFTER = dt.timedelta(minutes=10)

# Provider blips (timeouts, 5xx) are common; permanent failure is rare.
# ERROR rows are retried once their last transition — updated_at, refreshed
# by onupdate on every status change — ages past this horizon. Keying on the
# image row (not the GATE-run audit trail) also backs off rows that errored
# before any run was recorded; the run-based horizon used to leave those
# claimable on every cycle, starving the budget of fresh photos.
ERROR_RETRY_AFTER = dt.timedelta(hours=1)

# Slack on top of the presign expiry before an un-PUT upload row is
# terminalized: clock skew between the API host (row created_at) and the
# database, plus a slow final S3 write, must not expire a live upload.
PENDING_SWEEP_SLACK = dt.timedelta(hours=1)

# Downloads are sized by HEAD before any byte is transferred: an object
# larger than this is terminally SKIPPED (a misdirected video, a hostile
# upload) rather than pulled into worker memory. Photos are ~20 MB, so
# 25 MB keeps every legitimate shot while capping the blast radius.
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024

# A PENDING pre-registration with no object should not be HEADed every
# polling interval forever.  It becomes eligible again shortly, while the
# expiry sweep remains the terminal outcome after the form can no longer PUT.
PENDING_OBJECT_RETRY_AFTER = dt.timedelta(minutes=5)


def pending_upload_abandoned_after(settings: ScreeningRuntimeSettings) -> dt.timedelta:
    """How long a PENDING row may wait for its bytes.

    A presigned POST form can only ever write bytes while it is alive; once
    it has expired (plus slack) the row is permanently unfulfillable and is
    swept to SKIPPED so it cannot occupy the cycle budget forever.
    """
    return dt.timedelta(seconds=settings.screening_presign_expiry_seconds) + PENDING_SWEEP_SLACK


# Historical raw-key parser retained for migration/diagnostic tooling.  The
# worker no longer turns parsed keys into rows; only API pre-registrations are
# eligible for processing.  The bucket segment remains optional for legacy
# records whose key had no herd bucket.
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
    # The object is immutable by content address.  A short digest prefix is
    # convenient for logs but is not an integrity boundary: a farm member who
    # can upload photos could deliberately find a prefix collision and
    # overwrite an earlier review derivative.  Keep the full digest in the
    # key so the derivative referenced by a reviewed finding is genuinely
    # content-addressed.
    return f"screening/{farm_id}/{captured_date.isoformat()}/{sha256}.jpg"


def cropped_derivative_key(
    farm_id: int, captured_date: dt.date, image_sha256: str, crop_index: int
) -> str:
    """Deterministic, full-digest key for one per-goat derivative."""
    return f"screening/{farm_id}/{captured_date.isoformat()}/{image_sha256}-c{crop_index}.jpg"


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


# One bounded expiry batch per *cycle*: a huge abandoned backlog must not
# delay fresh animal screening while a worker drains it in a single DB
# transaction.
_EXPIRED_UPLOAD_BATCH_SIZE = 500


async def _expire_abandoned_uploads(
    db: AsyncSession, now: dt.datetime, abandoned_after: dt.timedelta
) -> int:
    """Terminalize direct-POST PENDING rows whose presigned URL expired long ago.

    Without this, every abandoned walkthrough (URL minted, phone never
    uploaded) permanently occupies a slot in the per-cycle budget — old
    rows sort first, so once they reach ``max_images_per_cycle`` no new
    key is ever claimed again. SKIPPED rows are terminal: the bytes can
    no longer arrive, because the URL that could write them is dead. Tokenless
    rows were created before that intake flow existed: they may already have
    objects to drain, so they are deliberately excluded.

    At most one bounded ``FOR UPDATE SKIP LOCKED`` batch is retired per
    worker cycle.  Candidates are materialized and locked before the UPDATE
    so PostgreSQL cannot re-evaluate a LIMIT mid-statement, concurrent
    workers' locked rows are skipped instead of blocked on, and a hostile
    backlog cannot turn this maintenance step into an unbounded transaction.
    The caller's claim commit publishes the sweep.
    """
    candidates = (
        select(ScreeningImage.id)
        .where(
            ScreeningImage.status == ScreeningImageStatus.PENDING.value,
            ScreeningImage.upload_token.is_not(None),
            ScreeningImage.created_at < now - abandoned_after,
        )
        .order_by(ScreeningImage.created_at, ScreeningImage.id)
        .limit(_EXPIRED_UPLOAD_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    candidate_ids = list((await db.execute(candidates)).scalars())
    if not candidate_ids:
        return 0
    result = cast(
        CursorResult[Any],
        await db.execute(
            update(ScreeningImage)
            .where(ScreeningImage.id.in_(candidate_ids))
            .values(
                status=ScreeningImageStatus.SKIPPED.value,
                error="presigned upload never arrived (URL expired)",
                next_attempt_at=None,
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
    the API inserts the row when it mints the form, the phone POSTs the
    bytes, and the next cycle claims the row (skipping it quietly if the
    object has not landed yet). PENDING rows older than the presign
    expiry are left for ``_expire_abandoned_uploads`` instead. Tokenless
    historical rows have no expiring form, so they remain eligible to drain
    even after that horizon.

    The claim is a durable commit (rows go to PROCESSING inside this
    transaction): two workers running side by side cannot claim the same
    row, because the second one's ``FOR UPDATE SKIP LOCKED`` scan skips
    rows the first still holds and no longer sees the ones it committed.
    A worker that crashes mid-cycle leaves PROCESSING rows that the stale
    reclaim below picks up. ERROR rows back off on the image's own
    ``updated_at`` — refreshed on every status transition — so rows that
    failed before any run row was written are also held back for
    ``ERROR_RETRY_AFTER`` instead of being retried every cycle.

    Returns the claimed rows plus how many of them were ERROR retries.
    """
    retry_horizon = now - ERROR_RETRY_AFTER
    stale_horizon = now - STALE_PROCESSING_AFTER
    pending_horizon = now - abandoned_after
    partial_crop_error = exists(
        select(ScreeningCrop.id).where(
            ScreeningCrop.farm_id == ScreeningImage.farm_id,
            ScreeningCrop.image_id == ScreeningImage.id,
            ScreeningCrop.status == ScreeningImageStatus.ERROR.value,
        )
    )
    eligible = (
        (
            (ScreeningImage.status == ScreeningImageStatus.PENDING.value)
            & or_(
                ScreeningImage.upload_token.is_(None),
                ScreeningImage.created_at >= pending_horizon,
            )
            & or_(
                ScreeningImage.next_attempt_at.is_(None),
                ScreeningImage.next_attempt_at <= now,
            )
        )
        | (
            (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value)
            & (ScreeningImage.updated_at < stale_horizon)
        )
        | (
            (ScreeningImage.status == ScreeningImageStatus.ERROR.value)
            & (ScreeningImage.updated_at < retry_horizon)
        )
        # A flagged photo can contain a successfully flagged goat *and* an
        # errored one.  Keep its visible FLAGGED status, but make it
        # retryable until every crop is terminal; otherwise the error crop
        # is silently stranded forever.
        | (
            (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value)
            & (ScreeningImage.updated_at < retry_horizon)
            & partial_crop_error
        )
    )
    # Global oldest-first claim order let one busy farm fill every worker
    # cycle.  Rank candidates inside each farm first, then take rank 1 from
    # every farm before rank 2.  The outer SELECT locks real image rows, not
    # the window subquery, so PostgreSQL can still SKIP LOCKED safely.
    ranked_candidates = (
        select(
            ScreeningImage.id.label("image_id"),
            ScreeningImage.created_at.label("created_at"),
            func.row_number()
            .over(
                partition_by=ScreeningImage.farm_id,
                order_by=(ScreeningImage.created_at.asc(), ScreeningImage.id.asc()),
            )
            .label("farm_rank"),
        )
        .where(eligible)
        .subquery()
    )
    result = await db.execute(
        select(ScreeningImage)
        .join(ranked_candidates, ranked_candidates.c.image_id == ScreeningImage.id)
        .order_by(
            ranked_candidates.c.farm_rank,
            ranked_candidates.c.created_at,
            ScreeningImage.id,
        )
        .limit(limit)
        .with_for_update(skip_locked=True, of=ScreeningImage)
    )
    rows = list(result.scalars())
    error_retries = sum(
        1
        for row in rows
        if row.status in (ScreeningImageStatus.ERROR.value, ScreeningImageStatus.FLAGGED.value)
    )
    for row in rows:
        row.status = ScreeningImageStatus.PROCESSING.value
        row.error = None
        row.next_attempt_at = None
    await db.flush()
    await db.commit()  # durable claim: released lock, visible PROCESSING
    return rows, error_retries


async def run_screening_cycle(
    db: AsyncSession,
    settings: ScreeningRuntimeSettings,
    storage: ScreeningStorage,
    rotation: ProviderRotation,
) -> CycleSummary:
    summary = CycleSummary()

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
    # The API pre-registers every accepted upload.  Deliberately do *not*
    # discover and insert arbitrary keys from the raw prefix here: anyone
    # with another bucket writer or a leaked object credential could otherwise
    # manufacture tenant-owned screening rows merely by choosing a farm id in
    # a key.  Existing registered rows remain the sole intake authority.
    claimed, error_retries = await _claim_retry_rows(db, budget, utcnow(), abandoned_after)
    summary.retried_errors += error_retries

    summary.claimed = len(claimed)
    farm_timezones: dict[int, str] = {}
    if claimed:
        timezone_rows = (
            await db.execute(
                select(Farm.id, Farm.timezone).where(
                    Farm.id.in_({image.farm_id for image in claimed})
                )
            )
        ).all()
        farm_timezones = {int(farm_id): timezone for farm_id, timezone in timezone_rows}
    for image in claimed:
        try:
            # Farm FK integrity guarantees this lookup.  The defensive India
            # fallback prevents a corrupt legacy row from crashing the whole
            # worker cycle while preserving existing date-helper behavior.
            business_today = today(farm_timezones.get(image.farm_id, "Asia/Kolkata"))
            await _process_image(
                db,
                settings,
                storage,
                rotation,
                image,
                summary,
                business_today,
            )
        except Exception as exc:
            logger.exception("screening image %s failed unexpectedly", image.id)
            # A DB-level failure inside _process_image aborts the
            # transaction; roll back BEFORE mutating the ORM object, or the
            # commit below would raise PendingRollbackError and strand every
            # remaining image of the cycle behind the aborted transaction.
            await db.rollback()
            image.status = ScreeningImageStatus.ERROR.value
            image.error = f"unexpected pipeline failure: {exc}"
            summary.errors += 1
        try:
            await db.commit()
        except Exception:
            # The per-image boundary is deliberate: one uncommittable row
            # (constraint, connection loss) must not take the whole cycle
            # down. Log, roll back so the next iteration starts clean, and
            # keep screening — the row stays PROCESSING and the stale-claim
            # reclaim retries it later.
            logger.exception("committing screening image %s failed", image.id)
            await db.rollback()

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
    if last_error is None:
        # ``chain`` always contains primary, so normal execution either
        # returned above or captured a provider/contract error. Keep a
        # corrupt/custom provider implementation from turning that invariant
        # into an AssertionError when Python runs with ``-O``.
        last_error = ProviderError("detection provider chain ended without a result")
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
    settings: ScreeningRuntimeSettings,
    rotation: ProviderRotation,
    image: ScreeningImage,
    crop: ScreeningCrop | None,
    jpeg: bytes,
    summary: CycleSummary,
    business_today: dt.date,
) -> str:
    """Gate → (if flagged) specialists + cross-check over one photo or crop.

    Runs and findings record ``crop`` when given; returns the terminal
    status string (HEALTHY / FLAGGED / ERROR) for the caller to apply.
    """
    primary = rotation.primary_for(business_today)
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
    secondary = rotation.secondary_for(business_today)
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
    """Photo status from its crops: retain a visible flag when any goat flags.

    A FLAGGED parent with an ERROR crop is deliberately non-terminal for the
    claim query above.  That keeps review urgency visible without sacrificing
    the retry path for the goat whose cascade failed.
    """
    if ScreeningImageStatus.FLAGGED.value in statuses:
        return ScreeningImageStatus.FLAGGED.value
    if ScreeningImageStatus.ERROR.value in statuses:
        return ScreeningImageStatus.ERROR.value
    return ScreeningImageStatus.HEALTHY.value if statuses else ScreeningImageStatus.ERROR.value


async def _content_claim_owner(db: AsyncSession, farm_id: int, sha256: str) -> int | None:
    """Return the image that durably owns a farm-local content digest."""
    return (
        await db.execute(
            select(ScreeningContentClaim.image_id).where(
                ScreeningContentClaim.farm_id == farm_id,
                ScreeningContentClaim.sha256 == sha256,
            )
        )
    ).scalar_one_or_none()


async def _reserve_normalized_content(
    db: AsyncSession, image: ScreeningImage, sha256: str
) -> int | None:
    """Atomically choose a canonical image for normalized bytes.

    The unique ``(farm_id, sha256)`` constraint is the cross-worker boundary
    on both PostgreSQL and SQLite.  A savepoint keeps a losing insert from
    poisoning the image transaction.  The winning reservation is committed
    before any network/model work: a second worker can promptly skip instead
    of waiting for an expensive cascade, while a crashed winner is retried as
    the same canonical image.

    ``None`` means this image already owns a different digest, which is an
    immutable-input violation rather than a reason to bill a new model call.
    """
    owner_id = await _content_claim_owner(db, image.farm_id, sha256)
    if owner_id is not None:
        if owner_id == image.id and image.sha256 != sha256:
            image.sha256 = sha256
            await db.commit()
        return owner_id

    try:
        async with db.begin_nested():
            db.add(
                ScreeningContentClaim(
                    farm_id=image.farm_id,
                    image_id=image.id,
                    sha256=sha256,
                )
            )
            await db.flush()
    except IntegrityError:
        # The concurrent winner has committed by the time its unique-index
        # conflict is reported.  Query it after the savepoint rollback; the
        # outer transaction remains usable on PostgreSQL and SQLite.
        owner_id = await _content_claim_owner(db, image.farm_id, sha256)
        if owner_id is not None:
            return owner_id
        # The other unique key is (farm_id, image_id): a prior successful
        # reservation for this image must not be silently rebound to new
        # bytes after its raw object changed.
        image_claim = (
            await db.execute(
                select(ScreeningContentClaim.id).where(
                    ScreeningContentClaim.farm_id == image.farm_id,
                    ScreeningContentClaim.image_id == image.id,
                )
            )
        ).scalar_one_or_none()
        if image_claim is not None:
            return None
        raise

    # Persist both sides of the identity binding before remote work.  This
    # intentionally creates a short transaction boundary inside one image's
    # cycle; the outer loop already treats each image as independently durable.
    image.sha256 = sha256
    await db.commit()
    return image.id


async def _process_image(
    db: AsyncSession,
    settings: ScreeningRuntimeSettings,
    storage: ScreeningStorage,
    rotation: ProviderRotation,
    image: ScreeningImage,
    summary: CycleSummary,
    business_today: dt.date,
) -> None:
    # The claim already committed the row as PROCESSING (error cleared);
    # re-asserting here keeps this function safe if it is ever handed a
    # PENDING row directly.
    image.status = ScreeningImageStatus.PROCESSING.value
    image.error = None
    image.next_attempt_at = None
    await db.flush()

    try:
        object_info = await asyncio.to_thread(storage.object_info, image.s3_key)
    except ScreeningStorageError as exc:
        # The size probe rides the same failure contract as the download.
        image.status = ScreeningImageStatus.ERROR.value
        image.error = str(exc)
        summary.errors += 1
        return
    if object_info is None:
        # A presigned-upload row whose bytes have not landed yet (phone still
        # on a slow link, or an abandoned URL): quietly stay PENDING and let
        # a later cycle look again — but do not let it consume every cycle.
        image.status = ScreeningImageStatus.PENDING.value
        image.error = None
        image.next_attempt_at = utcnow() + PENDING_OBJECT_RETRY_AFTER
        summary.notes.append(f"object not uploaded yet: {image.s3_key!r}")
        return
    if object_info.size > MAX_DOWNLOAD_BYTES:
        # Terminally SKIPPED, never ERROR: the bytes are a fact about the
        # object, not a transient condition, and the claim predicate only
        # ever re-reads PENDING/PROCESSING/ERROR rows — so an oversized
        # object cannot occupy the retry budget either.
        image.status = ScreeningImageStatus.SKIPPED.value
        image.error = f"object exceeds 25 MB download cap ({object_info.size} bytes)"
        summary.skipped += 1
        return
    if image.upload_token is not None:
        # New direct POSTs carry all three policy-bound facts.  A key alone
        # is never proof of tenant ownership: an out-of-band bucket writer
        # must not be able to inject an image by guessing a farm/key prefix.
        if image.upload_content_type is None:
            image.status = ScreeningImageStatus.SKIPPED.value
            image.error = "upload registration is missing its declared content type"
            summary.skipped += 1
            return
        object_token = object_info.metadata.get("screening-token")
        if object_token is None or not hmac.compare_digest(object_token, image.upload_token):
            image.status = ScreeningImageStatus.SKIPPED.value
            image.error = "object upload token does not match its pre-registration"
            summary.skipped += 1
            return
        if object_info.content_type != image.upload_content_type:
            image.status = ScreeningImageStatus.SKIPPED.value
            image.error = "object content type does not match its pre-registration"
            summary.skipped += 1
            return

    try:
        raw = await asyncio.to_thread(
            storage.download,
            image.s3_key,
            max_bytes=MAX_DOWNLOAD_BYTES,
            etag=object_info.etag,
            version_id=object_info.version_id,
        )
    except ScreeningObjectMissingError:
        # The object disappeared after HEAD.  Treat it like a not-yet-landed
        # upload, with the same bounded retry behavior.
        image.status = ScreeningImageStatus.PENDING.value
        image.error = None
        image.next_attempt_at = utcnow() + PENDING_OBJECT_RETRY_AFTER
        summary.notes.append(f"object not uploaded yet: {image.s3_key!r}")
        return
    except ScreeningObjectChangedError:
        # ETag/Version conditional GET prevented a malicious or merely slow
        # uploader from swapping bytes after validation.  Re-HEAD a stable
        # snapshot later rather than ever decoding unverified content.
        image.status = ScreeningImageStatus.PENDING.value
        image.error = None
        image.next_attempt_at = utcnow() + PENDING_OBJECT_RETRY_AFTER
        summary.notes.append(f"object changed during validated download: {image.s3_key!r}")
        return
    except ScreeningObjectTooLargeError as exc:
        image.status = ScreeningImageStatus.SKIPPED.value
        image.error = str(exc)
        summary.skipped += 1
        return
    except ScreeningStorageError as exc:
        image.status = ScreeningImageStatus.ERROR.value
        image.error = str(exc)
        summary.errors += 1
        return

    try:
        normalized: NormalizedImage = await asyncio.to_thread(
            normalize_image,
            raw,
            settings.screening_image_max_edge_px,
            image.upload_content_type,
        )
    except ImageNormalizationError as exc:
        # Corrupt/hostile bytes are immutable facts about this object.  A
        # retry would repeatedly send the same parser bomb through the
        # worker, so reject it terminally and require a new upload row.
        image.status = ScreeningImageStatus.SKIPPED.value
        image.error = f"image rejected during normalization: {exc}"
        summary.skipped += 1
        return

    # A raw-object overwrite after a prior successful reservation must not
    # turn one screening record into the canonical result for two byte
    # streams.  The current HEAD/conditional GET protects one attempt; this
    # closes the retry-to-retry gap as well.
    if image.sha256 is not None and image.sha256 != normalized.sha256:
        image.status = ScreeningImageStatus.SKIPPED.value
        image.error = "object bytes changed after normalized content was claimed"
        summary.skipped += 1
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

    claim_owner = await _reserve_normalized_content(db, image, normalized.sha256)
    if claim_owner is None:
        image.status = ScreeningImageStatus.SKIPPED.value
        image.error = "object bytes changed after normalized content was claimed"
        summary.skipped += 1
        return
    if claim_owner != image.id:
        image.status = ScreeningImageStatus.SKIPPED.value
        image.error = "duplicate: identical bytes already screened for this farm"
        image.sha256 = normalized.sha256
        summary.skipped += 1
        return

    captured = image.captured_date or business_today
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

    primary = rotation.primary_for(business_today)

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
        status = await _run_cascade(
            db,
            settings,
            rotation,
            image,
            None,
            normalized.data,
            summary,
            business_today,
        )
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
        status = await _run_cascade(
            db,
            settings,
            rotation,
            image,
            None,
            normalized.data,
            summary,
            business_today,
        )
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
        crop_derivative = cropped_derivative_key(
            image.farm_id, captured, normalized.sha256, crop.crop_index
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
        status = await _run_cascade(
            db,
            settings,
            rotation,
            image,
            crop,
            cropped.data,
            summary,
            business_today,
        )
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
