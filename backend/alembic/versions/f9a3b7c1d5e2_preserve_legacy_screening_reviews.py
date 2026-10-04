"""Preserve known legacy screening decisions before their first re-review.

Revision ID: f9a3b7c1d5e2
Revises: f8e2f6a0c5d3

Revision zero is a snapshot of the actual stored legacy decision, with its
original author, timestamp and note. Its previous_status equals status to
represent a baseline, not an invented earlier transition. Reviews already
overwritten before this upgrade cannot be reconstructed from current rows.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f9a3b7c1d5e2"
down_revision: str | Sequence[str] | None = "f8e2f6a0c5d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Block SELECT FOR UPDATE before taking History's DDL lock. SHARE ROW
    # EXCLUSIVE would admit a review's ROW SHARE lock, letting it insert
    # History and then wait on Finding while this upgrade waits on History.
    # EXCLUSIVE preserves that order while still permitting plain readers.
    op.execute("LOCK TABLE screening_findings IN EXCLUSIVE MODE")
    op.drop_constraint(
        "ck_screening_finding_reviews_revision", "screening_finding_reviews", type_="check"
    )
    op.create_check_constraint(
        "ck_screening_finding_reviews_revision", "screening_finding_reviews", "revision >= 0"
    )
    op.create_check_constraint(
        "ck_screening_finding_reviews_legacy_snapshot",
        "screening_finding_reviews",
        "revision <> 0 OR previous_status = status",
    )
    op.execute(
        "INSERT INTO screening_finding_reviews "
        "(finding_id, revision, farm_id, previous_status, status, review_note, "
        "reviewed_by_id, reviewed_at) "
        "SELECT id, 0, farm_id, status, status, review_note, reviewed_by_id, reviewed_at "
        "FROM screening_findings WHERE review_revision = 0 "
        "AND status IN ('CONFIRMED', 'REJECTED') "
        "AND reviewed_by_id IS NOT NULL AND reviewed_at IS NOT NULL "
        "ON CONFLICT (finding_id, revision) DO NOTHING"
    )


def downgrade() -> None:
    # Dropping the zero snapshots would lose attribution again. Permit a
    # normal blank-database roundtrip, but require an explicit evidence archive
    # and reconciliation before an operator rolls back populated history.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM screening_finding_reviews WHERE revision = 0) "
        "THEN RAISE EXCEPTION 'Legacy screening review snapshots would be lost; "
        "archive and reconcile this evidence before rollback.'; END IF; END $$"
    )
    op.drop_constraint(
        "ck_screening_finding_reviews_legacy_snapshot", "screening_finding_reviews", type_="check"
    )
    op.drop_constraint(
        "ck_screening_finding_reviews_revision", "screening_finding_reviews", type_="check"
    )
    op.create_check_constraint(
        "ck_screening_finding_reviews_revision", "screening_finding_reviews", "revision > 0"
    )
