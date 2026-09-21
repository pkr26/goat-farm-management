"""drop the unproducible MULTIPLET birth type and dead renewed insurance status

Revision ID: cad1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-09-20 12:00:00.000000+00:00

Two vocabulary entries that no code path can ever produce leave the server
contracts (2026-09-20 audit P3, dead/misleading code):

- ``BirthType.MULTIPLET``: the kidding service rejects any litter above the
  species maximum (4 for goats — ``LitterSizeError``), so the
  ``delivered_count >= 5`` branch that would set it is unreachable. The
  schema Literal, the enum, the DB CHECK and the generated client all drop
  it.
- ``insurance_policies.status = 'renewed'``: renewal keeps a policy ACTIVE
  with a new horizon and appends a premium row (services/finance.py states
  this in a comment at the renewal site); no writer ever assigns 'renewed'.

Both CHECK swaps follow the chain's own short-lock idiom (create NOT VALID,
then VALIDATE) so the initial metadata lock does not wait on a full-table
scan, and each is preceded by a fail-closed preflight that names surviving
row ids instead of silently violating the new constraint mid-swap.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "cad1e2f3a4b5"
down_revision: str | Sequence[str] | None = "b9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preflight_unproducible_values() -> None:
    """Refuse to narrow a constraint that live rows still violate.

    Neither value is producible by any shipped writer, so the preflight
    passing is the normal case; the ids make a manual repair actionable if a
    database was ever edited out-of-band.
    """
    for table, column, value in (
        ("animals", "birth_type", "MULTIPLET"),
        ("insurance_policies", "status", "renewed"),
    ):
        rows = (
            op.get_bind()
            .execute(sa.text(f"SELECT id FROM {table} WHERE {column} = :value"), {"value": value})
            .scalars()
            .all()
        )
        if rows:
            raise RuntimeError(
                f"{table}.{column} still holds '{value}' on row(s) "
                f"{sorted(int(row) for row in rows)[:20]}; rewrite those rows "
                f"before applying this revision"
            )


def upgrade() -> None:
    _preflight_unproducible_values()

    op.drop_constraint("ck_animals_birth_type", "animals", type_="check")
    op.create_check_constraint(
        "ck_animals_birth_type",
        "animals",
        "birth_type IS NULL OR birth_type IN ('SINGLE', 'TWIN', 'TRIPLET', 'QUADRUPLET')",
        postgresql_not_valid=True,
    )
    op.execute("ALTER TABLE animals VALIDATE CONSTRAINT ck_animals_birth_type")

    op.drop_constraint("ck_insurance_policies_status", "insurance_policies", type_="check")
    op.create_check_constraint(
        "ck_insurance_policies_status",
        "insurance_policies",
        "status IN ('active', 'lapsed', 'claimed')",
        postgresql_not_valid=True,
    )
    op.execute("ALTER TABLE insurance_policies VALIDATE CONSTRAINT ck_insurance_policies_status")


def downgrade() -> None:
    """Restore both vocabulary entries (no row rewrite is needed: widening a
    CHECK constraint accepts every row the narrowed one accepted)."""
    op.drop_constraint("ck_insurance_policies_status", "insurance_policies", type_="check")
    op.create_check_constraint(
        "ck_insurance_policies_status",
        "insurance_policies",
        "status IN ('active', 'renewed', 'lapsed', 'claimed')",
        postgresql_not_valid=True,
    )
    op.execute("ALTER TABLE insurance_policies VALIDATE CONSTRAINT ck_insurance_policies_status")

    op.drop_constraint("ck_animals_birth_type", "animals", type_="check")
    op.create_check_constraint(
        "ck_animals_birth_type",
        "animals",
        "birth_type IS NULL OR birth_type IN "
        "('SINGLE', 'TWIN', 'TRIPLET', 'QUADRUPLET', 'MULTIPLET')",
        postgresql_not_valid=True,
    )
    op.execute("ALTER TABLE animals VALIDATE CONSTRAINT ck_animals_birth_type")
