"""tasks skipped_by_id — skip attribution

Revision ID: 91a712b0367b
Revises: d8f2b6a41e90
Create Date: 2026-08-07 06:28:06.648493+00:00

Duties completed via /api/tasks/{id}/complete carry completed_by_id, but a
SKIPPED duty recorded no actor at all. Add the nullable skipped_by_id FK
(users.id), stamped by the skip endpoint; NULL for service-side skips
(abort, kidding leftovers, death/sale) where no single user made the call.
Nullable, so existing rows migrate untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "91a712b0367b"
down_revision: str | Sequence[str] | None = "d8f2b6a41e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("skipped_by_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_tasks_skipped_by_id_users", "tasks", "users", ["skipped_by_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_tasks_skipped_by_id_users", "tasks", type_="foreignkey")
    op.drop_column("tasks", "skipped_by_id")
