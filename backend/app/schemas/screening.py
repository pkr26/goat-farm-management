"""Pydantic schemas for the screening review API.

Reads for the review queue; Phase 2 adds the vet review mutation
(confirm/reject with optimistic concurrency on the current status).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import MAX_FREE_TEXT_LENGTH, MAX_INT32_ID, StrictInputModel

# Mirrors models.enums.ScreeningImageStatus.
ScreeningImageStatusStr = Literal["PENDING", "PROCESSING", "HEALTHY", "FLAGGED", "SKIPPED", "ERROR"]
# Mirrors models.enums.ScreeningFindingStatus.
ScreeningFindingStatusStr = Literal["PENDING_REVIEW", "CONFIRMED", "REJECTED"]
# Mirrors models.enums.ScreeningRunStatus.
ScreeningRunStatusStr = Literal["OK", "ERROR"]
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

ALLOWED_UPLOAD_CONTENT_TYPES = ("image/jpeg", "image/png")
# Kept in the request schema as well as exposed in ``ScreeningUploadOut`` so
# an oversized local file is rejected before the server reserves a PENDING
# intake row and signs an object-store form for it.
MAX_SCREENING_UPLOAD_BYTES = 25 * 1024 * 1024


ScreeningImageId = Annotated[int, Field(ge=1, le=MAX_INT32_ID)]


# Mirrors models.enums.ScreeningSeverity.
ScreeningSeverityStr = Literal["mild", "moderate", "severe"]


class ScreeningFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    crop_id: int | None = None
    region: str | None
    label: str
    confidence: Decimal | None
    severity: ScreeningSeverityStr | None
    note: str | None
    status: ScreeningFindingStatusStr
    review_note: str | None
    reviewed_at: datetime | None
    created_at: datetime


class ScreeningFindingReviewIn(StrictInputModel):
    """Vet verdict on one finding.

    ``expected_status`` is optimistic concurrency: two reviewers (or a
    reviewer racing a re-screen) get a 409 instead of silently overwriting
    each other's verdict on the training corpus.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["CONFIRMED", "REJECTED"]
    expected_status: ScreeningFindingStatusStr = "PENDING_REVIEW"
    review_note: str | None = Field(default=None, max_length=MAX_FREE_TEXT_LENGTH)

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
    reviewed_at: datetime | None


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
    stage: str
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
    verdict behavior, vet-labeled precision, and cross-check agreement."""

    provider: str
    model: str
    gate_runs: int
    gate_flagged: int
    gate_errors: int
    avg_gate_latency_ms: int | None
    avg_gate_confidence: Decimal | None
    cross_checks: int
    cross_check_agreements: int
    findings_confirmed: int
    findings_rejected: int
    findings_pending: int


class ScreeningStatsOut(BaseModel):
    window_days: int
    providers: list[ScreeningProviderStatsOut]


class ScreeningDatasetRecordOut(BaseModel):
    """One training example: immutable normalized image + optional crop box,
    model label, and the vet verdict that makes the label trustworthy.

    ``image_s3_key`` intentionally identifies the normalized derivative that
    was actually sent to the model, not the short-lived browser-upload raw
    key. A presigned raw POST may be replayed before expiry; the derivative is
    worker-owned and its bytes match ``image_sha256``.
    """

    finding_id: int
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


class ScreeningBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    submitted_at: datetime | None
    images_uploaded: int = 0
    images_screened: int = 0
    images_flagged: int = 0
    buckets: list[ScreeningBatchBucketProgressOut] = Field(default_factory=list)


class ScreeningBatchListOut(BaseModel):
    batches: list[ScreeningBatchOut]


class ScreeningBatchCreateIn(StrictInputModel):
    """Degenerate input model for the bodyless batch-intake idempotency claim.

    B5 (2026-09-21 audit): POST /batches accepts no request body, so the
    idempotency claim's request identity is exactly (actor, farm, key). An
    empty payload pins that fingerprint with nothing else to disagree with —
    a replay of the same key can never 409 as a changed request. Like every
    other request model it forbids unknown fields: zero declared fields means
    any supplied key is rejected."""


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
