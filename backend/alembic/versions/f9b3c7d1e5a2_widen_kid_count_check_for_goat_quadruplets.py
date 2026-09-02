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

from alembic import op

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
    op.drop_constraint("ck_breeding_records_kid_count", "breeding_records", type_="check")
    op.create_check_constraint(
        "ck_breeding_records_kid_count",
        "breeding_records",
        "kid_count_detected IS NULL OR kid_count_detected BETWEEN 1 AND 3",
    )
