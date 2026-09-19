"""Disease screening: daily S3 photo batches through a cascading model
pipeline (Phase 1: single gate model; healthy verdicts stop the cascade)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import utcnow
from .enums import (
    Bucket,
    ScreeningFindingStatus,
    ScreeningImageStatus,
    ScreeningRunStatus,
    ScreeningSeverity,
    ScreeningStage,
    sql_in_values,
)

if TYPE_CHECKING:
    from .core import Farm

# S3 key segment bounds (also enforced by the key parser in the pipeline).
MAX_S3_BUCKET_LENGTH = 255
MAX_S3_KEY_LENGTH = 1_024
MAX_SCREENING_PROVIDER_LENGTH = 40
MAX_SCREENING_MODEL_LENGTH = 120
MAX_SCREENING_LABEL_LENGTH = 80
MAX_SCREENING_REGION_LENGTH = 40
# Confidence columns are NUMERIC(4,3) CHECK-bounded to [0, 1] so a
# schema-valid-but-absurd value cannot reach the review UI.
SCREENING_CONFIDENCE_SQL = "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)"


class ScreeningBatch(Base):
    """One "disease check" walkthrough of the farm (Phase upload).

    A farm worker opens a batch, photographs each herd bucket (pen) —
    capture → upload per photo via constrained presigned POST — then submits it. The
    worker screens every photo in the batch as the bytes land; the API
    derives per-bucket progress from the image rows (uploaded / screened /
    flagged), so the batch needs no worker-side state machine.
    """

    __tablename__ = "screening_batches"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="uq_screening_batches_farm_id_id"),
        CheckConstraint(
            "submitted_at IS NULL OR submitted_at >= created_at",
            name="ck_screening_batches_submit_after_create",
        ),
        Index("ix_screening_batches_farm_created", "farm_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    # Null while the walkthrough is still collecting photos; set by the
    # submit endpoint ("process them") once every bucket has been covered.
    submitted_at: Mapped[dt.datetime | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )


class ScreeningImage(Base):
    """One raw photo claimed from S3, screened or errored, per farm.

    The (bucket, key) pair is the intake idempotency boundary: the poller may
    list the same object on every cycle, but only the first claim inserts.
    Uploads pre-create rows (status PENDING, bytes possibly not yet in S3);
    the worker claims PENDING rows whose object exists."""

    __tablename__ = "screening_images"
    __table_args__ = (
        UniqueConstraint("s3_bucket", "s3_key", name="uq_screening_images_object"),
        UniqueConstraint("farm_id", "id", name="uq_screening_images_farm_id_id"),
        CheckConstraint(
            f"status IN ({sql_in_values(ScreeningImageStatus)})",
            name="ck_screening_images_status",
        ),
        # Only ERROR rows carry an error, and must: a HEALTHY/FLAGGED/SKIPPED
        # row that also "errored" would contradict the review UI's semantics.
        CheckConstraint(
            "status <> 'ERROR' OR (error IS NOT NULL AND btrim(error) <> '')",
            name="ck_screening_images_error_requires_error_status",
        ),
        CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0",
            name="ck_screening_images_byte_size_nonneg",
        ),
        CheckConstraint(
            "width IS NULL OR height IS NULL OR (width > 0 AND height > 0)",
            name="ck_screening_images_dimensions_positive",
        ),
        CheckConstraint(
            f"bucket IS NULL OR bucket IN ({sql_in_values(Bucket)})",
            name="ck_screening_images_bucket_vocabulary",
        ),
        CheckConstraint(
            "upload_content_type IS NULL OR upload_content_type IN ('image/jpeg', 'image/png')",
            name="ck_screening_images_upload_content_type",
        ),
        CheckConstraint(
            "(upload_content_type IS NULL) = (upload_token IS NULL)",
            name="ck_screening_images_upload_registration_pair",
        ),
        CheckConstraint(
            "upload_token IS NULL OR length(upload_token) >= 32",
            name="ck_screening_images_upload_token_nontrivial",
        ),
        ForeignKeyConstraint(
            ["farm_id", "batch_id"],
            ["screening_batches.farm_id", "screening_batches.id"],
            name="fk_screening_images_batch",
        ),
        Index(
            "ix_screening_images_farm_status_created",
            "farm_id",
            "status",
            "created_at",
        ),
        Index("ix_screening_images_batch", "farm_id", "batch_id"),
        # Claim-fairness scan: status + next_attempt_at drive eligibility,
        # farm_id partitions the round-robin, created_at/id break ties.
        # Must match c4d8e1f9a2b7_screening_upload_hardening exactly or
        # Alembic autogenerate reports drift.
        Index(
            "ix_screening_images_claim_fairness",
            "status",
            "next_attempt_at",
            "farm_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    # Herd bucket (pen) the photo was taken in — from the upload flow or the
    # raw/<farm>/<date>/<bucket>/ key segment; null for legacy whole-farm
    # uploads that carried no bucket.
    bucket: Mapped[str | None] = mapped_column(String(30))
    batch_id: Mapped[int | None] = mapped_column(Integer)
    s3_bucket: Mapped[str] = mapped_column(String(MAX_S3_BUCKET_LENGTH))
    s3_key: Mapped[str] = mapped_column(String(MAX_S3_KEY_LENGTH))
    # Direct uploads are pre-registered before the browser receives a
    # constrained presigned POST.  The worker checks both values from the
    # object's HEAD metadata before it ever decodes bytes, so merely being
    # able to write something under the raw prefix cannot associate it with
    # a tenant image row.
    upload_content_type: Mapped[str | None] = mapped_column(String(20))
    upload_token: Mapped[str | None] = mapped_column(String(64))
    # Content hash of the normalized bytes; identical bytes re-uploaded under
    # a second key are SKIPPED as duplicates instead of re-billed to a model.
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # Derived (normalized, model-sized) key, written back to the same bucket
    # so review UIs load a ~200 KB JPEG, not the 20 MB original.
    normalized_key: Mapped[str | None] = mapped_column(String(MAX_S3_KEY_LENGTH))
    captured_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default=ScreeningImageStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text)
    # A minted URL may not have landed in object storage yet.  Backing off
    # those probes prevents a collection of slow/abandoned phones from
    # monopolizing every screening cycle before the presign expiry sweep.
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column()
    # Bounded retry budget, incremented by every claim.  Once it reaches the
    # pipeline's attempt cap the row is terminal and never re-claimed, so a
    # deterministic failure cannot poll and re-bill providers forever.
    screening_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        default=utcnow,
        onupdate=utcnow,
        # text() so alembic autogenerate compares a SQL expression, not a
        # quoted literal (the string form breaks `alembic check`).
        server_default=text("timezone('UTC', now())"),
        server_onupdate=text("timezone('UTC', now())"),
    )

    farm: Mapped[Farm] = relationship()
    crops: Mapped[list[ScreeningCrop]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )
    runs: Mapped[list[ScreeningRun]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )


class ScreeningContentClaim(Base):
    """The canonical image for one normalized content digest within a farm.

    ``screening_images`` deliberately permits repeated content: each upload
    needs its own intake/audit row.  This compact table supplies the separate
    concurrency boundary, so two workers that normalize two keys to the same
    bytes cannot both send them to a paid model.  The composite foreign key
    makes the canonical image tenant-local even if an id is supplied from a
    different farm.
    """

    __tablename__ = "screening_content_claims"
    __table_args__ = (
        UniqueConstraint("farm_id", "sha256", name="uq_screening_content_claims_farm_sha256"),
        # A raw upload is immutable once its normalized content has been
        # claimed.  This prevents a later overwrite of its S3 key from making
        # one review record canonical for two different byte streams.
        UniqueConstraint("farm_id", "image_id", name="uq_screening_content_claims_farm_image"),
        CheckConstraint("length(sha256) = 64", name="ck_screening_content_claims_sha256_length"),
        ForeignKeyConstraint(
            ["farm_id", "image_id"],
            ["screening_images.farm_id", "screening_images.id"],
            name="fk_screening_content_claims_image",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(Integer)
    image_id: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )


class ScreeningCrop(Base):
    """One goat detected inside a photo (Phase 3).

    The detection stage emits a box per goat; each crop runs the cascade
    (gate → specialists → cross-check) independently, so "stop at the
    gate" happens per goat, not per photo. Rows appear only when detection
    found at least one goat; photos with no detected goats screen as a
    whole and have no crops."""

    __tablename__ = "screening_crops"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="uq_screening_crops_farm_id_id"),
        UniqueConstraint(
            "farm_id", "image_id", "crop_index", name="uq_screening_crops_image_index"
        ),
        ForeignKeyConstraint(
            ["farm_id", "image_id"],
            ["screening_images.farm_id", "screening_images.id"],
            name="fk_screening_crops_image",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"status IN ({sql_in_values(ScreeningImageStatus)})",
            name="ck_screening_crops_status",
        ),
        # Detection boxes are 0-1000 normalized (provider convention) and
        # persisted verbatim for the training export.
        CheckConstraint(
            "box_x >= 0 AND box_x <= 1000 AND box_y >= 0 AND box_y <= 1000 "
            "AND box_w > 0 AND box_w <= 1000 AND box_h > 0 AND box_h <= 1000",
            name="ck_screening_crops_box_bounds",
        ),
        CheckConstraint(
            "status <> 'ERROR' OR (error IS NOT NULL AND btrim(error) <> '')",
            name="ck_screening_crops_error_requires_error_status",
        ),
        Index("ix_screening_crops_farm_image", "farm_id", "image_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(Integer)
    image_id: Mapped[int] = mapped_column(Integer)
    crop_index: Mapped[int] = mapped_column(Integer)
    box_x: Mapped[int] = mapped_column(Integer)
    box_y: Mapped[int] = mapped_column(Integer)
    box_w: Mapped[int] = mapped_column(Integer)
    box_h: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    normalized_key: Mapped[str | None] = mapped_column(String(MAX_S3_KEY_LENGTH))
    status: Mapped[str] = mapped_column(String(20), default=ScreeningImageStatus.PENDING.value)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )

    image: Mapped[ScreeningImage] = relationship(back_populates="crops")


class ScreeningRun(Base):
    """One model invocation over one image: provider, model, prompt version,
    verdict, latency. The audit trail that later powers per-provider accuracy
    stats and the Phase 2 rotation policy."""

    __tablename__ = "screening_runs"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="uq_screening_runs_farm_id_id"),
        ForeignKeyConstraint(
            ["farm_id", "image_id"],
            ["screening_images.farm_id", "screening_images.id"],
            name="fk_screening_runs_image",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["farm_id", "crop_id"],
            ["screening_crops.farm_id", "screening_crops.id"],
            name="fk_screening_runs_crop",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"stage IN ({sql_in_values(ScreeningStage)})",
            name="ck_screening_runs_stage",
        ),
        CheckConstraint(
            f"run_status IN ({sql_in_values(ScreeningRunStatus)})",
            name="ck_screening_runs_status",
        ),
        # Verdicts exist only for OK runs; provider/API failures record
        # ERROR status + error text instead of inventing a verdict.
        CheckConstraint(
            "run_status <> 'OK' OR verdict IN ('healthy', 'flagged')",
            name="ck_screening_runs_verdict_vocabulary",
        ),
        CheckConstraint(
            SCREENING_CONFIDENCE_SQL,
            name="ck_screening_runs_confidence_bounded",
        ),
        CheckConstraint(
            "run_status <> 'OK' OR latency_ms IS NOT NULL",
            name="ck_screening_runs_ok_requires_latency",
        ),
        CheckConstraint(
            "btrim(provider) <> '' AND btrim(model) <> ''",
            name="ck_screening_runs_provenance_nonblank",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_screening_runs_latency_nonneg",
        ),
        Index("ix_screening_runs_image_created", "image_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(Integer)
    image_id: Mapped[int] = mapped_column(Integer)
    # Null for whole-photo runs (detection off, no goats found, or Phase ≤2
    # rows); set when this run screened one detected goat.
    crop_id: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(20), default=ScreeningStage.GATE.value)
    run_status: Mapped[str] = mapped_column(String(10), default=ScreeningRunStatus.OK.value)
    verdict: Mapped[str | None] = mapped_column(String(10))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    provider: Mapped[str] = mapped_column(String(MAX_SCREENING_PROVIDER_LENGTH))
    model: Mapped[str] = mapped_column(String(MAX_SCREENING_MODEL_LENGTH))
    prompt_version: Mapped[str] = mapped_column(String(20))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # Bounded response summary (observations + usage), never the full payload.
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )

    image: Mapped[ScreeningImage] = relationship(back_populates="runs")
    findings: Mapped[list[ScreeningFinding]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ScreeningFinding(Base):
    """One observation the model wants a human to look at.

    Phase 1 findings are gate-level ("possible lesion near mouth"); Phase 2
    specialist runs refine the label vocabulary. pending_review rows are the
    review queue; confirmed/rejected rows accumulate as training labels.
    """

    __tablename__ = "screening_findings"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="uq_screening_findings_farm_id_id"),
        ForeignKeyConstraint(
            ["farm_id", "run_id"],
            ["screening_runs.farm_id", "screening_runs.id"],
            name="fk_screening_findings_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["farm_id", "crop_id"],
            ["screening_crops.farm_id", "screening_crops.id"],
            name="fk_screening_findings_crop",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"status IN ({sql_in_values(ScreeningFindingStatus)})",
            name="ck_screening_findings_status",
        ),
        CheckConstraint(
            f"severity IS NULL OR severity IN ({sql_in_values(ScreeningSeverity)})",
            name="ck_screening_findings_severity",
        ),
        CheckConstraint(
            SCREENING_CONFIDENCE_SQL,
            name="ck_screening_findings_confidence_bounded",
        ),
        CheckConstraint(
            "btrim(label) <> '' AND (note IS NULL OR btrim(note) <> '')",
            name="ck_screening_findings_text_nonblank",
        ),
        # A verdict exists exactly when a reviewer recorded it: PENDING_REVIEW
        # rows carry no review fields, CONFIRMED/REJECTED rows must.
        CheckConstraint(
            "(status = 'PENDING_REVIEW') = (reviewed_at IS NULL AND reviewed_by_id IS NULL)",
            name="ck_screening_findings_review_matches_status",
        ),
        CheckConstraint(
            "review_note IS NULL OR btrim(review_note) <> ''",
            name="ck_screening_findings_review_note_nonblank",
        ),
        Index(
            "ix_screening_findings_farm_status_created",
            "farm_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[int] = mapped_column(Integer)
    # Which detected goat this finding belongs to; null for whole-photo
    # findings. Denormalized from the run for direct review/export queries.
    crop_id: Mapped[int | None] = mapped_column(Integer)
    # Body region vocabulary is free-form English from the gate ("mouth",
    # "eye", "hoof", "udder", "skin", "general"); specialist findings keep
    # the gate region that triggered them.
    region: Mapped[str | None] = mapped_column(String(MAX_SCREENING_REGION_LENGTH))
    label: Mapped[str] = mapped_column(String(MAX_SCREENING_LABEL_LENGTH))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    severity: Mapped[str | None] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default=ScreeningFindingStatus.PENDING_REVIEW.value
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    reviewed_at: Mapped[dt.datetime | None] = mapped_column()
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )

    run: Mapped[ScreeningRun] = relationship(back_populates="findings")
