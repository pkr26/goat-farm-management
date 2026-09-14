"""insurance register (insurance_policies)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-14 00:00:00.000000+00:00

The husbandry-standards release adds a farm-scoped insurance register:
one row per policy, optionally linked to the covered animal through the
tenant composite FK pattern used by transactions/health_events
(farm_id + animal_id -> animals.farm_id + animals.id), with the
(farm_id, id) candidate key that pattern requires. The register is
append-style like the ledger — rows move forward through renewal and end
as lapsed/claimed — so the table carries no UPDATE/DELETE audit machinery;
renewal rewrites only the horizon (renewal_date/premium/status) by design.

CHECK constraints mirror models/finance.py verbatim (the vocabulary list
is single-sourced there via sql_in_values); they are created VALIDATED —
the table is new and empty, so there is nothing to backfill and no lock
worth avoiding. The (farm_id, status, renewal_date) index serves the
register's most urgent-first listing and the dashboard expiry window;
animal_id is indexed for the per-animal lifetime P&L join.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in step with app.models.finance (INSURANCE_POLICY_STATUSES order); the
# rendered bytes are pinned by tests/test_domain_check_constraints.py.
INSURANCE_STATUSES = ("active", "renewed", "lapsed", "claimed")


def upgrade() -> None:
    op.create_table(
        "insurance_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("animal_id", sa.Integer(), nullable=True),
        sa.Column("policy_number", sa.String(length=60), nullable=False),
        sa.Column("insurer", sa.String(length=120), nullable=False),
        sa.Column("sum_insured", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("premium", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("renewal_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sum_insured > 0 AND sum_insured <= 1000000000",
            name="ck_insurance_policies_sum_insured",
        ),
        sa.CheckConstraint(
            "premium >= 0 AND premium <= 1000000000",
            name="ck_insurance_policies_premium",
        ),
        sa.CheckConstraint(
            "renewal_date >= start_date",
            name="ck_insurance_policies_renewal_after_start",
        ),
        sa.CheckConstraint(
            f"status IN ({', '.join(f"'{value}'" for value in INSURANCE_STATUSES)})",
            name="ck_insurance_policies_status",
        ),
        sa.CheckConstraint(
            "btrim(policy_number) <> ''",
            name="ck_insurance_policies_policy_number_nonblank",
        ),
        sa.CheckConstraint(
            "btrim(insurer) <> ''",
            name="ck_insurance_policies_insurer_nonblank",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["animal_id"], ["animals.id"]),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_insurance_policies_farm_animal",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("farm_id", "id", name="uq_insurance_policies_farm_id_id"),
        sa.UniqueConstraint(
            "farm_id",
            "policy_number",
            name="uq_insurance_policies_farm_policy_number",
        ),
    )
    op.create_index(
        "ix_insurance_policies_farm_id",
        "insurance_policies",
        ["farm_id"],
    )
    op.create_index(
        "ix_insurance_policies_animal_id",
        "insurance_policies",
        ["animal_id"],
    )
    op.create_index(
        "ix_insurance_policies_farm_status_renewal",
        "insurance_policies",
        ["farm_id", "status", "renewal_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_insurance_policies_farm_status_renewal",
        table_name="insurance_policies",
    )
    op.drop_index(
        "ix_insurance_policies_animal_id",
        table_name="insurance_policies",
    )
    op.drop_index(
        "ix_insurance_policies_farm_id",
        table_name="insurance_policies",
    )
    op.drop_table("insurance_policies")
