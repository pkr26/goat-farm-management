"""insurance audit facts, premium accounting date and plan anchor repair

Revision ID: a6d4e2f9c8b7
Revises: c3e5a9f1d7b4
Create Date: 2026-09-17 00:00:00.000000+00:00

The insurance register previously accepted a claim date but discarded it,
and premium rows had no farm-local accounting date.  That made an immutable
per-animal payment history invisible to the farm's financial periods and made
terminal claims impossible to attribute.  This revision adds the missing
facts, backfills premium dates from their UTC creation instants in each farm's
timezone, and protects duplicate covered periods.

It also normalizes saved planner assumptions to the relational
``start_year_month`` anchor, which is the source of truth.  The prior mismatch
cannot be reconstructed as intent, so the repair deliberately chooses the
column already presented by the API rather than silently changing it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a6d4e2f9c8b7"
down_revision: str | Sequence[str] | None = "c3e5a9f1d7b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preflight_duplicate_premium_periods() -> None:
    """Fail closed rather than deleting or merging immutable payment facts."""
    op.execute(
        sa.text(
            """
            DO $insurance_premium_preflight$
            DECLARE
                duplicate_periods text;
            BEGIN
                SELECT string_agg(
                    farm_id::text || '/' || policy_id::text || ' [' ||
                    covered_from::text || ',' || covered_until::text || ']',
                    '; ' ORDER BY farm_id, policy_id, covered_from, covered_until
                )
                INTO duplicate_periods
                FROM (
                    SELECT farm_id, policy_id, covered_from, covered_until
                    FROM insurance_premiums
                    GROUP BY farm_id, policy_id, covered_from, covered_until
                    HAVING count(*) > 1
                    ORDER BY farm_id, policy_id, covered_from, covered_until
                    LIMIT 20
                ) AS duplicate_period;
                IF duplicate_periods IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot enforce one insurance premium per covered period: duplicate '
                        'farm/policy periods %. Insurance payments are immutable; reconcile '
                        'each duplicate explicitly before retrying.',
                        duplicate_periods;
                END IF;
            END
            $insurance_premium_preflight$
            """
        )
    )


def upgrade() -> None:
    _preflight_duplicate_premium_periods()

    op.add_column(
        "insurance_policies",
        sa.Column("claim_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "insurance_policies",
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "insurance_policies",
        sa.Column("claimed_by_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_insurance_policies_claimed_by",
        "insurance_policies",
        "users",
        ["claimed_by_id"],
        ["id"],
    )
    # Historical claimed policies predate attribution, so an all-null triple
    # remains valid.  Future writes must either record all three facts or none.
    op.create_check_constraint(
        "ck_insurance_policies_claim_metadata_state",
        "insurance_policies",
        "(status = 'claimed' AND "
        "((claim_date IS NULL AND claimed_at IS NULL AND claimed_by_id IS NULL) OR "
        "(claim_date IS NOT NULL AND claimed_at IS NOT NULL AND claimed_by_id IS NOT NULL))) "
        "OR (status <> 'claimed' AND claim_date IS NULL "
        "AND claimed_at IS NULL AND claimed_by_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_insurance_policies_claim_after_start",
        "insurance_policies",
        "claim_date IS NULL OR claim_date >= start_date",
    )

    op.add_column(
        "insurance_premiums",
        sa.Column("recorded_on", sa.Date(), nullable=True),
    )
    # created_at is a UTC timestamp stored without a timezone.  Interpret it
    # as UTC first, then project it into the owning farm's business timezone;
    # direct ``created_at::date`` would use the database server's calendar.
    op.execute(
        sa.text(
            """
            UPDATE insurance_premiums AS premium
            SET recorded_on = (
                (premium.created_at AT TIME ZONE 'UTC')
                AT TIME ZONE COALESCE(farm.timezone, 'UTC')
            )::date
            FROM farms AS farm
            WHERE farm.id = premium.farm_id
              AND premium.recorded_on IS NULL
            """
        )
    )
    op.alter_column(
        "insurance_premiums",
        "recorded_on",
        existing_type=sa.Date(),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_insurance_premiums_farm_policy_period",
        "insurance_premiums",
        ["farm_id", "policy_id", "covered_from", "covered_until"],
    )
    op.create_index(
        "ix_insurance_premiums_farm_recorded_on",
        "insurance_premiums",
        ["farm_id", "recorded_on"],
        unique=False,
    )

    # Repair the formerly possible split-brain state.  If a document lacks a
    # usable meta object it remains untouched and the application rejects it
    # as invalid; do not manufacture a partial assumptions document here.
    op.execute(
        sa.text(
            """
            UPDATE planner_plans
            SET assumptions = jsonb_set(
                assumptions,
                '{meta,start_year_month}',
                to_jsonb(start_year_month),
                true
            )
            WHERE jsonb_typeof(assumptions) = 'object'
              AND jsonb_typeof(assumptions -> 'meta') = 'object'
              AND assumptions #>> '{meta,start_year_month}' IS DISTINCT FROM start_year_month
            """
        )
    )


def downgrade() -> None:
    # The planner normalization intentionally has no inverse: the discarded
    # embedded anchor was contradictory data, not recoverable history.
    op.drop_index("ix_insurance_premiums_farm_recorded_on", table_name="insurance_premiums")
    op.drop_constraint(
        "uq_insurance_premiums_farm_policy_period",
        "insurance_premiums",
        type_="unique",
    )
    op.drop_column("insurance_premiums", "recorded_on")

    op.drop_constraint(
        "ck_insurance_policies_claim_after_start",
        "insurance_policies",
        type_="check",
    )
    op.drop_constraint(
        "ck_insurance_policies_claim_metadata_state",
        "insurance_policies",
        type_="check",
    )
    op.drop_constraint(
        "fk_insurance_policies_claimed_by",
        "insurance_policies",
        type_="foreignkey",
    )
    op.drop_column("insurance_policies", "claimed_by_id")
    op.drop_column("insurance_policies", "claimed_at")
    op.drop_column("insurance_policies", "claim_date")
