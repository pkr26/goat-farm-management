"""disease screening: images, runs, findings

Phase 1 of the photo-screening pipeline. Three tables:

- ``screening_images`` — one row per S3 object claimed by the poller; the
  (bucket, key) unique constraint is the intake idempotency boundary.
- ``screening_runs`` — the per-invocation audit trail (provider, model,
  prompt version, verdict, latency) that later backs per-provider accuracy
  stats and the Phase 2 round-robin rotation policy. Composite tenant FK to
  the image, CASCADE so deleting an image's history deletes with it.
- ``screening_findings`` — observations awaiting vet review; confirmed and
  rejected rows accumulate as the training-label corpus.

Revision ID: a7c8d9e0f1b2
Revises: f9b3c7d1e5a2
"""

from sqlalchemy import text

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7c8d9e0f1b2"
down_revision = "f9b3c7d1e5a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "screening_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "farm_id",
            sa.Integer(),
            sa.ForeignKey("farms.id"),
            nullable=False,
        ),
        sa.Column("s3_bucket", sa.String(length=255), nullable=False),
        sa.Column("s3_key", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("normalized_key", sa.String(length=1024), nullable=True),
        sa.Column("captured_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'HEALTHY', 'FLAGGED', 'SKIPPED', 'ERROR')",
            name="ck_screening_images_status",
        ),
        sa.CheckConstraint(
            "status <> 'ERROR' OR (error IS NOT NULL AND btrim(error) <> '')",
            name="ck_screening_images_error_requires_error_status",
        ),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0",
            name="ck_screening_images_byte_size_nonneg",
        ),
        sa.CheckConstraint(
            "width IS NULL OR height IS NULL OR (width > 0 AND height > 0)",
            name="ck_screening_images_dimensions_positive",
        ),
        sa.UniqueConstraint("s3_bucket", "s3_key", name="uq_screening_images_object"),
        sa.UniqueConstraint("farm_id", "id", name="uq_screening_images_farm_id_id"),
    )
    op.create_index("ix_screening_images_farm_id", "screening_images", ["farm_id"])
    op.create_index(
        "ix_screening_images_farm_status_created",
        "screening_images",
        ["farm_id", "status", "created_at"],
    )
    op.create_index("ix_screening_images_sha256", "screening_images", ["sha256"])

    op.create_table(
        "screening_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("run_status", sa.String(length=10), nullable=False),
        sa.Column("verdict", sa.String(length=10), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=20), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "image_id"],
            ["screening_images.farm_id", "screening_images.id"],
            name="fk_screening_runs_image",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("stage IN ('GATE')", name="ck_screening_runs_stage"),
        sa.CheckConstraint("run_status IN ('OK', 'ERROR')", name="ck_screening_runs_status"),
        sa.CheckConstraint(
            "run_status <> 'OK' OR verdict IN ('healthy', 'flagged')",
            name="ck_screening_runs_verdict_vocabulary",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_screening_runs_confidence_bounded",
        ),
        sa.CheckConstraint(
            "run_status <> 'OK' OR latency_ms IS NOT NULL",
            name="ck_screening_runs_ok_requires_latency",
        ),
        sa.CheckConstraint(
            "btrim(provider) <> '' AND btrim(model) <> ''",
            name="ck_screening_runs_provenance_nonblank",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_screening_runs_latency_nonneg",
        ),
        sa.UniqueConstraint("farm_id", "id", name="uq_screening_runs_farm_id_id"),
    )
    op.create_index("ix_screening_runs_image_created", "screening_runs", ["image_id", "created_at"])

    op.create_table(
        "screening_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("region", sa.String(length=40), nullable=True),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "run_id"],
            ["screening_runs.farm_id", "screening_runs.id"],
            name="fk_screening_findings_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_REVIEW', 'CONFIRMED', 'REJECTED')",
            name="ck_screening_findings_status",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_screening_findings_confidence_bounded",
        ),
        sa.CheckConstraint(
            "btrim(label) <> '' AND (note IS NULL OR btrim(note) <> '')",
            name="ck_screening_findings_text_nonblank",
        ),
        sa.UniqueConstraint("farm_id", "id", name="uq_screening_findings_farm_id_id"),
    )
    op.create_index(
        "ix_screening_findings_farm_status_created",
        "screening_findings",
        ["farm_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("screening_findings")
    op.drop_table("screening_runs")
    op.drop_table("screening_images")
