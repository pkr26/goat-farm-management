"""Persist purchase-batch sex even when animal stubs are not created.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-09 05:00:00.000000+00:00

Legacy batches infer sex only when every linked animal agrees. Batches with no
animals or mixed-sex historical imports remain explicitly unknown (NULL); the
API stores M/F for every new batch.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "purchase_batches",
        sa.Column("sex", sa.String(length=1), nullable=True),
    )
    op.execute(
        sa.text(
            """
            WITH inferred AS (
                SELECT purchase_batch_id,
                       min(sex) AS min_sex,
                       max(sex) AS max_sex
                FROM animals
                WHERE purchase_batch_id IS NOT NULL
                GROUP BY purchase_batch_id
            )
            UPDATE purchase_batches AS batch
            SET sex = CASE
                WHEN inferred.min_sex = inferred.max_sex THEN inferred.min_sex
                ELSE NULL
            END
            FROM inferred
            WHERE inferred.purchase_batch_id = batch.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE purchase_batches
            ADD CONSTRAINT ck_purchase_batches_sex
            CHECK (sex IS NULL OR sex IN ('M', 'F')) NOT VALID
            """
        )
    )
    op.execute(sa.text("ALTER TABLE purchase_batches VALIDATE CONSTRAINT ck_purchase_batches_sex"))


def downgrade() -> None:
    op.drop_constraint("ck_purchase_batches_sex", "purchase_batches", type_="check")
    op.drop_column("purchase_batches", "sex")
