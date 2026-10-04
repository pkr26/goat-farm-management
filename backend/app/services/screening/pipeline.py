"""The screening cycle: claim registered uploads, normalize, cascade.

Cascade (Phase 3): a detection call proposes per-goat crops. The whole frame
runs a safety cascade because detection completeness is uncertain, and each
proposed crop runs its own gate → (if flagged) specialists + cross-check.
Healthy verdicts stop the cascade per unit; findings land in the vet review
queue.

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
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Select, exists, func, literal, or_, select, true, union, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ...core.config import ScreeningRuntimeSettings
from ...metrics import record_screening_provider_call
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
from ...models.screening import HEALTHY_CONTROL_LABEL, ScreeningDailyBudget
from ...schemas.screening import MAX_SCREENING_UPLOAD_BYTES
from ...utils import DEFAULT_BUSINESS_TIMEZONE, today, utcnow
from .budget import ScreeningBudgetExhausted, reserve_provider_attempt
from .detect import (
    DETECT_PROMPT_VERSION,
    DETECT_SYSTEM_PROMPT,
    DetectionBox,
    DetectionParseError,
    parse_detection_response,
)
from .gate import GATE_PROMPT_VERSION, GateObservation
from .images import (
    CropError,
    CroppedImage,
    ImageNormalizationError,
    NormalizedImage,
    crop_image,
    normalize_image,
)
from .providers import BudgetedProvider, ProviderError, VisionProvider
from .providers import gate as run_gate
from .raw_cleanup import RAW_UPLOAD_CLEANUP_SLACK
from .rotation import GateExhaustedError, GateOutcome, ProviderRotation
from .s3 import (
    POST_MULTIPART_OVERHEAD_BYTES,
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


# B6 (2026-09-21 audit): tenant-facing error fields (ScreeningImage.error,
# ScreeningRun.error) carry a fixed reason code, never raw exception text —
# provider/S3 exceptions name endpoints, headers and HTTP topology, which is
# operator detail a farm member has no need to see. Logs likewise use stable
# reason codes and row IDs, never object keys or provider response text. These
# strings are the complete set a tenant can ever observe; they must stay free
# of interpolated values.
class ScreeningErrorReason:
    PROVIDER_ERROR = "PROVIDER_ERROR"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    INVALID_IMAGE = "INVALID_IMAGE"
    QUALITY_PROBLEM = "QUALITY_PROBLEM"
    OBJECT_TOO_LARGE = "OBJECT_TOO_LARGE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_SCREENING_ERROR_MESSAGES = {
    ScreeningErrorReason.PROVIDER_ERROR: "screening provider call failed",
    ScreeningErrorReason.DOWNLOAD_FAILED: "photo storage could not be reached",
    ScreeningErrorReason.INVALID_IMAGE: "photo bytes could not be processed",
    ScreeningErrorReason.QUALITY_PROBLEM: (
        "photo cannot be assessed; take and upload a clearer photo"
    ),
    ScreeningErrorReason.OBJECT_TOO_LARGE: "photo exceeds the size limit",
    ScreeningErrorReason.INTERNAL_ERROR: "unexpected screening failure",
}


def screening_error_reason(exc: BaseException) -> str:
    if isinstance(exc, ScreeningObjectTooLargeError):
        return ScreeningErrorReason.OBJECT_TOO_LARGE
    if isinstance(exc, ProviderError):
        return ScreeningErrorReason.PROVIDER_ERROR
    if isinstance(exc, ScreeningStorageError):
        return ScreeningErrorReason.DOWNLOAD_FAILED
    if isinstance(exc, (ImageNormalizationError, CropError, DetectionParseError)):
        return ScreeningErrorReason.INVALID_IMAGE
    return ScreeningErrorReason.INTERNAL_ERROR


def _reason_text(reason: str) -> str:
    return f"{_SCREENING_ERROR_MESSAGES[reason]} ({reason})"


def _tenant_safe_error(exc: BaseException) -> str:
    return _reason_text(screening_error_reason(exc))


# A crashed worker leaves PROCESSING rows behind; reclaim after this long.
# The pipeline refreshes the image row's ``updated_at`` after every completed
# stage (detection, each crop's gate, each finished crop), so this horizon
# means "no stage completed for this long" — comfortably above one worst-case
# cascade (gate chain + specialists + cross-check with every provider at its
# timeout). Configurable as ``screening_stale_processing_after_seconds``.
DEFAULT_STALE_PROCESSING_AFTER_SECONDS = 30 * 60

# Provider blips (timeouts, 5xx) are common; permanent failure is rare.
# ERROR rows are retried once their last transition — updated_at, refreshed
# by onupdate on every status change — ages past this horizon. Keying on the
# image row (not the GATE-run audit trail) also backs off rows that errored
# before any run was recorded; the run-based horizon used to leave those
# claimable on every cycle, starving the budget of fresh photos.
ERROR_RETRY_AFTER = dt.timedelta(hours=1)

# Every claim consumes one attempt from this budget.  Rows that exhaust it
# are never re-claimed, so a deterministic failure (degenerate crop box,
# unservable provider contract, an object deleted by a bucket lifecycle
# rule) terminates instead of retrying hourly forever — each retry re-
# downloads up to the byte cap and re-bills gate/specialist calls.
MAX_SCREENING_ATTEMPTS = 5

# A per-farm cap on fair-claim candidates. A deep queue in one tenant can
# contribute no more than this many rows and therefore cannot exclude another
# eligible farm before the rank-first merge.
_CLAIM_CANDIDATE_WINDOW = 2_000

# Review one stable tenth of whole-frame healthy verdicts. Sampling is based
# on tenant + normalized content, not queue order, so retries and worker
# replicas make the same decision without stored random state.
HEALTHY_CONTROL_SAMPLE_DENOMINATOR = 10

# Slack on top of the presign expiry before an un-PUT upload row is
# terminalized: clock skew between the API host (row created_at) and the
# database, plus a slow final S3 write, must not expire a live upload.
PENDING_SWEEP_SLACK = RAW_UPLOAD_CLEANUP_SLACK

# Downloads are sized by HEAD before any byte is transferred: an object
# larger than this is terminally SKIPPED (a misdirected video, a hostile
# upload) rather than pulled into worker memory. Photos are ~20 MB, so
# ~25 MB keeps every legitimate shot while capping the blast radius. The
# ceiling deliberately matches the presigned POST policy's bound
# (``MAX_SCREENING_UPLOAD_BYTES`` plus the multipart-envelope allowance
# some S3-compatible stores require), so an object the policy accepted is
# never rejected here — the two caps must move together.
MAX_DOWNLOAD_BYTES = MAX_SCREENING_UPLOAD_BYTES + POST_MULTIPART_OVERHEAD_BYTES

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


def normalized_derivative_key(
    farm_id: int, captured_date: dt.date, image_id: int, sha256: str
) -> str:
    """Identity-scoped key for one image's immutable normalized derivative.

    Older deployments used only farm/date/content and could make two database
    chains share a key. Including the durable image id prevents a new upload
    from racing a legacy retention purge and inheriting the object being
    removed. The full digest still prevents ambiguous integrity references.
    """
    return f"screening/{farm_id}/{captured_date.isoformat()}/v2/images/{image_id}/{sha256}.jpg"


def cropped_derivative_key(
    farm_id: int,
    captured_date: dt.date,
    image_id: int,
    crop_id: int,
    image_sha256: str,
    crop_index: int,
) -> str:
    """Identity-scoped, full-digest key for one per-goat derivative."""
    return (
        f"screening/{farm_id}/{captured_date.isoformat()}/v2/images/{image_id}/"
        f"crops/{crop_id}/{image_sha256}-c{crop_index}.jpg"
    )


@dataclass
class CycleSummary:
    claimed: int = 0
    healthy: int = 0
    unassessable: int = 0
    budget_deferred: int = 0
    flagged: int = 0
    skipped: int = 0
    errors: int = 0
    # Re-claimed rows that were already in ERROR (hourly backoff).
    retried_errors: int = 0
    # Re-claimed FLAGGED rows retrying an errored crop (same backoff, but a
    # distinct signal: visible flags exist and must not be lost).
    retried_flagged: int = 0
    expired_uploads: int = 0
    # PROCESSING rows swept to terminal ERROR because their attempt budget
    # was consumed before processing was interrupted (see
    # ``_terminate_budget_exhausted_processing``).
    terminated_processing: int = 0
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
            ScreeningImage.retention_tombstoned_at.is_(None),
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


async def _terminate_budget_exhausted_processing(
    db: AsyncSession, now: dt.datetime, stale_after: dt.timedelta
) -> int:
    """PROCESSING rows at the attempt budget are terminal, not reclaimable.

    Attempts are charged at claim time, so a row whose processing never
    reached the per-image boundary — worker crash, SIGTERM mid-cycle, or a
    DB-level failure inside the cycle — can sit at
    ``screening_attempts >= MAX`` while still PROCESSING.  Every claim
    branch refuses rows at the budget, and the stale-claim reclaim above is
    wrapped in that same budget predicate, so without this sweep such a row
    would stay PROCESSING forever: invisible to the retry queue, to the
    review UI, and to operators.  Once it is *also* stale (no lease touch
    for ``stale_after``), no live worker owns it, so terminalize it here
    with the same bounded FOR UPDATE SKIP LOCKED batch pattern as the
    abandoned-upload sweep.
    """
    candidates = (
        select(ScreeningImage.id)
        .where(
            ScreeningImage.status == ScreeningImageStatus.PROCESSING.value,
            ScreeningImage.screening_attempts >= MAX_SCREENING_ATTEMPTS,
            ScreeningImage.updated_at < now - stale_after,
            ScreeningImage.retention_tombstoned_at.is_(None),
        )
        .order_by(ScreeningImage.updated_at, ScreeningImage.id)
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
                status=ScreeningImageStatus.ERROR.value,
                error=(
                    "processing interrupted after the final attempt; terminal — "
                    "re-upload the photo to screen it again"
                ),
                next_attempt_at=None,
            )
        ),
    )
    return int(result.rowcount or 0)


async def _farm_local_day_starts(db: AsyncSession, now: dt.datetime) -> dict[str, dt.datetime]:
    """Each farm timezone's local midnight today, as naive UTC.

    ``ScreeningRun.created_at`` is a naive UTC TIMESTAMP and ``now`` is naive
    UTC, so a farm's budget window opens at its own local midnight converted
    back to naive UTC — not at UTC midnight, which would reset the budget
    mid-local-day for farms far from UTC. Keyed by the stored timezone string
    so the budget query can join farms on it; corrupt legacy timezone rows
    fall back to the same default the business-date helpers use."""
    day_starts: dict[str, dt.datetime] = {}
    for (timezone_name,) in (await db.execute(select(Farm.timezone).distinct())).all():
        try:
            tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo(DEFAULT_BUSINESS_TIMEZONE)
        local_midnight = dt.datetime.combine(
            now.replace(tzinfo=dt.UTC).astimezone(tz).date(), dt.time.min, tzinfo=tz
        )
        day_starts[timezone_name] = local_midnight.astimezone(dt.UTC).replace(tzinfo=None)
    return day_starts


async def _claim_retry_rows(
    db: AsyncSession,
    limit: int,
    now: dt.datetime,
    abandoned_after: dt.timedelta,
    stale_after: dt.timedelta,
    daily_call_budget_per_farm: int = 0,
) -> tuple[list[ScreeningImage], int, int]:
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

    Every claim consumes one unit of the row's ``screening_attempts``
    budget; rows at ``MAX_SCREENING_ATTEMPTS`` are terminal and never
    eligible again, which is what bounds this queue.

    Returns the claimed rows plus how many of them were ERROR retries and
    how many were FLAGGED rows retrying an errored crop.
    """
    retry_horizon = now - ERROR_RETRY_AFTER
    stale_horizon = now - stale_after
    pending_horizon = now - abandoned_after
    partial_crop_error = exists(
        select(ScreeningCrop.id).where(
            ScreeningCrop.farm_id == ScreeningImage.farm_id,
            ScreeningCrop.image_id == ScreeningImage.id,
            ScreeningCrop.status == ScreeningImageStatus.ERROR.value,
        )
    )
    # This is only an inexpensive queue filter. Every outbound initial,
    # retry and fallback attempt has independent durable admission below.
    # No per-image worst-case formula can reserve a concurrent batch safely.
    budget = daily_call_budget_per_farm
    budget_ok: ColumnElement[bool] = ~literal(False)
    if budget > 0:
        day_starts = await _farm_local_day_starts(db, now)
        over_budget_parts: list[Select[tuple[int]]] = []
        for timezone_name, day_start in day_starts.items():
            try:
                zone = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError:
                zone = ZoneInfo(DEFAULT_BUSINESS_TIMEZONE)
            local_date = now.replace(tzinfo=dt.UTC).astimezone(zone).date()
            charged = (
                select(ScreeningDailyBudget.farm_id)
                .join(Farm, Farm.id == ScreeningDailyBudget.farm_id)
                .where(
                    Farm.timezone == timezone_name,
                    ScreeningDailyBudget.local_date == local_date,
                    ScreeningDailyBudget.reserved_calls >= budget,
                )
            )
            # Legacy logical calls have up to two transport attempts. Once a
            # daily counter exists, it is authoritative and already includes
            # that conservative baseline; never count its new run rows twice.
            legacy = (
                select(ScreeningRun.farm_id)
                .join(Farm, Farm.id == ScreeningRun.farm_id)
                .where(
                    ScreeningRun.created_at >= day_start,
                    Farm.timezone == timezone_name,
                    ~exists(
                        select(ScreeningDailyBudget.farm_id).where(
                            ScreeningDailyBudget.farm_id == ScreeningRun.farm_id,
                            ScreeningDailyBudget.local_date == local_date,
                        )
                    ),
                )
                .group_by(ScreeningRun.farm_id)
                .having(func.count() * 2 >= budget)
            )
            over_budget_parts.extend((charged, legacy))
        if over_budget_parts:
            over_budget_farms = union(*over_budget_parts).subquery()
            budget_ok = ~ScreeningImage.farm_id.in_(select(over_budget_farms.c.farm_id))

    eligible = (
        (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS)
        & budget_ok
        # A committed retention manifest is a logical tombstone. Never
        # reclaim that image and create fresh derivatives after its exact-key
        # deletion plan has become durable.
        & ScreeningImage.retention_tombstoned_at.is_(None)
        & (
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
    )
    # Build the bounded window *inside each farm*. A global oldest-N window
    # lets one deep tenant backlog exclude every row from a newer farm before
    # the partition rank even runs. The lateral slice admits up to ``limit``
    # rows independently per selected farm (at most limit² candidates), then
    # the rank-first merge takes each farm's oldest before its second-oldest.
    eligible_farms = (
        select(
            ScreeningImage.farm_id.label("farm_id"),
            func.min(ScreeningImage.created_at).label("farm_oldest"),
        )
        .where(eligible)
        .group_by(ScreeningImage.farm_id)
        .subquery("screening_eligible_farms")
    )
    # ``screening_attempts`` increments in the durable claim transaction.
    # Its latest timestamp is therefore a persistent round-robin cursor per
    # farm: never-claimed farms sort first, then the least recently served.
    # Unlike queue age, this ordering cannot be pinned by another farm's
    # arbitrarily deep backlog.
    farm_claim_activity = (
        select(
            ScreeningImage.farm_id.label("farm_id"),
            func.max(ScreeningImage.updated_at).label("last_claimed_at"),
        )
        .where(ScreeningImage.screening_attempts > 0)
        .group_by(ScreeningImage.farm_id)
        .subquery("screening_farm_claim_activity")
    )
    candidate_farms = (
        select(
            eligible_farms.c.farm_id,
            eligible_farms.c.farm_oldest,
            farm_claim_activity.c.last_claimed_at,
        )
        .outerjoin(
            farm_claim_activity,
            farm_claim_activity.c.farm_id == eligible_farms.c.farm_id,
        )
        .order_by(
            farm_claim_activity.c.last_claimed_at.asc().nulls_first(),
            eligible_farms.c.farm_oldest,
            eligible_farms.c.farm_id,
        )
        .limit(min(limit, _CLAIM_CANDIDATE_WINDOW))
        .subquery("screening_candidate_farms")
    )
    per_farm_candidates = (
        select(
            ScreeningImage.id.label("image_id"),
            ScreeningImage.farm_id.label("farm_id"),
            ScreeningImage.created_at.label("created_at"),
        )
        .where(ScreeningImage.farm_id == candidate_farms.c.farm_id, eligible)
        .order_by(ScreeningImage.created_at, ScreeningImage.id)
        .limit(min(limit, _CLAIM_CANDIDATE_WINDOW))
        .correlate(candidate_farms)
        .lateral("screening_per_farm_candidates")
    )
    bounded_candidates = (
        select(
            per_farm_candidates.c.image_id,
            per_farm_candidates.c.farm_id,
            per_farm_candidates.c.created_at,
            candidate_farms.c.farm_oldest,
        )
        .select_from(candidate_farms)
        .join(per_farm_candidates, true())
        .subquery("screening_bounded_candidates")
    )
    ranked_candidates = select(
        bounded_candidates.c.image_id,
        bounded_candidates.c.created_at,
        bounded_candidates.c.farm_oldest,
        func.row_number()
        .over(
            partition_by=bounded_candidates.c.farm_id,
            order_by=(
                bounded_candidates.c.created_at.asc(),
                bounded_candidates.c.image_id.asc(),
            ),
        )
        .label("farm_rank"),
    ).subquery("screening_ranked_candidates")
    result = await db.execute(
        select(ScreeningImage)
        .join(ranked_candidates, ranked_candidates.c.image_id == ScreeningImage.id)
        .order_by(
            ranked_candidates.c.farm_rank,
            ranked_candidates.c.farm_oldest,
            ranked_candidates.c.created_at,
            ScreeningImage.id,
        )
        .limit(limit)
        .with_for_update(skip_locked=True, of=ScreeningImage)
    )
    rows = list(result.scalars())
    error_retries = sum(1 for row in rows if row.status == ScreeningImageStatus.ERROR.value)
    flagged_retries = sum(1 for row in rows if row.status == ScreeningImageStatus.FLAGGED.value)
    for row in rows:
        row.status = ScreeningImageStatus.PROCESSING.value
        row.error = None
        row.next_attempt_at = None
        row.screening_attempts += 1
    await db.flush()
    await db.commit()  # durable claim: released lock, visible PROCESSING
    return rows, error_retries, flagged_retries


async def run_screening_cycle(
    db: AsyncSession,
    settings: ScreeningRuntimeSettings,
    storage: ScreeningStorage,
    rotation: ProviderRotation,
) -> CycleSummary:
    summary = CycleSummary()

    budget = settings.screening_max_images_per_cycle
    abandoned_after = pending_upload_abandoned_after(settings)
    stale_after = dt.timedelta(
        seconds=getattr(
            settings,
            "screening_stale_processing_after_seconds",
            DEFAULT_STALE_PROCESSING_AFTER_SECONDS,
        )
    )
    # Expire abandoned uploads BEFORE claiming (same transaction): without
    # the sweep, stale PENDING rows would monopolize the claim below.
    expired = await _expire_abandoned_uploads(db, utcnow(), abandoned_after)
    if expired:
        summary.expired_uploads = expired
        summary.notes.append(
            f"{expired} abandoned PENDING uploads expired to SKIPPED "
            f"(presigned URL older than {abandoned_after})"
        )
    # Budget-exhausted PROCESSING rows can never be reclaimed (the claim
    # predicate refuses rows at the budget); sweep them to terminal ERROR
    # before claiming so they cannot linger as invisible zombies.
    zombies = await _terminate_budget_exhausted_processing(db, utcnow(), stale_after)
    if zombies:
        summary.terminated_processing = zombies
        summary.notes.append(
            f"{zombies} PROCESSING rows at the attempt budget terminated to ERROR "
            f"(stale for longer than {stale_after})"
        )
    # The API pre-registers every accepted upload.  Deliberately do *not*
    # discover and insert arbitrary keys from the raw prefix here: anyone
    # with another bucket writer or a leaked object credential could otherwise
    # manufacture tenant-owned screening rows merely by choosing a farm id in
    # a key.  Existing registered rows remain the sole intake authority.
    claimed, error_retries, flagged_retries = await _claim_retry_rows(
        db,
        budget,
        utcnow(),
        abandoned_after,
        stale_after,
        settings.screening_daily_call_budget_per_farm,
    )
    summary.retried_errors += error_retries
    summary.retried_flagged += flagged_retries

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
    # rollback() expires every claimed instance in the identity map even
    # though the session uses expire_on_commit=False (that option only
    # shields commits), and an attribute read on an expired instance raises
    # MissingGreenlet under AsyncSession.  Snapshot the per-image farm ids
    # before the loop so a mid-cycle rollback can never turn a later
    # iteration's timezone lookup into a lazy refresh.
    image_farm_ids = {image.id: image.farm_id for image in claimed}
    # Snapshot ids and attempt counts while the session is still clean after
    # the claim commit: a mid-cycle rollback expires every claimed instance,
    # and any later read off the ORM object — logging included — raises
    # MissingGreenlet under AsyncSession and kills the whole cycle.  The
    # attempt count is stable for the same reason: the claim consumes and
    # commits the budget before the cycle starts, so nothing mutates it
    # mid-loop.
    claimed_snapshot = [(image, image.id, image.screening_attempts) for image in claimed]
    identities_expired = False
    for image, image_id, attempts in claimed_snapshot:
        budget_deferred = False
        try:
            if identities_expired:
                # A previous iteration's rollback expired this row too;
                # reload its columns before anything reads them.
                await db.refresh(image)
            # Farm FK integrity guarantees this lookup.  The defensive India
            # fallback prevents a corrupt legacy row from crashing the whole
            # worker cycle while preserving existing date-helper behavior.
            business_today = today(
                farm_timezones.get(image_farm_ids[image_id], DEFAULT_BUSINESS_TIMEZONE)
            )
            budgeted_providers: list[VisionProvider] = []
            for provider in rotation:

                async def admit(
                    provider_name: str = provider.name, admitted_image_id: int = image_id
                ) -> None:
                    await reserve_provider_attempt(
                        db,
                        farm_id=image_farm_ids[admitted_image_id],
                        timezone_name=farm_timezones.get(
                            image_farm_ids[admitted_image_id], DEFAULT_BUSINESS_TIMEZONE
                        ),
                        provider=provider_name,
                        cap=settings.screening_daily_call_budget_per_farm,
                        now=utcnow(),
                    )

                budgeted_providers.append(BudgetedProvider(provider, admit))
            await _process_image(
                db,
                settings,
                storage,
                ProviderRotation(budgeted_providers),
                image,
                summary,
                business_today,
            )
        except ScreeningBudgetExhausted:
            # No external request started. Preserve already written evidence,
            # defer the unfinished image, and do not burn its failure budget.
            budget_deferred = True
            image.status = ScreeningImageStatus.ERROR.value
            image.error = (
                "Daily screening call budget reached; "
                "unfinished screening waits for the next local day"
            )
            image.screening_attempts = max(0, attempts - 1)
            summary.budget_deferred += 1
        except Exception as exc:
            # Log from the snapshot id: if the failure was the loop-top
            # refresh itself (dead connection), the instance is still
            # expired and reading image.id here would raise inside the
            # handler that exists to keep the cycle alive.
            logger.error(
                "screening image failed image_id=%s code=UNEXPECTED_PIPELINE_FAILURE "
                "exception_type=%s",
                image_id,
                type(exc).__name__,
            )
            # A DB-level failure inside _process_image aborts the
            # transaction; roll back BEFORE mutating the ORM object, or the
            # commit below would raise PendingRollbackError and strand every
            # remaining image of the cycle behind the aborted transaction.
            # The attempt count comes from the pre-loop snapshot: reading it
            # off the now-expired instance would trigger the lazy refresh
            # described above and crash this handler itself (stranding the
            # row PROCESSING until the stale reclaim, burning the budget
            # each time).
            await db.rollback()
            identities_expired = True
            image.status = ScreeningImageStatus.ERROR.value
            # Persist only the tenant-safe category. Logs likewise keep a
            # stable code and exception class, never provider response text,
            # object keys, endpoints, or exception payloads.
            image.error = _tenant_safe_error(exc)
            summary.errors += 1
        if (
            image.status == ScreeningImageStatus.ERROR.value
            and attempts >= MAX_SCREENING_ATTEMPTS
            and not budget_deferred
        ):
            # Central terminal marker: every ERROR path funnels through this
            # per-image boundary, and the claim predicate above refuses rows
            # at the budget, so this row will never be retried again.  Say so
            # in the operator-visible error instead of implying more retries.
            image.error = f"{image.error or 'screening failed'}; terminal after {attempts} attempts"
        try:
            await db.commit()
        except Exception as exc:
            # The per-image boundary is deliberate: one uncommittable row
            # (constraint, connection loss) must not take the whole cycle
            # down. Roll back FIRST: after a flush-level failure the session
            # sits in pending-rollback state and even reading an attribute
            # that was loaded before the failure raises PendingRollbackError
            # (verified against SQLAlchemy 2.0.51) — the log line must come
            # after the rollback and use the snapshot id. The row stays
            # PROCESSING and the stale-claim reclaim retries it later.
            await db.rollback()
            identities_expired = True
            logger.error(
                "screening image commit failed image_id=%s code=DATABASE_COMMIT_FAILED "
                "exception_type=%s",
                image_id,
                type(exc).__name__,
            )

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
    # One provider call = one run row, whatever its outcome: the spend
    # metrics and the daily per-farm budget both count it here (ITEM 6).
    record_screening_provider_call(provider, run_status == ScreeningRunStatus.OK.value)
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
    healthy_control: bool = False,
) -> ScreeningFinding:
    # The DB CHECK rejects blank-but-non-NULL notes; a model that answered
    # note: "" (or a client that sent whitespace) must become NULL here, not
    # an IntegrityError that rolls the whole cascade back into the retry
    # loop.
    if note is not None and not note.strip():
        note = None
    # The control type intentionally rides an existing bounded label column.
    # Never let provider-authored text impersonate that server-owned marker.
    if label == HEALTHY_CONTROL_LABEL and not healthy_control:
        label = f"Model finding: {label}"
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


