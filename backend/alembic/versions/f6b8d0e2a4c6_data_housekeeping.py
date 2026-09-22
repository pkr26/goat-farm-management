"""Data housekeeping (ITEM 9, 2026-09-21 playbook)

1. ``created_at`` (UTC server default) on HealthEvent, KiddingRecord,
   BreedingRecord, WeightRecord, FeedingRecord and KidEntry — closes the
   backdating blind spot where a backdated entry was indistinguishable from
   a same-day one (insurance/withdrawal evidence). Existing rows backfill to
   the migration's transaction timestamp; from here on entry time is a fact.
2. Screening fact-table PKs int4 → bigint while the tables are small:
   per-photo fact rows (runs, crops, findings) are the system's
   highest-velocity sequence and the int4 ceiling is a live constraint, not
   a theoretical one.
"""

import sqlalchemy as sa

from alembic import op

revision = "f6b8d0e2a4c6"
down_revision = "e5a7c9d1b3f5"
branch_labels = None
depends_on = None

# (table) — plain ADD COLUMN with a server default; no rewrite.
CREATED_AT_TABLES = (
    "health_events",
    "kidding_records",
    "breeding_records",
    "weight_records",
    "feeding_records",
    "kid_entries",
)

# (table) — int4 PK widened to bigint. Serial sequences must be retyped too,
# else they keep minting int4-max values against a bigint column.
BIGINT_PK_TABLES = (
    "screening_runs",
    "screening_crops",
    "screening_findings",
)


def upgrade() -> None:
    now_default = sa.text("timezone('UTC', now())")
    for table in CREATED_AT_TABLES:
        op.add_column(
            table,
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=now_default,
            ),
        )
    for table in BIGINT_PK_TABLES:
        op.alter_column(
            table,
            "id",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            postgresql_using="id::bigint",
        )
        op.execute(f"ALTER SEQUENCE IF EXISTS {table}_id_seq AS bigint")


def downgrade() -> None:
    for table in BIGINT_PK_TABLES:
        op.alter_column(
            table,
            "id",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            postgresql_using="id::integer",
        )
    for table in CREATED_AT_TABLES:
        op.drop_column(table, "created_at")
