"""Pydantic schemas for the screening review API.

Reads for the review queue; Phase 2 adds the vet review mutation
(confirm/reject with optimistic concurrency on the current status).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import MAX_FREE_TEXT_LENGTH, MAX_INT32_ID, PostgresText, StrictInputModel, StrictInt

# Mirrors models.enums.ScreeningImageStatus.
ScreeningImageStatusStr = Literal[
    "PENDING", "PROCESSING", "HEALTHY", "UNASSESSABLE", "FLAGGED", "SKIPPED", "ERROR"
]
# Mirrors models.enums.ScreeningFindingStatus.
ScreeningFindingStatusStr = Literal["PENDING_REVIEW", "CONFIRMED", "REJECTED"]
# Mirrors models.enums.ScreeningRunStatus.
ScreeningRunStatusStr = Literal["OK", "ERROR"]
# Mirrors models.enums.ScreeningStage.
ScreeningStageStr = Literal[
    "DETECT",
    "GATE",
    "CROSS_CHECK",
    "SPECIALIST_SKIN",
    "SPECIALIST_EYE",
    "SPECIALIST_HOOF",
    "SPECIALIST_UDDER",
    "SPECIALIST_GENERAL",
]
# Mirrors models.enums.Bucket (herd buckets / pens) — same vocabulary as
# schemas.animals.BucketStr, restated locally to keep the screening schema
# self-contained for the generated client.
ScreeningBucketStr = Literal[
    "QUARANTINE",
    "FOUNDATION",
    "BREEDING",
    "PREGNANCY_EARLY",
    "PREGNANCY_LATE",
    "DELIVERY",
    "RECOVERY",
    "RESTING",
    "MALE_KIDS",
    "FEMALE_KIDS",
]

# Kept in the request schema as well as exposed in ``ScreeningUploadOut`` so
# an oversized local file is rejected before the server reserves a PENDING
# intake row and signs an object-store form for it.
MAX_SCREENING_UPLOAD_BYTES = 25 * 1024 * 1024


# Mirrors models.enums.ScreeningSeverity.
ScreeningSeverityStr = Literal["mild", "moderate", "severe"]


class ScreeningFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    crop_id: int | None = None
    region: str | None
    label: str
    evaluation_kind: Literal["POSITIVE_FINDING", "HEALTHY_CONTROL"]
    confidence: Decimal | None
    severity: ScreeningSeverityStr | None
    note: str | None
    status: ScreeningFindingStatusStr
    review_note: str | None
    # Reviewer attribution, exposed like every sibling's *_by_id: it was the one attribution the
    # review payload hid.
    reviewed_by_id: int | None
    reviewed_at: datetime | None
    review_revision: int
    created_at: datetime


class ScreeningFindingReviewIn(StrictInputModel):
    """Vet verdict on one finding.

    ``expected_revision`` is optimistic concurrency: two reviewers (or a
    reviewer racing a re-screen) get a 409 instead of silently overwriting
    each other's verdict on the training corpus.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["CONFIRMED", "REJECTED"]
    expected_status: ScreeningFindingStatusStr = "PENDING_REVIEW"
    expected_revision: Annotated[StrictInt, Field(ge=0, le=MAX_INT32_ID)]
    review_note: PostgresText | None = Field(default=None, max_length=MAX_FREE_TEXT_LENGTH)

    @field_validator("review_note")
    @classmethod
    def _blank_review_note_is_none(cls, value: str | None) -> str | None:
        # The DB CHECK rejects blank-but-non-NULL review notes; mapping the
        # blank here keeps a whitespace-only note a valid no-note request
        # (422-free) instead of an unhandled IntegrityError (500).
        return value or None


class ScreeningFindingReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: ScreeningFindingStatusStr
    review_note: str | None
    # Reviewer attribution, like ScreeningFindingOut.
    reviewed_by_id: int | None
    reviewed_at: datetime | None
    review_revision: int


class ScreeningFindingReviewHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    revision: int
    previous_status: ScreeningFindingStatusStr
    status: Literal["CONFIRMED", "REJECTED"]
    review_note: str | None
    reviewed_by_id: int
    reviewed_at: datetime


class ScreeningFindingReviewHistoryListOut(BaseModel):
    finding_id: int
    review_revision: int
    # Revision zero preserves the actual known legacy decision. It is a
    # baseline snapshot (previous_status == status), not a fabricated prior
    # transition, and retains its original reviewer, timestamp and note.
    legacy_review: bool
    reviews: list[ScreeningFindingReviewHistoryOut]
    total: int
    limit: int
    offset: int


class ScreeningCropOut(BaseModel):
    """One detected goat inside a photo, with its own cascade verdict."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    crop_index: int
    box_x: int
    box_y: int
    box_w: int
    box_h: int
    status: ScreeningImageStatusStr
    error: str | None
    created_at: datetime
    # Short-lived presigned URL for the crop derivative, filled by the
    # detail endpoint when screening storage is configured.
    image_url: str | None = None


class ScreeningRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    image_id: int
    crop_id: int | None = None
    stage: ScreeningStageStr
    run_status: ScreeningRunStatusStr
    verdict: str | None
    confidence: Decimal | None
    provider: str
    model: str
    prompt_version: str
    latency_ms: int | None
    detail: dict[str, Any] | None
    error: str | None
    created_at: datetime


class ScreeningImageRowOut(BaseModel):
    """One row of the review list: the image plus its latest gate verdict
    and its pending-review finding count (the queue the vet works down)."""

    id: int
    status: ScreeningImageStatusStr
    bucket: ScreeningBucketStr | None = None
    batch_id: int | None = None
    s3_key: str
    captured_date: date | None
    width: int | None
    height: int | None
    byte_size: int | None
    error: str | None
    created_at: datetime
    latest_run: ScreeningRunOut | None = None
    pending_findings: int = 0
    pending_healthy_controls: int = 0


class ScreeningImageListOut(BaseModel):
    images: list[ScreeningImageRowOut]
    total: int
    limit: int
    offset: int


class ScreeningImageDetailOut(BaseModel):
    """Full review payload: the image, its bounded (normalized) image URL,
    every model run and every finding ever recorded against it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: ScreeningImageStatusStr
    bucket: ScreeningBucketStr | None = None
    batch_id: int | None = None
    s3_key: str
    captured_date: date | None
    width: int | None
    height: int | None
    byte_size: int | None
    sha256: str | None
    error: str | None
    created_at: datetime
    image_url: str | None = None
    crops: list[ScreeningCropOut] = Field(default_factory=list)
    runs: list[ScreeningRunOut] = Field(default_factory=list)
    findings: list[ScreeningFindingOut] = Field(default_factory=list)


class ScreeningProviderStatsOut(BaseModel):
    """Rotation-provider scoreboard over the requested window: call volume,
    conditional reviewed-positive precision, sampled healthy-verdict misses,
    and cross-check agreement. It is not full sensitivity/specificity."""

    provider: str
    model: str
    gate_runs: int
    gate_flagged: int
    gate_unassessable: int = 0
    gate_errors: int
    avg_gate_latency_ms: int | None
    avg_gate_confidence: Decimal | None
    cross_checks: int
    cross_check_agreements: int
    findings_confirmed: int
    findings_rejected: int
    findings_pending: int
    # Positive precision is conditional on the model having emitted a
    # finding. Healthy-control false-negative rate comes from a deterministic
    # sample of healthy verdicts; neither field claims full sensitivity.
    positive_precision: Decimal | None = None
    positive_precision_ci_low: Decimal | None = None
    positive_precision_ci_high: Decimal | None = None
    healthy_controls_confirmed: int = 0
    healthy_controls_rejected: int = 0
    healthy_controls_pending: int = 0
    healthy_controls_reviewed: int = 0
    healthy_false_negative_rate: Decimal | None = None
    healthy_false_negative_ci_low: Decimal | None = None
    healthy_false_negative_ci_high: Decimal | None = None


