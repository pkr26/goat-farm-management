"""breeding_records.outcome UNASSESSED — services closed by a herd exit

Revision ID: d1c2b3a4e5f6
Revises: c4d5e6f7a8b9
Create Date: 2026-08-09 13:10:00.000000+00:00

Selling, culling or recording the death of a doe auto-resolves a
CONFIRMED_PREGNANT service as ABORTED, but a PENDING (never ultrasounded) one
was left open forever: ``record_ultrasound_result`` refuses a non-ACTIVE doe,
so the only exit from PENDING was unreachable. Those rows kept the doe's
``uq_breeding_open_pregnancy`` slot and kept the UI offering an "Ultrasound
result" action that could only 409.

Add the terminal ``UNASSESSED`` outcome for "this service could never be
assessed because the animal left the herd". It deliberately shares PENDING's
column shape (``ultrasound_done`` false, ``pregnant``/``kid_count_detected``/
``ultrasound_result_date`` NULL, no expected kidding date): closing the record
must not invent a scan that never happened, so it is neither a conception nor
a recorded failure to conceive.

Existing rows are repaired: every PENDING service whose doe is already
SOLD/DEAD/CULLED is exactly the stuck state above and becomes UNASSESSED.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d1c2b3a4e5f6"
down_revision: str | Sequence[str] | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOME_VALUES_WITH_UNASSESSED = (
    "outcome IN ('PENDING', 'CONFIRMED_PREGNANT', 'FAILED', 'ABORTED', 'UNASSESSED')"
)
OUTCOME_VALUES_LEGACY = "outcome IN ('PENDING', 'CONFIRMED_PREGNANT', 'FAILED', 'ABORTED')"

OUTCOME_STATE_WITH_UNASSESSED = (
    "(outcome IN ('PENDING', 'UNASSESSED') AND ultrasound_done IS FALSE "
    "AND pregnant IS NULL "
    "AND kid_count_detected IS NULL AND ultrasound_result_date IS NULL) OR "
    "(outcome = 'CONFIRMED_PREGNANT' AND ultrasound_done IS TRUE "
    "AND pregnant IS TRUE) OR "
    "(outcome = 'FAILED' AND ultrasound_done IS TRUE AND pregnant IS FALSE "
    "AND kid_count_detected IS NULL) OR "
    "(outcome = 'ABORTED' AND pregnant IS FALSE)"
)
OUTCOME_STATE_LEGACY = (
    "(outcome = 'PENDING' AND ultrasound_done IS FALSE AND pregnant IS NULL "
    "AND kid_count_detected IS NULL AND ultrasound_result_date IS NULL) OR "
    "(outcome = 'CONFIRMED_PREGNANT' AND ultrasound_done IS TRUE "
    "AND pregnant IS TRUE) OR "
    "(outcome = 'FAILED' AND ultrasound_done IS TRUE AND pregnant IS FALSE "
    "AND kid_count_detected IS NULL) OR "
    "(outcome = 'ABORTED' AND pregnant IS FALSE)"
)

EXPECTED_STATE_WITH_UNASSESSED = (
    "(outcome = 'CONFIRMED_PREGNANT' AND expected_kidding_date IS NOT NULL) OR "
    "(outcome IN ('PENDING', 'FAILED', 'UNASSESSED') AND expected_kidding_date IS NULL) OR "
    "outcome = 'ABORTED'"
)
EXPECTED_STATE_LEGACY = (
    "(outcome = 'CONFIRMED_PREGNANT' AND expected_kidding_date IS NOT NULL) OR "
    "(outcome IN ('PENDING', 'FAILED') AND expected_kidding_date IS NULL) OR "
    "outcome = 'ABORTED'"
)

STUCK_PENDING_SERVICES = sa.text(
    """
    UPDATE breeding_records br
    SET outcome = 'UNASSESSED'
    FROM animals a
    WHERE a.id = br.doe_id
      AND br.outcome = 'PENDING'
      AND a.status <> 'ACTIVE'
    """
)


def _swap_outcome_checks(values: str, outcome_state: str, expected_state: str) -> None:
    """Replace the three outcome CHECKs in one transaction.

    Dropping before creating keeps the rewrite valid in both directions; the
    table takes an ACCESS EXCLUSIVE lock for the (constant-time) drops and one
    validating scan per new CHECK.
    """
    for name in (
        "ck_breeding_records_expected_state",
        "ck_breeding_records_outcome_state",
        "ck_breeding_records_outcome",
    ):
        op.drop_constraint(name, "breeding_records", type_="check")
    op.create_check_constraint("ck_breeding_records_outcome", "breeding_records", values)
    op.create_check_constraint(
        "ck_breeding_records_outcome_state", "breeding_records", outcome_state
    )
    op.create_check_constraint(
        "ck_breeding_records_expected_state", "breeding_records", expected_state
    )


def upgrade() -> None:
    _swap_outcome_checks(
        OUTCOME_VALUES_WITH_UNASSESSED,
        OUTCOME_STATE_WITH_UNASSESSED,
        EXPECTED_STATE_WITH_UNASSESSED,
    )
    # Only after the new value is accepted. These are the rows the API could
    # never resolve; their PENDING column shape already satisfies the
    # UNASSESSED branch, so nothing else has to be rewritten.
    op.execute(STUCK_PENDING_SERVICES)


def downgrade() -> None:
    # Back to the stuck-but-legal PENDING state the constraints below allow.
    # A non-ACTIVE doe can never be re-bred, so at most one such service
    # exists per doe and restoring PENDING cannot collide with the
    # uq_breeding_open_pregnancy partial unique index.
    op.execute(
        sa.text("UPDATE breeding_records SET outcome = 'PENDING' WHERE outcome = 'UNASSESSED'")
    )
    _swap_outcome_checks(
        OUTCOME_VALUES_LEGACY,
        OUTCOME_STATE_LEGACY,
        EXPECTED_STATE_LEGACY,
    )
