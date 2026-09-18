"""screening phase 3: per-goat crops, detection stage

Multi-goat photos are split by a detection call into per-goat crops; each
crop runs the cascade independently (gate stops per goat, not per photo).
``screening_crops`` stores the detection box (0-1000 normalized, persisted
verbatim for the training export); ``screening_runs``/``screening_findings``
gain a nullable ``crop_id`` (null = whole-photo run / Phase ≤2 row). The
stage CHECK widens for DETECT.

Revision ID: c9e0f1a3b4d5
Revises: b8d9e0f2a3c4
"""

import sqlalchemy as sa

from alembic import op

revision = "c9e0f1a3b4d5"
down_revision = "b8d9e0f2a3c4"
branch_labels = None
depends_on = None

_STAGE_VOCAB = (
    "'DETECT', 'GATE', 'CROSS_CHECK', 'SPECIALIST_SKIN', 'SPECIALIST_EYE', "
    "'SPECIALIST_HOOF', 'SPECIALIST_UDDER', 'SPECIALIST_GENERAL'"
)


def upgrade() -> None:
    op.create_table(
        "screening_crops",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=False),
        sa.Column("crop_index", sa.Integer(), nullable=False),
        sa.Column("box_x", sa.Integer(), nullable=False),
        sa.Column("box_y", sa.Integer(), nullable=False),
        sa.Column("box_w", sa.Integer(), nullable=False),
        sa.Column("box_h", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("normalized_key", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "image_id"],
            ["screening_images.farm_id", "screening_images.id"],
            name="fk_screening_crops_image",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'HEALTHY', 'FLAGGED', 'SKIPPED', 'ERROR')",
            name="ck_screening_crops_status",
        ),
        sa.CheckConstraint(
            "box_x >= 0 AND box_x <= 1000 AND box_y >= 0 AND box_y <= 1000 "
            "AND box_w > 0 AND box_w <= 1000 AND box_h > 0 AND box_h <= 1000",
            name="ck_screening_crops_box_bounds",
        ),
        sa.CheckConstraint(
            "status <> 'ERROR' OR (error IS NOT NULL AND btrim(error) <> '')",
            name="ck_screening_crops_error_requires_error_status",
        ),
        sa.UniqueConstraint("farm_id", "id", name="uq_screening_crops_farm_id_id"),
        sa.UniqueConstraint(
            "farm_id", "image_id", "crop_index", name="uq_screening_crops_image_index"
        ),
    )
    op.create_index("ix_screening_crops_farm_image", "screening_crops", ["farm_id", "image_id"])

    op.add_column("screening_runs", sa.Column("crop_id", sa.Integer(), nullable=True))
    op.add_column("screening_findings", sa.Column("crop_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_screening_runs_crop",
        "screening_runs",
        "screening_crops",
        ["farm_id", "crop_id"],
        ["farm_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_screening_findings_crop",
        "screening_findings",
        "screening_crops",
        ["farm_id", "crop_id"],
        ["farm_id", "id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("ck_screening_runs_stage", "screening_runs", type_="check")
    op.create_check_constraint(
        "ck_screening_runs_stage", "screening_runs", f"stage IN ({_STAGE_VOCAB})"
    )


def downgrade() -> None:
    op.drop_constraint("ck_screening_runs_stage", "screening_runs", type_="check")
    op.create_check_constraint(
        "ck_screening_runs_stage",
        "screening_runs",
        "stage IN ('GATE', 'CROSS_CHECK', 'SPECIALIST_SKIN', 'SPECIALIST_EYE', "
        "'SPECIALIST_HOOF', 'SPECIALIST_UDDER', 'SPECIALIST_GENERAL')",
    )
    op.drop_constraint("fk_screening_findings_crop", "screening_findings", type_="foreignkey")
    op.drop_constraint("fk_screening_runs_crop", "screening_runs", type_="foreignkey")
    op.drop_column("screening_findings", "crop_id")
    op.drop_column("screening_runs", "crop_id")
    op.drop_table("screening_crops")
