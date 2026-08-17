"""allow culled animals to retain sale proceeds

Revision ID: e6f8a0b2c4d7
Revises: d3b5f7c9e024
Create Date: 2026-08-17 00:00:00.000000+00:00

``cull_candidate`` is a live-herd worklist flag and is correctly cleared when
an animal leaves the herd.  Cull-price calibration therefore needs the
terminal ``CULLED`` status, rather than that transient flag, to identify a
cull disposal.  Permit an optional price/buyer on CULLED rows while retaining
the same protections for ACTIVE and DEAD animals.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text

from alembic import context, op

revision: str = "e6f8a0b2c4d7"
down_revision: str | Sequence[str] | None = "d3b5f7c9e024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_animals_sale_fields"
_SWAP_CONSTRAINT = "ck_animals_sale_fields_monetized_cull"
_CULLED_SALE_FIELDS = "status IN ('SOLD', 'CULLED') OR (sale_price IS NULL AND buyer_name IS NULL)"
_SOLD_ONLY_SALE_FIELDS = "status = 'SOLD' OR (sale_price IS NULL AND buyer_name IS NULL)"


def _replace_constraint(expression: str) -> None:
    # Build and validate the replacement while the prior, stricter constraint
    # still protects every row. VALIDATE takes a weaker lock than adding a
    # fully validated CHECK directly; only the final drop/rename needs the
    # brief metadata lock.
    op.create_check_constraint(
        _SWAP_CONSTRAINT,
        "animals",
        expression,
        postgresql_not_valid=True,
    )
    op.execute(sa.text(f'ALTER TABLE animals VALIDATE CONSTRAINT "{_SWAP_CONSTRAINT}"'))
    op.drop_constraint(_CONSTRAINT, "animals", type_="check")
    op.execute(
        sa.text(f'ALTER TABLE animals RENAME CONSTRAINT "{_SWAP_CONSTRAINT}" TO "{_CONSTRAINT}"')
    )


def upgrade() -> None:
    _replace_constraint(_CULLED_SALE_FIELDS)


def downgrade() -> None:
    incompatible_sql = (
        "SELECT id FROM animals WHERE status = 'CULLED' "
        "AND (sale_price IS NOT NULL OR buyer_name IS NOT NULL) ORDER BY id LIMIT 1"
    )
    if context.is_offline_mode():
        op.execute(
            "DO $$ DECLARE incompatible_id bigint; BEGIN "
            f"SELECT id INTO incompatible_id FROM ({incompatible_sql}) AS incompatible; "
            "IF incompatible_id IS NOT NULL THEN "
            "RAISE EXCEPTION 'Cannot downgrade monetized culls while CULLED animal % "
            "retains sale price or buyer data', incompatible_id; "
            "END IF; END $$"
        )
    else:
        incompatible_id = op.get_bind().execute(text(incompatible_sql)).scalar_one_or_none()
        if incompatible_id is not None:
            raise RuntimeError(
                "Cannot downgrade monetized culls while CULLED animal "
                f"{incompatible_id} retains sale price or buyer data"
            )

    _replace_constraint(_SOLD_ONLY_SALE_FIELDS)
