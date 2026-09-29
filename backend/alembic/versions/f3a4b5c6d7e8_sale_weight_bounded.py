"""bound sale_weight_kg like every sibling numeric (2026-09-28 audit, D5)

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d8
Create Date: 2026-09-28 00:00:00.000000+00:00

``ck_animals_sale_weight_positive`` was only ``sale_weight_kg > 0`` while
every sibling weight CHECK carries an upper bound and the NaN/Infinity text
guard (``ck_animals_birth_weight_bounded``,
``ck_weight_records_weight_bounded``): ``Infinity > 0`` is true in
PostgreSQL, so direct SQL could store a non-finite or absurd sale weight and
poison the ₹/kg benchmarking that divides by it. The API was already safe;
the database layer now matches, with the siblings' exact byte shape.

NOT VALID -> VALIDATE swap, following b7c1d5e9f3a2/c3d4e5f6a7b8: the catalog
swap takes a brief ACCESS EXCLUSIVE, then validation scans under SHARE
UPDATE EXCLUSIVE. Validation is fail-closed inside this transaction — a
pre-existing row above 1000 kg (reachable only via direct SQL) aborts the
upgrade for operator review rather than slipping under the new CHECK.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "e2f3a4b5c6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_animals_sale_weight_positive"

# Keep in step with app/models/animals.py; mirrors the bounded sibling
# CHECKs (ck_animals_birth_weight_bounded, ck_weight_records_weight_bounded).
LEGACY_SQL = "sale_weight_kg IS NULL OR sale_weight_kg > 0"
BOUNDED_SQL = (
    "sale_weight_kg IS NULL OR "
    "(sale_weight_kg > 0 AND sale_weight_kg <= 1000 AND "
    "sale_weight_kg::text NOT IN ('NaN', 'Infinity', '-Infinity'))"
)


def _swap_sale_weight_check(sqltext: str) -> None:
    op.drop_constraint(CONSTRAINT_NAME, "animals", type_="check")
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "animals",
        sqltext,
        postgresql_not_valid=True,
    )
    op.execute(f'ALTER TABLE "animals" VALIDATE CONSTRAINT "{CONSTRAINT_NAME}"')


def upgrade() -> None:
    _swap_sale_weight_check(BOUNDED_SQL)


def downgrade() -> None:
    # Every row that passes the bounded CHECK also passes the weaker legacy
    # one, so the downgrade validation cannot fail on data.
    _swap_sale_weight_check(LEGACY_SQL)
