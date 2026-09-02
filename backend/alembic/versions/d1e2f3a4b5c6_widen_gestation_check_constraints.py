"""widen breeding gestation CHECK constraints for buffalo late records

Two CHECK constraints on ``breeding_records`` carried the goat maximum
gestation (200 days) as a hardcoded bound:

* ``ck_breeding_loss_within_max_gestation`` — ``loss_date <= breeding_date + 200``
* ``ck_breeding_records_result_within_max_gestation`` —
  ``ultrasound_result_date <= breeding_date + 200``

The service layer already validates against the farm's species profile
(buffalo ``max_gestation_days = 350``), so a legitimate buffalo abortion in
the last trimester (day 201-310) or a delayed PD result entry dated after
day 201 passed API validation and then exploded at flush time with an
unhandled ``CheckViolation`` → HTTP 500.

The shared table cannot express a per-farm-type constraint, so the CHECKs are
widened to the global maximum across species (350 days) and the tight
per-species windows remain enforced in ``services.breeding``.

Revision ID: d1e2f3a4b5c6
Revises: f8a2c4e6b1d9
"""

from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "f8a2c4e6b1d9"
branch_labels = None
depends_on = None

CONSTRAINTS = (
    ("ck_breeding_loss_within_max_gestation", "breeding_records"),
    ("ck_breeding_records_result_within_max_gestation", "breeding_records"),
)


def upgrade() -> None:
    op.drop_constraint("ck_breeding_loss_within_max_gestation", "breeding_records", type_="check")
    op.create_check_constraint(
        "ck_breeding_loss_within_max_gestation",
        "breeding_records",
        "loss_date IS NULL OR loss_cause = 'ANIMAL_STATUS_CHANGE' "
        "OR loss_date <= breeding_date + 350",
    )
    op.drop_constraint(
        "ck_breeding_records_result_within_max_gestation", "breeding_records", type_="check"
    )
    op.create_check_constraint(
        "ck_breeding_records_result_within_max_gestation",
        "breeding_records",
        "ultrasound_result_date IS NULL OR ultrasound_result_date <= breeding_date + 350",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_breeding_records_result_within_max_gestation", "breeding_records", type_="check"
    )
    op.create_check_constraint(
        "ck_breeding_records_result_within_max_gestation",
        "breeding_records",
        "ultrasound_result_date IS NULL OR ultrasound_result_date <= breeding_date + 200",
    )
    op.drop_constraint("ck_breeding_loss_within_max_gestation", "breeding_records", type_="check")
    op.create_check_constraint(
        "ck_breeding_loss_within_max_gestation",
        "breeding_records",
        "loss_date IS NULL OR loss_cause = 'ANIMAL_STATUS_CHANGE' "
        "OR loss_date <= breeding_date + 200",
    )
