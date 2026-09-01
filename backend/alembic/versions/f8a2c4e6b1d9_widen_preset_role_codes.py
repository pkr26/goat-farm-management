"""widen the preset role-code vocabulary

Revision ID: f8a2c4e6b1d9
Revises: c5a8e1f3b7d2
Create Date: 2026-08-31 00:00:00.000000+00:00

New farm-role presets (Farm Manager, Procurement Officer, Milking Attendant,
Milk Quality Supervisor, Calf-shed Attendant, Accountant, Auditor) extend the
server-owned ``roles.code`` vocabulary. ``ck_roles_preset_code`` still lists
the previous five codes verbatim, so seeding any new preset would violate it
on every existing installation. Widen the CHECK to the new full list.

Both directions follow d5e7f9a1b3c4's pattern: add the constraint NOT VALID
(brief ACCESS EXCLUSIVE for the catalog swap only), then VALIDATE CONSTRAINT
under SHARE UPDATE EXCLUSIVE so reads/writes on the shared ``roles`` table
never stall behind a full-scan lock. Validation cannot fail on data — the
legacy five-code constraint guarantees no new codes exist before the upgrade,
and the downgrade normalizes them away first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f8a2c4e6b1d9"
down_revision: str | Sequence[str] | None = "c5a8e1f3b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK_NAME = "ck_roles_preset_code"
# Keep in step with app.permissions.ROLE_PRESET_CODES (sorted).
PRESET_CODES = (
    "ACCOUNTANT",
    "BUYER",
    "CALF_ATTENDANT",
    "CLEANER",
    "CLEANER_MANAGER",
    "FEEDER",
    "MANAGER",
    "MILKER",
    "MILK_QC",
    "MOVER",
    "VET",
    "VIEWER",
)
PRESET_CODES_SQL = ", ".join(f"'{code}'" for code in PRESET_CODES)
LEGACY_CODES_SQL = ", ".join(
    f"'{code}'" for code in ("CLEANER", "CLEANER_MANAGER", "FEEDER", "MOVER", "VET")
)


def _swap_constraint(codes_sql: str) -> None:
    op.drop_constraint(CHECK_NAME, "roles", type_="check")
    op.create_check_constraint(
        CHECK_NAME,
        "roles",
        f"code IS NULL OR code IN ({codes_sql})",
        postgresql_not_valid=True,
    )
    op.execute(f"ALTER TABLE roles VALIDATE CONSTRAINT {CHECK_NAME}")


def upgrade() -> None:
    _swap_constraint(PRESET_CODES_SQL)


def downgrade() -> None:
    # The application release that routes duties to the new presets is gone
    # with this downgrade, so those rows become ordinary custom roles
    # (code = NULL) — the d5e7f9a1b3c4 normalization pattern. Role ids,
    # memberships and every historical duty reference survive intact.
    op.execute(
        f"UPDATE roles SET code = NULL "
        f"WHERE code IS NOT NULL AND code NOT IN ({LEGACY_CODES_SQL})"
    )
    _swap_constraint(LEGACY_CODES_SQL)
