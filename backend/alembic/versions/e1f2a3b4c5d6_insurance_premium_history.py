"""insurance premium payment history (insurance_premiums)

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-09-14 00:00:00.000000+00:00

insurance_policies.premium is only the CURRENT period's price: renewal
overwrites it (a policy is one row, not a history), so the per-animal
lifetime P&L summing that column under-reports every renewed policy (a
450->500 renewal reads as 500 paid, not 950). This table is the register's
payment audit ledger: one row per premium booked at registration and at
every renewal, carrying the covered period (covered_from/covered_until).

Existing installations backfill one row per never-renewed policy (its
original premium covering start_date -> renewal_date): the policy column
value IS that payment, so the P&L sum is preserved exactly. Policies whose
premium column was already overwritten by a renewal before this release
cannot be reconstructed and simply keep their single backfill row — the
under-count for those ends here.

CHECK constraints mirror models/finance.py verbatim; the table is new, so
they are created VALIDATED. The (farm_id, policy_id) index serves the
lifetime-P&L sum's policy subquery.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "insurance_premiums",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=False),
        sa.Column("premium", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("covered_from", sa.Date(), nullable=False),
        sa.Column("covered_until", sa.Date(), nullable=False),
        sa.Column("recorded_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "premium >= 0 AND premium <= 1000000000",
            name="ck_insurance_premiums_premium",
        ),
        sa.CheckConstraint(
            "covered_until > covered_from",
            name="ck_insurance_premiums_covered_period",
        ),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["policy_id"], ["insurance_policies.id"]),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(
            ["farm_id", "policy_id"],
            ["insurance_policies.farm_id", "insurance_policies.id"],
            name="fk_insurance_premiums_farm_policy",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("farm_id", "id", name="uq_insurance_premiums_farm_id_id"),
    )
    op.create_index(
        "ix_insurance_premiums_farm_id",
        "insurance_premiums",
        ["farm_id"],
    )
    op.create_index(
        "ix_insurance_premiums_farm_policy",
        "insurance_premiums",
        ["farm_id", "policy_id"],
    )
    # One backfill row per existing policy whose covered period is a real
    # span (renewal_date > start_date): the column value is that payment.
    op.execute(
        """
        INSERT INTO insurance_premiums
            (farm_id, policy_id, premium, covered_from, covered_until, recorded_by_id)
        SELECT p.farm_id, p.id, p.premium, p.start_date, p.renewal_date, p.created_by_id
        FROM insurance_policies p
        WHERE p.renewal_date > p.start_date
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_insurance_premiums_farm_policy",
        table_name="insurance_premiums",
    )
    op.drop_index(
        "ix_insurance_premiums_farm_id",
        table_name="insurance_premiums",
    )
    op.drop_table("insurance_premiums")
