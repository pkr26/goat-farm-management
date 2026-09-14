"""purchase-batch provenance: origin market, transit time, seller history

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-14 00:00:00.000000+00:00

The husbandry-standards release frames every quarantine batch with the
provenance facts a producer actually records at purchase time: which
market/shed the animals came from, how long they were in transit before
arrival (arrival stress and disease-exposure risk), and what
vaccinations/deworming the seller reported at source.

All three columns are additive and nullable, so the upgrade is a pure
schema widening with no data migration. The one integrity rule — transit
time inside 0–240 hours — follows the house pattern for CHECKs on shared
tables (d2e3f4a5b6c7): create the constraint NOT VALID so the catalog
swaps under a brief ACCESS EXCLUSIVE lock only, then VALIDATE CONSTRAINT
under SHARE UPDATE EXCLUSIVE. Validation cannot fail: the column is new,
so every existing row is NULL.

The downgrade is symmetric: drop the constraint, then the columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "purchase_batches",
        sa.Column("origin_market", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "purchase_batches",
        sa.Column("transport_hours", sa.Integer(), nullable=True),
    )
    op.add_column(
        "purchase_batches",
        sa.Column("seller_health_history", sa.Text(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE purchase_batches
            ADD CONSTRAINT ck_purchase_batches_transport_hours
            CHECK (transport_hours IS NULL OR transport_hours BETWEEN 0 AND 240) NOT VALID
            """
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE purchase_batches VALIDATE CONSTRAINT ck_purchase_batches_transport_hours"
        )
    )


def downgrade() -> None:
    op.drop_constraint("ck_purchase_batches_transport_hours", "purchase_batches", type_="check")
    op.drop_column("purchase_batches", "seller_health_history")
    op.drop_column("purchase_batches", "transport_hours")
    op.drop_column("purchase_batches", "origin_market")
