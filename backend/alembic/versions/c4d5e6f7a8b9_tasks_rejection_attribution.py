"""tasks rejected_by_id/rejected_at — rejection attribution

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a8
Create Date: 2026-08-09 11:20:00.000000+00:00

Completing, verifying and skipping a duty each record who acted and when
(``completed_by_id``/``completed_at``, ``verified_by_id``/``verified_at``,
``skipped_by_id``/``skipped_at``). Sending a duty back to its worker recorded
only the free-text ``verification_note``, so the verification trail had a hole
exactly where an operator disputes a rejection.

Add the nullable ``rejected_by_id`` FK (users.id) and naive-UTC ``rejected_at``,
stamped by the reject endpoint and cleared when the duty leaves the rejected
state. Both nullable, so existing rows migrate untouched and the paired CHECK
validates immediately.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("rejected_by_id", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("rejected_at", sa.DateTime(), nullable=True))
    op.create_foreign_key(
        "fk_tasks_rejected_by_id_users", "tasks", "users", ["rejected_by_id"], ["id"]
    )
    # Mirrors ck_tasks_verification_attribution: an attributed rejection always
    # carries its instant, so "who" can never be recorded without "when".
    op.create_check_constraint(
        "ck_tasks_rejection_attribution",
        "tasks",
        "rejected_by_id IS NULL OR rejected_at IS NOT NULL",
        postgresql_not_valid=True,
    )
    op.execute(sa.text("ALTER TABLE tasks VALIDATE CONSTRAINT ck_tasks_rejection_attribution"))


def downgrade() -> None:
    op.drop_constraint("ck_tasks_rejection_attribution", "tasks", type_="check")
    op.drop_constraint("fk_tasks_rejected_by_id_users", "tasks", type_="foreignkey")
    op.drop_column("tasks", "rejected_at")
    op.drop_column("tasks", "rejected_by_id")
