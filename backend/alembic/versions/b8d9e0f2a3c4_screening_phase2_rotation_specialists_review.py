"""screening phase 2: rotation stages, specialist findings, vet review

Widens ``ck_screening_runs_stage`` for the cascade vocabulary (CROSS_CHECK,
SPECIALIST_*) and adds the vet-review columns to ``screening_findings``
(``severity`` from specialists; ``reviewed_by_id``/``reviewed_at``/
``review_note`` from confirm/reject). The severity CHECK and the
review-matches-status CHECK keep the review corpus honest for training
exports later.

Revision ID: b8d9e0f2a3c4
Revises: a7c8d9e0f1b2
"""

import sqlalchemy as sa

from alembic import op

revision = "b8d9e0f2a3c4"
down_revision = "a7c8d9e0f1b2"
branch_labels = None
depends_on = None

_STAGE_VOCAB = (
    "'GATE', 'CROSS_CHECK', 'SPECIALIST_SKIN', 'SPECIALIST_EYE', "
    "'SPECIALIST_HOOF', 'SPECIALIST_UDDER', 'SPECIALIST_GENERAL'"
)


def upgrade() -> None:
    op.drop_constraint("ck_screening_runs_stage", "screening_runs", type_="check")
    op.create_check_constraint(
        "ck_screening_runs_stage", "screening_runs", f"stage IN ({_STAGE_VOCAB})"
    )

    op.add_column("screening_findings", sa.Column("severity", sa.String(10), nullable=True))
    op.add_column("screening_findings", sa.Column("reviewed_by_id", sa.Integer(), nullable=True))
    op.add_column("screening_findings", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.add_column("screening_findings", sa.Column("review_note", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_screening_findings_severity",
        "screening_findings",
        "severity IS NULL OR severity IN ('mild', 'moderate', 'severe')",
    )
    op.create_check_constraint(
        "ck_screening_findings_review_matches_status",
        "screening_findings",
        "(status = 'PENDING_REVIEW') = (reviewed_at IS NULL AND reviewed_by_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_screening_findings_review_note_nonblank",
        "screening_findings",
        "review_note IS NULL OR btrim(review_note) <> ''",
    )
    op.create_foreign_key(
        "fk_screening_findings_reviewer",
        "screening_findings",
        "users",
        ["reviewed_by_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_screening_findings_reviewed_by_id", "screening_findings", ["reviewed_by_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_screening_findings_reviewed_by_id", table_name="screening_findings")
    op.drop_constraint("fk_screening_findings_reviewer", "screening_findings", type_="foreignkey")
    op.drop_constraint(
        "ck_screening_findings_review_note_nonblank", "screening_findings", type_="check"
    )
    op.drop_constraint(
        "ck_screening_findings_review_matches_status", "screening_findings", type_="check"
    )
    op.drop_constraint("ck_screening_findings_severity", "screening_findings", type_="check")
    op.drop_column("screening_findings", "review_note")
    op.drop_column("screening_findings", "reviewed_at")
    op.drop_column("screening_findings", "reviewed_by_id")
    op.drop_column("screening_findings", "severity")
    op.drop_constraint("ck_screening_runs_stage", "screening_runs", type_="check")
    op.create_check_constraint("ck_screening_runs_stage", "screening_runs", "stage IN ('GATE')")