class ScreeningStatsOut(BaseModel):
    window_days: int
    providers: list[ScreeningProviderStatsOut]


class ScreeningDatasetRecordOut(BaseModel):
    """One training example: immutable normalized image + optional crop box,
    model label, and the vet verdict that makes the label trustworthy.

    ``image_s3_key`` intentionally identifies the normalized derivative that
    was actually sent to the model, not the short-lived browser-upload raw
    key. A presigned raw POST may be replayed before expiry; the derivative is
    worker-owned and its bytes match ``image_sha256``. For
    ``HEALTHY_CONTROL`` examples, ``CONFIRMED`` means the reviewer also saw no
    abnormality; ``REJECTED`` is a hard negative where the reviewer found one.
    """

    finding_id: int
    example_kind: Literal["POSITIVE_FINDING", "HEALTHY_CONTROL"]
    model_verdict: Literal["healthy", "flagged", "unassessable"] | None
    prompt_version: str
    vet_status: ScreeningFindingStatusStr
    label: str
    confidence: Decimal | None
    severity: ScreeningSeverityStr | None
    region: str | None
    captured_date: date | None
    image_s3_key: str
    image_sha256: str | None
    crop_index: int | None
    crop_box_1000: list[int] | None
    crop_s3_key: str | None
    detected_by: str
    reviewed_at: datetime | None


class ScreeningDatasetExportOut(BaseModel):
    farm_id: int
    generated_at: datetime
    record_count: int
    records: list[ScreeningDatasetRecordOut]


class ScreeningBatchBucketProgressOut(BaseModel):
    """Per-pen photo coverage for one disease-check walkthrough."""

    bucket: ScreeningBucketStr
    uploaded: int
    screened: int
    flagged: int
    unassessable: int = 0


class ScreeningBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    submitted_at: datetime | None
    images_uploaded: int = 0
    images_screened: int = 0
    images_flagged: int = 0
    images_unassessable: int = 0
    buckets: list[ScreeningBatchBucketProgressOut] = Field(default_factory=list)


class ScreeningBatchListOut(BaseModel):
    batches: list[ScreeningBatchOut]
    total: int
    limit: int
    offset: int


class ScreeningBatchCreateIn(StrictInputModel):
    """Empty input model for the bodyless batch-intake idempotency claim.

    The request identity consists only of actor, farm, and key. An empty payload
    keeps the fingerprint stable on replay. Unknown fields are forbidden, as in
    every other request model."""


class ScreeningUploadIn(StrictInputModel):
    """Request a direct-upload URL for one photo taken inside a pen."""

    model_config = ConfigDict(str_strip_whitespace=True)

    batch_id: Annotated[int, Field(ge=1, le=MAX_INT32_ID)]
    bucket: ScreeningBucketStr
    # Extension only — the server generates the stored filename.
    file_name: str = Field(min_length=5, max_length=255, pattern=r"^[\w.\- ]+\.(?i:jpe?g|png)$")
    content_type: Literal["image/jpeg", "image/png"]
    # The browser already has the selected File at this point.  Requiring its
    # size lets the API reject an impossible upload before it consumes one of
    # the farm's bounded pre-registrations; the S3 POST policy independently
    # enforces the same cap against a dishonest client.
    file_size: int = Field(ge=1, le=MAX_SCREENING_UPLOAD_BYTES)


class ScreeningUploadOut(BaseModel):
    """A constrained browser-to-object-store POST form.

    ``upload_url`` is retained as the transport target for existing clients,
    but callers must use ``upload_method`` and append every ``upload_fields``
    entry to a ``FormData`` before appending the file as ``file``.  The S3
    policy binds the form to this pre-registered image and caps its bytes.
    """

    image_id: int
    s3_key: str
    upload_url: str
    upload_method: Literal["POST"] = "POST"
    upload_fields: dict[str, str]
    max_upload_bytes: int
    expires_in_seconds: int
