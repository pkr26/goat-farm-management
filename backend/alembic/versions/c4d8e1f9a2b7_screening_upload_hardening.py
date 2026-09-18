"""bind screening uploads to registrations and bounded retry scheduling

Revision ID: c4d8e1f9a2b7
Revises: c3e5a9f1d7b4
Create Date: 2026-09-17 16:20:00.000000+00:00

New browser uploads are pre-registered before a constrained S3 POST form is
issued.  The declared content type and random token are persisted so the worker
can verify the object's HEAD metadata before decoding it.  Both remain nullable
for rows created by historical versions; those rows are already registered and
can drain safely, but raw-prefix discovery no longer creates new records.

``next_attempt_at`` prevents not-yet-uploaded objects from consuming every
worker cycle while their short-lived form is still valid.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d8e1f9a2b7"
down_revision: str | Sequence[str] | None = "c3e5a9f1d7b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "screening_images",
        sa.Column("upload_content_type", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "screening_images",
        sa.Column("upload_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "screening_images",
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
    )
    op.create_check_constraint(
        "ck_screening_images_upload_content_type",
        "screening_images",
        "upload_content_type IS NULL OR upload_content_type IN ('image/jpeg', 'image/png')",
    )
    op.create_check_constraint(
        "ck_screening_images_upload_registration_pair",
        "screening_images",
        "(upload_content_type IS NULL) = (upload_token IS NULL)",
    )
    op.create_check_constraint(
        "ck_screening_images_upload_token_nontrivial",
        "screening_images",
        "upload_token IS NULL OR length(upload_token) >= 32",
    )
    # Supports PENDING retry windows and the farm-partitioned fair claim
    # scan without changing the existing review-list index.
    op.create_index(
        "ix_screening_images_claim_fairness",
        "screening_images",
        ["status", "next_attempt_at", "farm_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_screening_images_claim_fairness", table_name="screening_images")
    op.drop_constraint(
        "ck_screening_images_upload_token_nontrivial",
        "screening_images",
        type_="check",
    )
    op.drop_constraint(
        "ck_screening_images_upload_registration_pair",
        "screening_images",
        type_="check",
    )
    op.drop_constraint(
        "ck_screening_images_upload_content_type",
        "screening_images",
        type_="check",
    )
    op.drop_column("screening_images", "next_attempt_at")
    op.drop_column("screening_images", "upload_token")
    op.drop_column("screening_images", "upload_content_type")
