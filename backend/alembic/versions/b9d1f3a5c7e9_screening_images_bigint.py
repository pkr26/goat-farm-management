"""Screening images PK int4 → bigint (ITEM 9 completion, 2026-09-21)

``f6b8d0e2a4c6`` widened the three highest-velocity screening fact tables;
``screening_images`` is the fourth playbook-cited fact table (one row per
uploaded photo) and is widened the same way while the table is small.
"""

import sqlalchemy as sa

from alembic import op

revision = "b9d1f3a5c7e9"
down_revision = "a8c0e2f4b6d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "screening_images",
        "id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        postgresql_using="id::bigint",
    )
    op.execute("ALTER SEQUENCE IF EXISTS screening_images_id_seq AS bigint")


def downgrade() -> None:
    op.alter_column(
        "screening_images",
        "id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        postgresql_using="id::integer",
    )
