"""the FAMACHA duty category (monthly anemia-scoring round)

Revision ID: c3d4e5f6a7b8
Revises: b9d1f3a5c7e9
Create Date: 2026-09-23 00:00:00.000000+00:00

2026-09-23 verification finding: the husbandry plan expects a monthly
FAMACHA (conjunctiva-color anemia) scoring round, but the vocabulary had
no such category — FAMACHA appeared only as a screening-specialist label.
The cadence sweep now generates the round (30-day lookback, assigned to
the VET preset role), which requires widening ``ck_tasks_category`` with
``FAMACHA``, following b7c1d5e9f3a2's NOT VALID -> VALIDATE swap (brief
ACCESS EXCLUSIVE for the catalog swap only, then SHARE UPDATE EXCLUSIVE
validation; validation cannot fail on data because the legacy CHECK
guarantees no FAMACHA row exists before the upgrade).

The literals are pinned byte-for-byte by tests/test_domain_check_constraints.py
against app/models/enums.py member order.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b9d1f3a5c7e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in step with app.models (enums.py member order); the rendered bytes
# are pinned by tests/test_domain_check_constraints.py.
TASK_CATEGORIES_LEGACY = (
    "VACCINE",
    "DEWORMING",
    "ULTRASOUND",
    "KIDDING_DUE",
    "WEANING",
    "BUCKET_MOVE",
    "QUARANTINE",
    "FEED",
    "CLEANING",
    "OTHER",
    "KIDDING_WATCH",
    "BIRTHING_KIT",
    "HEALTH_CHECK",
    "HEAT_WATCH",
    "HOOF_TRIMMING",
    "SPRAYING",
    "DISINFECTION",
    "WEIGHING",
    "REBREED",
    "BUCK_ROTATION",
    "INSURANCE",
    "WATER",
)
TASK_CATEGORIES_NEW = (*TASK_CATEGORIES_LEGACY, "FAMACHA")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _swap_constraint(name: str, table: str, column: str, values: tuple[str, ...]) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(
        name,
        table,
        f"{column} IN ({_in_list(values)})",
        postgresql_not_valid=True,
    )
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    _swap_constraint("ck_tasks_category", "tasks", "category", TASK_CATEGORIES_NEW)


def downgrade() -> None:
    # The application release that writes FAMACHA duties is gone with this
    # downgrade, so surviving rows normalize to the closest legacy value
    # before the tighter CHECK is restored (a scored animal needs a health
    # professional's eye on it, so HEALTH_CHECK is the honest bucket).
    op.execute("UPDATE tasks SET category = 'HEALTH_CHECK' WHERE category = 'FAMACHA'")
    _swap_constraint("ck_tasks_category", "tasks", "category", TASK_CATEGORIES_LEGACY)
