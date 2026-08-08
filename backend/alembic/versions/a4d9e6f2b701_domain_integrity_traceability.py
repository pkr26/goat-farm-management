"""Domain-integrity holds and generic animal-health traceability.

Revision ID: a4d9e6f2b701
Revises: a7c9e2f4b1d8
Create Date: 2026-08-08 18:30:00.000000+00:00

This migration deliberately records facts (holds, notifications, product and
certificate details) without encoding veterinary diagnosis or treatment.
Existing rows remain valid: all provenance fields are nullable and new hold
flags default to false.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4d9e6f2b701"
down_revision: str | Sequence[str] | None = "a7c9e2f4b1d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "animals",
        sa.Column("movement_restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("animals", sa.Column("restriction_reason", sa.String(length=255), nullable=True))
    op.add_column(
        "animals",
        sa.Column(
            "suspected_scheduled_disease", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("animals", sa.Column("suspected_disease", sa.String(length=120), nullable=True))
    op.add_column("animals", sa.Column("authority_notified_at", sa.Date(), nullable=True))
    op.add_column("animals", sa.Column("mortality_cause", sa.String(length=120), nullable=True))
    op.add_column("animals", sa.Column("mortality_reported_at", sa.Date(), nullable=True))
    op.alter_column("animals", "movement_restricted", server_default=None)
    op.alter_column("animals", "suspected_scheduled_disease", server_default=None)

    op.add_column("breeding_records", sa.Column("ultrasound_result_date", sa.Date(), nullable=True))
    op.add_column("kid_entries", sa.Column("mortality_reported_at", sa.Date(), nullable=True))

    op.add_column(
        "health_events", sa.Column("schedule_template_name", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "health_events", sa.Column("next_due_authority", sa.String(length=120), nullable=True)
    )
    op.add_column("health_events", sa.Column("product_lot", sa.String(length=120), nullable=True))
    op.add_column("health_events", sa.Column("product_manufactured_on", sa.Date(), nullable=True))
    op.add_column("health_events", sa.Column("product_expires_on", sa.Date(), nullable=True))
    op.add_column("health_events", sa.Column("vaccine_valid_until", sa.Date(), nullable=True))
    op.add_column(
        "health_events", sa.Column("certificate_number", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "health_events", sa.Column("official_tag_number", sa.String(length=80), nullable=True)
    )
    op.add_column(
        "health_events", sa.Column("administered_by", sa.String(length=120), nullable=True)
    )
    op.add_column("health_events", sa.Column("withdrawal_until", sa.Date(), nullable=True))
    op.add_column(
        "health_events",
        sa.Column(
            "suspected_scheduled_disease", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("health_events", sa.Column("authority_notified_at", sa.Date(), nullable=True))
    op.add_column("health_events", sa.Column("isolation_started_at", sa.Date(), nullable=True))
    op.alter_column("health_events", "suspected_scheduled_disease", server_default=None)


def downgrade() -> None:
    for name in (
        "isolation_started_at",
        "authority_notified_at",
        "suspected_scheduled_disease",
        "withdrawal_until",
        "administered_by",
        "official_tag_number",
        "certificate_number",
        "vaccine_valid_until",
        "product_expires_on",
        "product_manufactured_on",
        "product_lot",
        "next_due_authority",
        "schedule_template_name",
    ):
        op.drop_column("health_events", name)
    op.drop_column("kid_entries", "mortality_reported_at")
    op.drop_column("breeding_records", "ultrasound_result_date")
    for name in (
        "mortality_reported_at",
        "mortality_cause",
        "authority_notified_at",
        "suspected_disease",
        "suspected_scheduled_disease",
        "restriction_reason",
        "movement_restricted",
    ):
        op.drop_column("animals", name)
