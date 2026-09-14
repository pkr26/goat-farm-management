"""sale capture (weight/buyer economics) and coded death audit fields

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-14 00:00:00.000000+00:00

The husbandry-standards release upgrades the two terminal status flows:

- SOLD: ``sale_weight_kg`` (Numeric(8,2), CHECK > 0) persists the live weight
  at sale so realized ₹/kg can be benchmarked against mandi rates; the price
  derivation itself (weight × rate) runs in the endpoint with the money
  helpers and only the resulting paise-exact ``sale_price`` is stored.
- DEAD: ``mortality_cause_code`` (bounded to the MortalityCause vocabulary,
  wave-0 enum), ``disposal_method``, ``necropsy_done`` (default false) and
  ``necropsy_findings`` record the coded cause, carcass disposal and optional
  post-mortem beside the legacy free-text ``mortality_cause``.

All new columns are additive/nullable (``necropsy_done`` backfills false via
a server default), so every existing row satisfies the new CHECKs trivially.
The CHECKs follow the f1e2d3c4b5a6 pattern — created NOT VALID (brief
ACCESS EXCLUSIVE for the catalog add only), then VALIDATE CONSTRAINT under
SHARE UPDATE EXCLUSIVE — so reads/writes on the shared animals table never
stall behind a full-scan lock. Validation cannot fail on existing data.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in step with app.models.enums.MortalityCause member order; the model
# renders the same list via sql_in_values and pins these bytes.
_MORTALITY_CAUSES = (
    "PNEUMONIA",
    "DIARRHOEA",
    "COLIBACILLOSIS",
    "ENTEROTOXAEMIA",
    "PPR_SUSPECTED",
    "FMD_SUSPECTED",
    "GOAT_POX_SUSPECTED",
    "PARASITISM",
    "COCCIDIOSIS",
    "NUTRITIONAL",
    "HEAT_STRESS",
    "PREDATION",
    "ACCIDENT",
    "DYSTOCIA",
    "OLD_AGE",
    "OTHER",
    "UNKNOWN",
)
_MORTALITY_IN_LIST = ", ".join(f"'{value}'" for value in _MORTALITY_CAUSES)

# Mirrors app/models/animals.py __table_args__ (ck_animals_*).
_NEW_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "ck_animals_sale_weight_positive",
        "sale_weight_kg IS NULL OR sale_weight_kg > 0",
    ),
    (
        "ck_animals_death_audit_fields",
        "status = 'DEAD' OR (mortality_cause_code IS NULL AND disposal_method IS NULL "
        "AND necropsy_findings IS NULL)",
    ),
    (
        "ck_animals_mortality_cause_code",
        f"mortality_cause_code IS NULL OR mortality_cause_code IN ({_MORTALITY_IN_LIST})",
    ),
)


def _add_check(name: str, expression: str) -> None:
    op.create_check_constraint(
        name,
        "animals",
        expression,
        postgresql_not_valid=True,
    )
    op.execute(sa.text(f'ALTER TABLE animals VALIDATE CONSTRAINT "{name}"'))


def upgrade() -> None:
    op.add_column("animals", sa.Column("sale_weight_kg", sa.Numeric(8, 2), nullable=True))
    op.add_column("animals", sa.Column("mortality_cause_code", sa.String(30), nullable=True))
    op.add_column("animals", sa.Column("disposal_method", sa.String(60), nullable=True))
    op.add_column(
        "animals",
        sa.Column(
            "necropsy_done",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("animals", sa.Column("necropsy_findings", sa.Text(), nullable=True))
    for name, expression in _NEW_CHECKS:
        _add_check(name, expression)


def downgrade() -> None:
    # Symmetric: the new columns are nullable (or a constant false), so no
    # row can hold data the downgrade would strand — dropping them and their
    # CHECKs restores the previous shape exactly.
    for name, _expression in reversed(_NEW_CHECKS):
        op.drop_constraint(name, "animals", type_="check")
    op.drop_column("animals", "necropsy_findings")
    op.drop_column("animals", "necropsy_done")
    op.drop_column("animals", "disposal_method")
    op.drop_column("animals", "mortality_cause_code")
    op.drop_column("animals", "sale_weight_kg")