def _response_sha256(raw_text: str) -> str:
    """Digest provider text without retaining provider payload or secrets."""
    return hashlib.sha256(raw_text.encode("utf-8", "surrogatepass")).hexdigest()


def _sample_healthy_control(image: ScreeningImage) -> bool:
    """Stable farm-local 10% sample for healthy-verdict human review."""
    identity = image.sha256 or str(image.id)
    digest = hashlib.sha256(
        f"healthy-control-v1:{image.farm_id}:{identity}".encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], "big") % HEALTHY_CONTROL_SAMPLE_DENOMINATOR == 0


async def _enqueue_healthy_control(
    db: AsyncSession,
    image: ScreeningImage,
    gate_run: ScreeningRun,
) -> None:
    """Add one neutral review item for a genuinely healthy image outcome."""
    already_queued = (
        await db.execute(
            select(
                exists().where(
                    ScreeningFinding.farm_id == image.farm_id,
                    ScreeningFinding.label == HEALTHY_CONTROL_LABEL,
                    ScreeningFinding.run_id == ScreeningRun.id,
                    ScreeningRun.farm_id == image.farm_id,
                    ScreeningRun.image_id == image.id,
                )
            )
        )
    ).scalar_one()
    if already_queued:
        return
    gate_run.detail = {**(gate_run.detail or {}), "healthy_control_sample": True}
    await db.flush()
    db.add(
        _add_finding(
            image,
            gate_run.id,
            region=None,
            label=HEALTHY_CONTROL_LABEL,
            confidence=(float(gate_run.confidence) if gate_run.confidence is not None else None),
            note="Neutral review sample; assess the photo without a model-supplied label.",
            crop_id=None,
            healthy_control=True,
        )
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
    for provider in chain:
        try:
            answer = await provider.complete(jpeg, DETECT_SYSTEM_PROMPT)
            boxes = parse_detection_response(answer.text, max_crops)
        except (ProviderError, DetectionParseError) as exc:
            failures.append(provider.name)
            # One provider call = one run row, whatever its outcome: a failed
            # fallback attempt was paid and must count in the spend ledger and
            # the daily budget here, not only the attempt that finally served
            # (2026-10-01 audit, 02-3).
            db.add(
                _record_run(
                    image,
                    stage=ScreeningStage.DETECT.value,
                    provider=provider.name,
                    model=provider.model,
                    prompt_version=DETECT_PROMPT_VERSION,
                    run_status=ScreeningRunStatus.ERROR.value,
                    error=_tenant_safe_error(exc),
                )
            )
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
                    "boxes_1000": [
                        {"x": box.x, "y": box.y, "w": box.w, "h": box.h} for box in boxes
                    ],
                    "response_sha256": _response_sha256(answer.text),
                    "served_by": provider.name,
                    "fallbacks_failed": failures,
                },
            )
        )
        return boxes
    # ``chain`` always contains primary, and every entry either returned
    # above or recorded its own ERROR row, so a fully-failed chain has
    # already left one run row per paid attempt.
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
    *,
    coverage_safety_pass: bool = False,
    sample_healthy: bool = True,
) -> str:
    """Gate → (if flagged) specialists + cross-check over one photo or crop.

    Runs and findings record ``crop`` when given; returns the terminal
    status string (HEALTHY / FLAGGED / ERROR) for the caller to apply.
    """
    primary = rotation.primary_for(business_today)
    crop_id = crop.id if crop is not None else None

    def _record_failed_gate_attempt(name: str) -> None:
        # One provider call = one run row, whatever its outcome: each fallback
        # attempt that failed before another provider served the gate was paid
        # and must count in the spend ledger and the daily budget individually,
        # never only the attempt that finally served (2026-10-01 audit, 02-3).
        failed = rotation.provider_named(name)
        db.add(
            _record_run(
                image,
                stage=ScreeningStage.GATE.value,
                provider=name,
                model=failed.model if failed is not None else "unknown",
                prompt_version=GATE_PROMPT_VERSION,
                run_status=ScreeningRunStatus.ERROR.value,
                error=_reason_text(ScreeningErrorReason.PROVIDER_ERROR),
                crop_id=crop_id,
            )
        )

    try:
        outcome: GateOutcome = await rotation.gate_with_fallback(primary, jpeg)
    except GateExhaustedError as exc:
        for name in exc.failed_providers or (primary.name,):
            _record_failed_gate_attempt(name)
        logger.warning(
            "screening gate failed image_id=%s code=GATE_PROVIDER_FAILURE",
            image.id,
        )
        return ScreeningImageStatus.ERROR.value
    for name in outcome.failed_providers:
        _record_failed_gate_attempt(name)
    # The gate chain is the longest single provider segment (every rotation
    # entry at its own timeout); renew the processing lease before the
    # specialist/cross-check stages continue without an image-row write.
    await _touch_processing_lease(db, image)

    gate_result = outcome.result
    gate_run = _record_run(
        image,
        stage=ScreeningStage.GATE.value,
        provider=gate_result.provider,
        model=gate_result.model,
        prompt_version=gate_result.prompt_version,
        verdict=(
            "unassessable"
            if gate_result.response.quality_problem
            else "flagged"
            if gate_result.response.flagged
            else "healthy"
        ),
        confidence=gate_result.response.confidence,
        latency_ms=gate_result.latency_ms,
        detail={
            "served_by": outcome.served_by,
            "fallbacks_failed": list(outcome.failed_providers),
            "quality_problem": gate_result.response.quality_problem,
            "observation_count": len(gate_result.response.observations),
            "response": gate_result.response.model_dump(mode="json"),
            "response_sha256": _response_sha256(gate_result.raw_text),
            "coverage_safety_pass": coverage_safety_pass,
        },
        crop_id=crop_id,
    )
    db.add(gate_run)

    if gate_result.response.quality_problem:
        return ScreeningImageStatus.UNASSESSABLE.value

    if not gate_result.response.flagged:
        # The cascade stops here. A deterministic tenth of whole-frame
        # healthy verdicts enters a neutral, human-reviewed control queue so
        # the product can measure false negatives instead of reporting only
        # precision among the positives it chose to emit.
        if sample_healthy and crop is None and _sample_healthy_control(image):
            await _enqueue_healthy_control(db, image, gate_run)
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
    # Gate observations grouped by the specialist kind their region maps to:
    # a failed specialist call falls back to exactly its own kind's
    # observations, so a partial outage neither drops a missed region's
    # finding nor overrides a specialist that did answer (2026-10-01
    # audit, 02-2).
    observations_by_kind: dict[SpecialistKind, list[GateObservation]] = {}
    for observation in observations or []:
        observations_by_kind.setdefault(specialist_for_region(observation.region), []).append(
            observation
        )
    gate_observations_lost: list[GateObservation] = []
    budget_limited = False
    for kind_index, (kind, region) in enumerate(kinds):
        if serving is None:
            gate_observations_lost.extend(observations_by_kind.get(kind, []))
            continue
        try:
            specialist: SpecialistCallResult = await run_specialist(serving, jpeg, kind)
        except ScreeningBudgetExhausted:
            # Preserve the gate's urgent observations even when optional
            # refinement has no admission. A denied request is not a paid
            # error run; its absence is explicit in the gate provenance.
            for remaining_kind, _ in kinds[kind_index:]:
                gate_observations_lost.extend(observations_by_kind.get(remaining_kind, []))
            gate_run.detail = {**(gate_run.detail or {}), "budget_limited": True}
            summary.budget_deferred += 1
            budget_limited = True
            break
        except ProviderError as exc:
            db.add(
                _record_run(
                    image,
                    stage=kind.value,
                    provider=serving.name,
                    model=serving.model,
                    prompt_version=SPECIALIST_PROMPT_VERSIONS[kind],
                    run_status=ScreeningRunStatus.ERROR.value,
                    error=_tenant_safe_error(exc),
                    crop_id=crop_id,
                )
            )
            logger.warning(
                "screening specialist failed image_id=%s specialist=%s "
                "code=SPECIALIST_PROVIDER_FAILURE",
                image.id,
                kind.value,
            )
            summary.notes.append(f"specialist {kind.value} failed for image {image.id}")
            # The crop aggregates to FLAGGED, which is terminal for retries,
            # so this is the only chance the missed region's gate observation
            # has of reaching the vet queue.
            gate_observations_lost.extend(observations_by_kind.get(kind, []))
            continue
        specialist_run = _record_run(
            image,
            stage=kind.value,
            provider=specialist.provider,
            model=specialist.model,
            prompt_version=specialist.prompt_version,
            latency_ms=specialist.latency_ms,
            detail={
                "condition_count": len(specialist.response.conditions),
                "conditions": specialist.response.model_dump(mode="json")["conditions"],
                "response_sha256": _response_sha256(specialist.raw_text),
            },
            crop_id=crop_id,
        )
        db.add(specialist_run)
        await db.flush()
        for condition in specialist.response.conditions:
            specialist_conditions.append((specialist_run.id, region, condition))

    if specialist_conditions or gate_observations_lost:
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
        # Observations whose specialist never answered stay in the queue,
        # attributed to the gate run that originally saw them.
        for observation in gate_observations_lost:
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

    # ---- cross-check from the next usable provider in the rotation -------
    # Skip whoever served the gate and whoever already failed it: the date's
    # blind primary+1 pick made the "second opinion" the serving fallback
    # itself in 3-provider rotations, silently skipping the cross-check
    # (P3, 2026-09-20 audit).
    secondary = rotation.cross_checker_for(
        business_today, gate_result.provider, outcome.failed_providers
    )
    if not budget_limited and secondary is not None and secondary.name != gate_result.provider:
        try:
            check = await run_gate(secondary, jpeg)
            db.add(
                _record_run(
                    image,
                    stage=ScreeningStage.CROSS_CHECK.value,
                    provider=check.provider,
                    model=check.model,
                    prompt_version=check.prompt_version,
                    verdict=(
                        "unassessable"
                        if check.response.quality_problem
                        else "flagged"
                        if check.response.flagged
                        else "healthy"
                    ),
                    confidence=check.response.confidence,
                    latency_ms=check.latency_ms,
                    detail={
                        "cross_check_of": gate_run.id,
                        "agrees": check.response.flagged and not check.response.quality_problem,
                        "quality_problem": check.response.quality_problem,
                        "response": check.response.model_dump(mode="json"),
                        "response_sha256": _response_sha256(check.raw_text),
                    },
                    crop_id=crop_id,
                )
            )
        except ScreeningBudgetExhausted:
            gate_run.detail = {**(gate_run.detail or {}), "budget_limited": True}
            summary.budget_deferred += 1
        except ProviderError as exc:
            db.add(
                _record_run(
                    image,
                    stage=ScreeningStage.CROSS_CHECK.value,
                    provider=secondary.name,
                    model=secondary.model,
                    prompt_version=GATE_PROMPT_VERSION,
                    run_status=ScreeningRunStatus.ERROR.value,
                    error=_tenant_safe_error(exc),
                    crop_id=crop_id,
                )
            )
            logger.warning(
                "screening cross-check failed image_id=%s code=CROSS_CHECK_PROVIDER_FAILURE",
                image.id,
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
    if ScreeningImageStatus.UNASSESSABLE.value in statuses:
        return ScreeningImageStatus.UNASSESSABLE.value
    return ScreeningImageStatus.HEALTHY.value if statuses else ScreeningImageStatus.ERROR.value


async def _prior_coverage_safety_status(db: AsyncSession, image: ScreeningImage) -> str | None:
    """Reuse a completed whole-frame safety pass when only a crop retries."""
    verdict = (
        await db.execute(
            select(ScreeningRun.verdict)
            .where(
                ScreeningRun.farm_id == image.farm_id,
                ScreeningRun.image_id == image.id,
                ScreeningRun.crop_id.is_(None),
                ScreeningRun.stage == ScreeningStage.GATE.value,
                ScreeningRun.run_status == ScreeningRunStatus.OK.value,
                ScreeningRun.detail["coverage_safety_pass"].astext == "true",
            )
            .order_by(ScreeningRun.created_at.desc(), ScreeningRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if verdict is None:
        return None
    return {
        "healthy": ScreeningImageStatus.HEALTHY.value,
        "flagged": ScreeningImageStatus.FLAGGED.value,
        "unassessable": ScreeningImageStatus.UNASSESSABLE.value,
    }.get(verdict)


async def _content_claim_owner(db: AsyncSession, farm_id: int, sha256: str) -> int | None:
    """Return the *live* image that owns a farm-local content digest.

    A retention-fenced image is logically absent even while its relational
    chain remains as retry evidence. Joining the same-row tombstone fence here
    prevents that hidden image from making a fresh upload look like a
    duplicate.
    """
    return (
        await db.execute(
            select(ScreeningContentClaim.image_id)
            .join(
                ScreeningImage,
                (ScreeningImage.farm_id == ScreeningContentClaim.farm_id)
                & (ScreeningImage.id == ScreeningContentClaim.image_id),
            )
            .where(
                ScreeningContentClaim.farm_id == farm_id,
                ScreeningContentClaim.sha256 == sha256,
                ScreeningImage.retention_tombstoned_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def _resolve_content_claim_conflict(
    db: AsyncSession, image: ScreeningImage, sha256: str, owner_id: int
) -> str | None:
    """Decide a duplicate-upload conflict against the claim's current owner.

    Returns ``None`` when ``image`` takes over the screening duty, or the
    operator-visible skip reason when it must not screen these bytes.

    * Owner already produced a result (HEALTHY/FLAGGED/SKIPPED): classic
      duplicate — the new upload is skipped, exactly as before.
    * Owner is PROCESSING right now: a worker is mid-cascade on these very
      bytes; skipping the new upload is truthful ("being screened") and
      avoids duplicate findings and a double bill.
    * Owner is PENDING or ERROR: it holds the digest but has produced
      nothing.  A farmer re-photographing because the first upload is stuck
      must not be permanently rejected with a claim that screening happened.
      The bytes are in hand *here*, so the claim moves to this image and the
      stalled owner is terminally SKIPPED as superseded.  (An ERROR owner
      cannot have findings: any flagged crop would have kept its aggregate
      status FLAGGED, which is the first branch.)
    """
    owner = (
        await db.execute(
            select(ScreeningImage)
            .where(
                ScreeningImage.id == owner_id,
                ScreeningImage.farm_id == image.farm_id,
            )
            # Retention locks the same root row before setting its durable
            # tombstone. If it won, this waiter sees the updated tuple; if we
            # win, retention's SKIP LOCKED planner leaves the owner alone.
            .with_for_update()
        )
    ).scalar_one_or_none()
    owner_status = owner.status if owner is not None else None
    owner_is_live = owner is not None and owner.retention_tombstoned_at is None
    if owner_is_live and owner_status in (
        ScreeningImageStatus.HEALTHY.value,
        ScreeningImageStatus.UNASSESSABLE.value,
        ScreeningImageStatus.FLAGGED.value,
        ScreeningImageStatus.SKIPPED.value,
    ):
        return "duplicate: identical bytes already screened for this farm"
    if owner_is_live and owner_status == ScreeningImageStatus.PROCESSING.value:
        return "duplicate: identical bytes are currently being screened for this farm"

    claim = (
        await db.execute(
            select(ScreeningContentClaim)
            .where(
                ScreeningContentClaim.farm_id == image.farm_id,
                ScreeningContentClaim.sha256 == sha256,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if claim is None or claim.image_id != owner_id:
        # Raced with another takeover or a cascade delete; the caller's
        # duplicate pre-check and the unique constraints still bound this —
        # re-read the winner rather than guessing.
        winner = await _content_claim_owner(db, image.farm_id, sha256)
        if winner == image.id:
            return None
        if winner is None:
            winner = await _reserve_normalized_content(db, image, sha256)
            if winner == image.id:
                return None
        return "duplicate: identical bytes already screened for this farm"
    claim.image_id = image.id
    if owner_is_live and owner is not None:
        owner.status = ScreeningImageStatus.SKIPPED.value
        owner.error = "superseded by a newer upload of identical bytes"
        owner.next_attempt_at = None
    await db.flush()
    return None


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
        # The unique digest row can still belong to a now-tombstoned image.
        # Lock and transfer that compact claim instead of treating logically
        # deleted evidence as a canonical result. Retention's child delete is
        # predicate-bound to the old image id, so it cannot remove the claim
        # after this update becomes visible.
        stale_claim = (
            await db.execute(
                select(ScreeningContentClaim)
                .where(
                    ScreeningContentClaim.farm_id == image.farm_id,
                    ScreeningContentClaim.sha256 == sha256,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if stale_claim is not None:
            stale_owner = await db.get(ScreeningImage, stale_claim.image_id)
            if stale_owner is not None and stale_owner.retention_tombstoned_at is not None:
                image_claim = (
                    await db.execute(
                        select(ScreeningContentClaim.id).where(
                            ScreeningContentClaim.farm_id == image.farm_id,
                            ScreeningContentClaim.image_id == image.id,
                            ScreeningContentClaim.id != stale_claim.id,
                        )
                    )
                ).scalar_one_or_none()
                if image_claim is not None:
                    return None
                stale_claim.image_id = image.id
                image.sha256 = sha256
                await db.commit()
                return image.id
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


def _note_object_absent(image: ScreeningImage, summary: CycleSummary) -> None:
    """Record an absent/changed object without lying about the cause.

    Three cases must not collapse into one:

    * The row has already been normalized once (``sha256`` set — including a
      reclaimed FLAGGED photo), or has no presigned form that could still
      deliver bytes (tokenless legacy intake).  The object vanishing is then
      a storage-level fact (bucket lifecycle rule, out-of-band delete): mark
      ERROR with a truthful message.  The claim's attempt is kept, so the
      budget eventually terminates a permanently missing object instead of
      polling forever — and a FLAGGED row is never silently demoted back to
      PENDING (or later swept with a false "upload never arrived").
    * A tokened row whose form is still alive may simply be waiting for a
      slow phone: stay PENDING with the bounded re-probe backoff and refund
      the attempt — waiting for bytes is not a failed screening.  The
      expiry sweep remains that row's terminal outcome.
    """
    if image.sha256 is not None or image.upload_token is None:
        image.status = ScreeningImageStatus.ERROR.value
        image.error = (
            "object became unavailable from storage (deleted, changed, or lifecycle-expired)"
        )
        summary.errors += 1
        return
    image.screening_attempts = max(0, image.screening_attempts - 1)
    image.status = ScreeningImageStatus.PENDING.value
    image.error = None
    image.next_attempt_at = utcnow() + PENDING_OBJECT_RETRY_AFTER
    summary.notes.append(f"image {image.id} object not uploaded yet")


async def _touch_processing_lease(db: AsyncSession, image: ScreeningImage) -> None:
    """Refresh the row's ``updated_at`` so the stale-claim horizon sees progress.

    The claim predicate reclaims PROCESSING rows whose ``updated_at`` aged
    past the configured horizon.  Per-crop processing mutates only crop
    rows, so without an explicit touch a legitimately long cascade would
    look indistinguishable from a crashed worker and two workers could
    double-process one photo.  One touch per completed stage bounds the
    silent window to a single cascade.

    The touch is flushed, not just applied to the ORM object: an unflushed
    mutation holds no row lock and is invisible to a concurrent worker's
    ``FOR UPDATE SKIP LOCKED`` claim scan, so under tight-but-legal configs
    (long provider timeouts, short stale horizon) a second worker could
    stale-reclaim this row mid-cascade and double-bill the providers.
    Flushed, the UPDATE pins the row until the next commit and SKIP LOCKED
    treats it as actively owned.
    """
    image.updated_at = utcnow()
    await db.flush()


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

    # A retry resumes from the already-sanitized derivative. Provider work
    # therefore never depends on retaining an EXIF-bearing raw upload after
    # normalization has succeeded.
    using_persisted_derivative = bool(image.normalized_key and image.sha256)
    source_key = image.normalized_key if using_persisted_derivative else image.s3_key
    if source_key is None:  # defensive; the bool above makes this unreachable
        image.status = ScreeningImageStatus.ERROR.value
        image.error = _reason_text(ScreeningErrorReason.DOWNLOAD_FAILED)
        summary.errors += 1
        return

    try:
        object_info = await asyncio.to_thread(storage.object_info, source_key)
    except ScreeningStorageError as exc:
        # The size probe rides the same failure contract as the download.
        image.status = ScreeningImageStatus.ERROR.value
        logger.warning(
            "screening object metadata failed image_id=%s code=OBJECT_INFO_FAILED",
            image.id,
        )
        image.error = _tenant_safe_error(exc)
        summary.errors += 1
        return
    if object_info is None:
        # A presigned-upload row whose bytes have not landed yet (phone still
        # on a slow link, or an abandoned URL), or an object that vanished
        # after a prior attempt — see _note_object_absent for the split.
        _note_object_absent(image, summary)
        return
    if object_info.size > MAX_DOWNLOAD_BYTES:
        # Terminally SKIPPED, never ERROR: the bytes are a fact about the
        # object, not a transient condition, and the claim predicate only
        # ever re-reads PENDING/PROCESSING/ERROR rows — so an oversized
        # object cannot occupy the retry budget either. The cap matches the
        # presigned POST policy's bound (object cap + multipart-envelope
        # allowance) so a policy-accepted upload is never rejected here.
        # Tenant-facing field carries the fixed reason code (B6); the byte
        # counts stay in the worker log.
        logger.warning(
            "screening object skipped image_id=%s code=OBJECT_TOO_LARGE size_bytes=%d cap_bytes=%d",
            image.id,
            object_info.size,
            MAX_DOWNLOAD_BYTES,
        )
        image.status = ScreeningImageStatus.SKIPPED.value
        image.error = _reason_text(ScreeningErrorReason.OBJECT_TOO_LARGE)
        summary.skipped += 1
        return
    if image.upload_token is not None and not using_persisted_derivative:
        # New direct POSTs carry all three policy-bound facts.  A key alone
        # is never proof of tenant ownership: an out-of-band bucket writer
        # must not be able to inject an image by guessing a farm/key prefix.
        if image.upload_content_type is None:
            image.status = ScreeningImageStatus.SKIPPED.value
            image.error = "upload registration is missing its declared content type"
            summary.skipped += 1
            return
        object_token = object_info.metadata.get("screening-token")
        # compare_digest raises TypeError for non-ASCII str input, which an
        # out-of-band bucket writer controls here; that must fall into the
        # ordinary mismatch verdict instead of escaping as an unexpected
        # pipeline failure that burns five retry cycles (L-2, 2026-09-20
        # audit). Encode both sides so any byte value compares cleanly.
        stored_token_bytes = image.upload_token.encode("utf-8", "surrogatepass")
        object_token_bytes = (
            object_token.encode("utf-8", "surrogatepass") if object_token is not None else None
        )
        if object_token_bytes is None or not hmac.compare_digest(
            object_token_bytes, stored_token_bytes
        ):
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
            source_key,
            max_bytes=MAX_DOWNLOAD_BYTES,
            etag=object_info.etag,
            version_id=object_info.version_id,
        )
    except ScreeningObjectMissingError:
        # The object disappeared after HEAD.  Same policy split as a HEAD
        # miss: keep waiting only while a live form could still deliver.
        _note_object_absent(image, summary)
        return
    except ScreeningObjectChangedError:
        # ETag/Version conditional GET prevented a malicious or merely slow
        # uploader from swapping bytes after validation.  Re-HEAD a stable
        # snapshot later rather than ever decoding unverified content.
        _note_object_absent(image, summary)
        return
    except ScreeningObjectTooLargeError:
        image.status = ScreeningImageStatus.SKIPPED.value
        # Tenant-facing field carries the fixed reason code (B6); the byte
        # counts stay in the worker log.
        logger.warning(
            "screening object skipped image_id=%s code=OBJECT_TOO_LARGE",
            image.id,
        )
        image.error = _reason_text(ScreeningErrorReason.OBJECT_TOO_LARGE)
        summary.skipped += 1
        return
    except ScreeningStorageError as exc:
        image.status = ScreeningImageStatus.ERROR.value
        logger.warning(
            "screening object download failed image_id=%s code=OBJECT_DOWNLOAD_FAILED",
            image.id,
        )
        image.error = _tenant_safe_error(exc)
        summary.errors += 1
        return

    if using_persisted_derivative:
        derivative_sha256 = hashlib.sha256(raw).hexdigest()
        if derivative_sha256 != image.sha256 or image.width is None or image.height is None:
            image.status = ScreeningImageStatus.ERROR.value
            image.error = "normalized screening derivative failed its integrity check"
            summary.errors += 1
            return
        normalized = NormalizedImage(
            data=raw,
            width=image.width,
            height=image.height,
            sha256=derivative_sha256,
            byte_size=len(raw),
        )
    else:
        try:
            normalized = await asyncio.to_thread(
                normalize_image,
                raw,
                settings.screening_image_max_edge_px,
                image.upload_content_type,
            )
        except ImageNormalizationError as exc:
            # Corrupt/hostile bytes are immutable facts about this object. A
            # retry would repeatedly send the same parser bomb through the
            # worker, so reject it terminally and require a new upload row.
            image.status = ScreeningImageStatus.SKIPPED.value
            logger.warning(
                "screening normalization rejected image_id=%s code=NORMALIZATION_REJECTED",
                image.id,
            )
            image.error = _tenant_safe_error(exc)
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
                ScreeningImage.retention_tombstoned_at.is_(None),
                ScreeningImage.status.in_(
                    [
                        ScreeningImageStatus.HEALTHY.value,
                        ScreeningImageStatus.UNASSESSABLE.value,
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
        skip_reason = await _resolve_content_claim_conflict(
            db, image, normalized.sha256, claim_owner
        )
        if skip_reason is not None:
            image.status = ScreeningImageStatus.SKIPPED.value
            image.error = skip_reason
            image.sha256 = normalized.sha256
            summary.skipped += 1
            return

    captured = image.captured_date or business_today
    derivative_key = normalized_derivative_key(image.farm_id, captured, image.id, normalized.sha256)
    try:
        await asyncio.to_thread(storage.upload, derivative_key, normalized.data, "image/jpeg")
    except ScreeningStorageError as exc:
        # The gate could still run on the raw bytes, but then the review UI
        # would have no bounded image to show; fail and retry the whole image.
        image.status = ScreeningImageStatus.ERROR.value
        logger.warning(
            "screening derivative upload failed image_id=%s code=DERIVATIVE_UPLOAD_FAILED",
            image.id,
        )
        image.error = _tenant_safe_error(exc)
        summary.errors += 1
        return

    image.sha256 = normalized.sha256
    image.width = normalized.width
    image.height = normalized.height
    image.byte_size = normalized.byte_size
    image.normalized_key = derivative_key

    # Bind the database record durably to the sanitized derivative before
    # removing raw bytes. If object-store deletion fails, a later claim uses
    # the derivative above and retries deletion before any provider call.
    await db.flush()
    await db.commit()
    try:
        await asyncio.to_thread(storage.delete_permanently, [image.s3_key])
    except ScreeningStorageError as exc:
        image.status = ScreeningImageStatus.ERROR.value
        image.error = _tenant_safe_error(exc)
        logger.warning(
            "raw screening photo deletion failed image_id=%s code=RAW_DELETE_FAILED",
            image.id,
        )
        summary.errors += 1
        return
    if image.upload_token is not None:
        image.upload_token = None
        image.upload_content_type = None
    await db.flush()
    await db.commit()

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
        elif status == ScreeningImageStatus.UNASSESSABLE.value:
            image.error = _reason_text(ScreeningErrorReason.QUALITY_PROBLEM)
            summary.unassessable += 1
        else:
            image.error = _reason_text(ScreeningErrorReason.PROVIDER_ERROR)
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
    # Detection (or the boxed re-entry) can take as long as the whole
    # rotation at provider timeout; renew the lease before the cascades.
    await _touch_processing_lease(db, image)

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
        elif status == ScreeningImageStatus.UNASSESSABLE.value:
            image.error = _reason_text(ScreeningErrorReason.QUALITY_PROBLEM)
            summary.unassessable += 1
        else:
            image.error = _reason_text(ScreeningErrorReason.PROVIDER_ERROR)
            summary.errors += 1
        return

    # Detector boxes are proposals, not proof of full-frame coverage. Run a
    # whole-frame safety gate as well so an undetected goat or lesion outside
    # every crop can prevent a false HEALTHY parent. A completed pass is
    # reused on crop-only retry to avoid duplicate findings/provider spend.
    coverage_status = await _prior_coverage_safety_status(db, image)
    if coverage_status is None:
        coverage_status = await _run_cascade(
            db,
            settings,
            rotation,
            image,
            None,
            normalized.data,
            summary,
            business_today,
            coverage_safety_pass=True,
            # A crop can still flag after this healthy whole-frame gate. Queue
            # the control only after the aggregate outcome is known healthy,
            # otherwise the simultaneous positive finding would unblind it.
            sample_healthy=False,
        )
    if coverage_status == ScreeningImageStatus.ERROR.value:
        image.status = ScreeningImageStatus.ERROR.value
        image.error = _reason_text(ScreeningErrorReason.PROVIDER_ERROR)
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

    # The whole-frame pass participates in the parent verdict: flagged wins,
    # then crop/provider errors, then unassessable, then healthy.
    crop_statuses: list[str] = [coverage_status]
    for crop, box in zip(crops, boxes, strict=True):
        if crop.status not in (
            ScreeningImageStatus.PENDING.value,
            # A daily-budget stop commits the parent ERROR and the in-flight
            # crop PROCESSING so already-written whole-frame evidence is not
            # lost. Once the parent is reclaimed, that crop is abandoned work
            # owned by this new claim and must be screened again.
            ScreeningImageStatus.PROCESSING.value,
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
            logger.warning(
                "screening crop failed image_id=%s crop_index=%s code=CROP_PROCESSING_FAILED",
                image.id,
                crop.crop_index,
            )
            crop.error = _tenant_safe_error(exc)
            crop_statuses.append(ScreeningImageStatus.ERROR.value)
            continue
        crop_derivative = cropped_derivative_key(
            image.farm_id,
            captured,
            image.id,
            crop.id,
            normalized.sha256,
            crop.crop_index,
        )
        try:
            await asyncio.to_thread(storage.upload, crop_derivative, cropped.data, "image/jpeg")
        except ScreeningStorageError as exc:
            crop.status = ScreeningImageStatus.ERROR.value
            logger.warning(
                "screening crop derivative upload failed image_id=%s "
                "code=CROP_DERIVATIVE_UPLOAD_FAILED",
                image.id,
            )
            crop.error = _tenant_safe_error(exc)
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
            sample_healthy=False,
        )
        crop.status = status
        if status == ScreeningImageStatus.ERROR.value:
            crop.error = _reason_text(ScreeningErrorReason.PROVIDER_ERROR)
        elif status == ScreeningImageStatus.UNASSESSABLE.value:
            crop.error = _reason_text(ScreeningErrorReason.QUALITY_PROBLEM)
        crop_statuses.append(status)
        # One completed crop = one lease renewal: a multi-crop photo must
        # never look stale while it is still making per-crop progress.
        await _touch_processing_lease(db, image)

    image.status = _aggregate_crop_statuses(crop_statuses)
    if image.status == ScreeningImageStatus.HEALTHY.value and _sample_healthy_control(image):
        coverage_gate_run = (
            await db.execute(
                select(ScreeningRun)
                .where(
                    ScreeningRun.farm_id == image.farm_id,
                    ScreeningRun.image_id == image.id,
                    ScreeningRun.crop_id.is_(None),
                    ScreeningRun.stage == ScreeningStage.GATE.value,
                    ScreeningRun.run_status == ScreeningRunStatus.OK.value,
                    ScreeningRun.detail["coverage_safety_pass"].astext == "true",
                )
                .order_by(ScreeningRun.created_at.desc(), ScreeningRun.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if coverage_gate_run is not None:
            await _enqueue_healthy_control(db, image, coverage_gate_run)
    if image.status == ScreeningImageStatus.FLAGGED.value:
        summary.flagged += 1
    elif image.status == ScreeningImageStatus.HEALTHY.value:
        summary.healthy += 1
    elif image.status == ScreeningImageStatus.UNASSESSABLE.value:
        image.error = _reason_text(ScreeningErrorReason.QUALITY_PROBLEM)
        summary.unassessable += 1
    else:
        image.error = "one or more goats could not be screened (see crop rows)"
        summary.errors += 1
