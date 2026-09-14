"""widen task/health/kidding-ease vocabularies for husbandry standards

Revision ID: f1e2d3c4b5a6
Revises: c4f6a8b0d2e5
Create Date: 2026-09-14 00:00:00.000000+00:00

The husbandry-standards release extends three server-owned vocabularies:

- ``tasks.category`` — 11 duty categories (kidding watch, birthing kit,
  health check, heat watch, hoof trimming, spraying, disinfection,
  weighing, rebreed, buck rotation, insurance);
- ``health_events.type`` — EXAM and FECAL_EXAM clinical events;
- ``kidding_records.ease`` — CAESAREAN.

Their CHECK constraints still list the previous values verbatim, so the
first generated duty of a new category would violate the constraint on
every existing installation. Widen the CHECKs to the new full lists
(app/models/enums.py remains the single source; these literals are pinned
byte-for-byte by tests/test_domain_check_constraints.py).

Both directions follow f8a2c4e6b1d9's pattern: swap the constraint NOT
VALID (brief ACCESS EXCLUSIVE for the catalog swap only), then VALIDATE
CONSTRAINT under SHARE UPDATE EXCLUSIVE so reads/writes on the shared
tables never stall behind a full-scan lock. Validation cannot fail on
data — the legacy constraint guarantees no new value exists before the
upgrade, and the downgrade normalizes them away first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f1e2d3c4b5a6"
down_revision: str | Sequence[str] | None = "c4f6a8b0d2e5"
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
)
TASK_CATEGORIES_NEW = (
    *TASK_CATEGORIES_LEGACY,
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
)
HEALTH_TYPES_LEGACY = ("VACCINE", "DEWORMING", "TREATMENT", "FOOTBATH", "VITAMIN")
HEALTH_TYPES_NEW = (*HEALTH_TYPES_LEGACY, "EXAM", "FECAL_EXAM")
KIDDING_EASE_LEGACY = ("NORMAL", "ASSISTED", "DIFFICULT")
KIDDING_EASE_NEW = (*KIDDING_EASE_LEGACY, "CAESAREAN")


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
    _swap_constraint("ck_health_events_type", "health_events", "type", HEALTH_TYPES_NEW)
    _swap_constraint("ck_kidding_records_ease", "kidding_records", "ease", KIDDING_EASE_NEW)


def downgrade() -> None:
    # The application release that writes the new values is gone with this
    # downgrade, so surviving rows normalize to their closest legacy value
    # (the f8a2c4e6b1d9 pattern) before the tighter CHECK is restored.
    op.execute(
        f"UPDATE tasks SET category = 'OTHER' "
        f"WHERE category IN ({_in_list(TASK_CATEGORIES_NEW[len(TASK_CATEGORIES_LEGACY) :])})"
    )
    op.execute("UPDATE health_events SET type = 'TREATMENT' WHERE type IN ('EXAM', 'FECAL_EXAM')")
    op.execute("UPDATE kidding_records SET ease = 'DIFFICULT' WHERE ease = 'CAESAREAN'")
    _swap_constraint("ck_kidding_records_ease", "kidding_records", "ease", KIDDING_EASE_LEGACY)
    _swap_constraint("ck_health_events_type", "health_events", "type", HEALTH_TYPES_LEGACY)
    _swap_constraint("ck_tasks_category", "tasks", "category", TASK_CATEGORIES_LEGACY)
