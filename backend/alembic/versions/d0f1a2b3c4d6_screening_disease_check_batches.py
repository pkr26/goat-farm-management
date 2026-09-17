"""screening upload flow: disease-check batches + bucket-tagged photos

The "disease check" walkthrough: a worker creates a batch, photographs each
herd bucket (presigned PUT straight to S3 under
``raw/<farm>/<date>/<bucket>/<file>``), then submits the batch. Photos
pre-create PENDING ``screening_images`` rows (bucket + batch recorded at
upload time); the worker claims PENDING rows whose object exists. The key
parser learns the optional bucket segment — legacy
``raw/<farm>/<date>/<file>`` keys keep working with bucket NULL.

Revision ID: d0f1a2b3c4d6
Revises: c9e0f1a3b4d5
"""

import sqlalchemy as sa

from alembic import op

revision = "d0f1a2b3c4d6"
down_revision = "c9e0f1a3b4d5"
branch_labels = None
depends_on = None

_BUCKET_VOCAB = (
    "'QUARANTINE', 'FOUNDATION', 'BREEDING', 'PREGNANCY_EARLY', 'PREGNANCY_LATE', "
    "'DELIVERY', 'RECOVERY', 'RESTING', 'MALE_KIDS', 'FEMALE_KIDS'"
)


def upgrade() -> None:
    op.create_table(
        "screening_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False
        ),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "submitted_at IS NULL OR submitted_at >= created_at",
            name="ck_screening_batches_submit_after_create",
        ),
        sa.UniqueConstraint("farm_id", "id", name="uq_screening_batches_farm_id_id"),
    )
    op.create_index(
        "ix_screening_batches_farm_id", "screening_batches", ["farm_id"]
    )
    op.create_index(
        "ix_screening_batches_created_by_id", "screening_batches", ["created_by_id"]
    )
    op.create_index(
        "ix_screening_batches_farm_created",
        "screening_batches",
        ["farm_id", "created_at"],
    )

    op.add_column("screening_images", sa.Column("bucket", sa.String(30), nullable=True))
    op.add_column("screening_images", sa.Column("batch_id", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_screening_images_bucket_vocabulary",
        "screening_images",
        f"bucket IS NULL OR bucket IN ({_BUCKET_VOCAB})",
    )
    op.create_foreign_key(
        "fk_screening_images_batch",
        "screening_images",
        "screening_batches",
        ["farm_id", "batch_id"],
        ["farm_id", "id"],
    )
    op.create_index(
        "ix_screening_images_batch", "screening_images", ["farm_id", "batch_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_screening_images_batch", table_name="screening_images")
    op.drop_constraint("fk_screening_images_batch", "screening_images", type_="foreignkey")
    op.drop_constraint(
        "ck_screening_images_bucket_vocabulary", "screening_images", type_="check"
    )
    op.drop_column("screening_images", "batch_id")
    op.drop_column("screening_images", "bucket")
    op.drop_index("ix_screening_batches_farm_created", table_name="screening_batches")
    op.drop_index("ix_screening_batches_created_by_id", table_name="screening_batches")
    op.drop_index("ix_screening_batches_farm_id", table_name="screening_batches")
    op.drop_table("screening_batches")
