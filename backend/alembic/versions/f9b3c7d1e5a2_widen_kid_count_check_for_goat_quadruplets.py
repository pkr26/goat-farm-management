"""widen kid_count CHECK to the cross-species maximum (4)

``ck_breeding_records_kid_count`` carried the goat SPEC's SINGLE/TWIN/TRIPLET
wording (``kid_count_detected BETWEEN 1 AND 3``) as a hardcoded bound. Goat
litters legitimately reach quadruplets (SpeciesProfile.max_litter_size = 4),
and the delivered litter already accepts 4 (BirthType.QUADRUPLET), so a
quadruplet scan passed API validation and then exploded at flush time with an
unhandled ``CheckViolation`` -> HTTP 500.

The shared table cannot express a per-farm-type constraint, so the CHECK is
widened to the global maximum across species (4); the tight per-species caps
(4 goat / 2 buffalo) remain enforced in ``services.breeding
.record_ultrasound_result``.

Revision ID: f9b3c7d1e5a2
Revises: b5d7f9a1c3e5
"""

from alembic import context, op
from sqlalchemy import text

revision = "f9b3c7d1e5a2"
down_revision = "b5d7f9a1c3e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_breeding_records_kid_count", "breeding_records", type_="check")
    op.create_check_constraint(
        "ck_breeding_records_kid_count",
        "breeding_records",
        "kid_count_detected IS NULL OR kid_count_detected BETWEEN 1 AND 4",
    )


def downgrade() -> None:
    # House preflight (see e6f8a0b2c4d7): refuse while live quadruplet rows
    # would violate the narrowed CHECK instead of aborting mid-walk with a
    # raw CheckViolation that names only the constraint, not the rows.
    incompatible_sql = (
        "SELECT id FROM breeding_records WHERE kid_count_detected > 3 "
        "ORDER BY id LIMIT 1"
    )
    if context.is_offline_mode():
        op.execute(
            "DO $$ DECLARE incompatible_id bigint; BEGIN "
            f"SELECT id INTO incompatible_id FROM ({incompatible_sql}) AS incompatible; "
            "IF incompatible_id IS NOT NULL THEN "
            "RAISE EXCEPTION 'Cannot restore the triplet kid_count maximum while "
            "breeding record % detected a quadruplet litter', incompatible_id; "
            "END IF; END $$"
        )
    else:
        incompatible_id = op.get_bind().execute(text(incompatible_sql)).scalar_one_or_none()
        if incompatible_id is not None:
            raise RuntimeError(
                "Cannot restore the triplet kid_count maximum while breeding record "
                f"{incompatible_id} detected a quadruplet litter; reconcile that "
                "record before downgrading"
            )
    op.drop_constraint("ck_breeding_records_kid_count", "breeding_records", type_="check")
    op.create_check_constraint(
        "ck_breeding_records_kid_count",
        "breeding_records",
        "kid_count_detected IS NULL OR kid_count_detected BETWEEN 1 AND 3",
    )
