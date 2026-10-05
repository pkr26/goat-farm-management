"""Enforce effective birth before purchase without rewriting historical facts.

Revision ID: ff6a7b8c9d01
Revises: fe5f6a7b8c9d
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ff6a7b8c9d01"
down_revision: str | Sequence[str] | None = "fe5f6a7b8c9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Validation fails transactionally if contradictory legacy data exists.
    # Operators must reconcile it from records; migration must not invent DOBs.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM animals
                WHERE purchase_date < COALESCE(date_of_birth, estimated_dob)
            ) THEN
                RAISE EXCEPTION 'Animal purchase dates predate effective birth dates; '
                    'reconcile recorded date_of_birth/estimated_dob/purchase_date '
                    'from source records before upgrading';
            END IF;
        END $$
    """)
    op.create_check_constraint(
        "ck_animals_purchase_after_birth",
        "animals",
        "purchase_date IS NULL OR COALESCE(date_of_birth, estimated_dob) IS NULL "
        "OR purchase_date >= COALESCE(date_of_birth, estimated_dob)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_animals_purchase_after_birth", "animals", type_="check")
