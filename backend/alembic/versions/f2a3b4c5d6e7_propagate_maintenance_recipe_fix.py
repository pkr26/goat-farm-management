"""propagate the maintenance-recipe mineral/salt standards fix

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-14 00:00:00.000000+00:00

The husbandry-standards release corrected the seeded MAINTENANCE_75_25
recipe in app/seed.py (mineral mixture 0.75 -> 2.0 kg/100kg per the ~2%
ICAR standard, a 1.0 Salt line added, DORB cut 7.5 -> 5.25 so the
concentrates still total the same 100 kg). seed_reference_data runs
ON CONFLICT DO NOTHING by design — "changing an existing definition
remains an explicit data migration" — so every already-seeded database
kept the old recipe forever. This is that data migration.

Each UPDATE is guarded on the row still carrying the pre-fix value, so a
deliberately hand-tuned global recipe is not silently clobbered; the Salt
line is only inserted when absent. Downgrade restores the exact pre-fix
composition (and removes Salt) under the same guards.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECIPE_CODE = "MAINTENANCE_75_25"


def _recipe_id_select() -> str:
    return f"(SELECT id FROM feed_recipes WHERE code = '{_RECIPE_CODE}')"


def upgrade() -> None:
    recipe_id = _recipe_id_select()
    op.execute(
        f"""
        UPDATE feed_recipe_lines SET kg_per_100kg = 2.0
        WHERE recipe_id = {recipe_id}
          AND ingredient = 'Mineral mix' AND kg_per_100kg = 0.75
        """
    )
    op.execute(
        f"""
        UPDATE feed_recipe_lines SET kg_per_100kg = 5.25
        WHERE recipe_id = {recipe_id}
          AND ingredient = 'DORB' AND kg_per_100kg = 7.5
        """
    )
    # SELECT FROM feed_recipes (not a scalar subselect): a database whose
    # reference data was never seeded has no MAINTENANCE_75_25 row, and the
    # migration must no-op there rather than insert a NULL recipe_id. The
    # UPDATEs above are naturally no-ops in that case.
    op.execute(
        """
        INSERT INTO feed_recipe_lines (recipe_id, ingredient, kg_per_100kg, category)
        SELECT r.id, 'Salt', 1.0, 'CONCENTRATE'
        FROM feed_recipes r
        WHERE r.code = 'MAINTENANCE_75_25'
          AND NOT EXISTS (
            SELECT 1 FROM feed_recipe_lines
            WHERE recipe_id = r.id AND ingredient = 'Salt'
          )
        """
    )


def downgrade() -> None:
    recipe_id = _recipe_id_select()
    op.execute(
        f"""
        DELETE FROM feed_recipe_lines
        WHERE recipe_id = {recipe_id}
          AND ingredient = 'Salt' AND kg_per_100kg = 1.0
              AND category = 'CONCENTRATE'
        """
    )
    op.execute(
        f"""
        UPDATE feed_recipe_lines SET kg_per_100kg = 7.5
        WHERE recipe_id = {recipe_id}
          AND ingredient = 'DORB' AND kg_per_100kg = 5.25
        """
    )
    op.execute(
        f"""
        UPDATE feed_recipe_lines SET kg_per_100kg = 0.75
        WHERE recipe_id = {recipe_id}
          AND ingredient = 'Mineral mix' AND kg_per_100kg = 2.0
        """
    )
