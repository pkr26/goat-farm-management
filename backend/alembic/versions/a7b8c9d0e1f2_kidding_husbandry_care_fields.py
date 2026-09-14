"""kidding parity + dam/kid postpartum care facts

Revision ID: a7b8c9d0e1f2
Revises: f1e2d3c4b5a6
Create Date: 2026-09-14 00:00:00.000000+00:00

The husbandry-standards release records the per-kidding care facts behind
the postpartum duty generation (record_kidding):

- ``kidding_records.parity`` — server-derived litter index (1 = first
  kidding), filled by the service; legacy rows stay NULL rather than being
  back-filled from possibly incomplete history;
- ``kidding_records.placenta_passed`` / ``mastitis_suspected`` — dam check
  facts (NULL = not recorded; mastitis flags default false);
- ``kid_entries.colostrum_within_2h`` / ``navel_dipped`` — neonatal care
  facts (NULL = not recorded), and ``dam_rejected`` defaulting false, which
  together drive the conditional kid-support duty.

All six are additive; the two NOT NULL booleans carry server_default false
so existing rows migrate untouched, and the downgrade is the symmetric
DROP COLUMN.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f1e2d3c4b5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("kidding_records", sa.Column("parity", sa.Integer(), nullable=True))
    op.add_column("kidding_records", sa.Column("placenta_passed", sa.Boolean(), nullable=True))
    op.add_column(
        "kidding_records",
        sa.Column(
            "mastitis_suspected", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column("kid_entries", sa.Column("colostrum_within_2h", sa.Boolean(), nullable=True))
    op.add_column("kid_entries", sa.Column("navel_dipped", sa.Boolean(), nullable=True))
    op.add_column(
        "kid_entries",
        sa.Column("dam_rejected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("kid_entries", "dam_rejected")
    op.drop_column("kid_entries", "navel_dipped")
    op.drop_column("kid_entries", "colostrum_within_2h")
    op.drop_column("kidding_records", "mastitis_suspected")
    op.drop_column("kidding_records", "placenta_passed")
    op.drop_column("kidding_records", "parity")
