"""Preserve audit attribution with de-identified account tombstones.

Revision ID: d4e5f6a7b8c9
Revises: d3f4a5b6c7d8
Create Date: 2026-08-09 07:00:00.000000+00:00

The application no longer physically deletes non-owner users. It revokes
sessions, scrubs profile/login material, and retains a pseudonymous parent row
plus non-authorizing membership anchors for immutable operational attribution.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "d3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_check_constraint(
        "ck_users_deleted_profile_scrubbed",
        "users",
        "deleted_at IS NULL OR (name IS NULL AND email LIKE 'deleted-%@deleted.invalid')",
    )

    # Pregnancy-loss attribution is immutable farm history. A future physical
    # deletion bug must fail closed instead of silently nulling its actor.
    op.drop_constraint(
        "fk_breeding_records_loss_recorded_by",
        "breeding_records",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_breeding_records_loss_recorded_by",
        "breeding_records",
        "users",
        ["loss_recorded_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Older application versions have no tombstone guard. Refuse to make a
    # deleted identity look active again; operators must reconcile or purge it
    # deliberately before downgrading.
    bind = op.get_bind()
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM users WHERE deleted_at IS NOT NULL)")
    ).scalar_one():
        raise RuntimeError(
            "Cannot downgrade account tombstones while deleted users exist; "
            "reconcile those identities before running an older application."
        )

    op.drop_constraint(
        "fk_breeding_records_loss_recorded_by",
        "breeding_records",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_breeding_records_loss_recorded_by",
        "breeding_records",
        "users",
        ["loss_recorded_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint("ck_users_deleted_profile_scrubbed", "users", type_="check")
    op.drop_column("users", "deleted_at")
