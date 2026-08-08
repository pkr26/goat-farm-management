"""query performance indexes

Revision ID: e4a7c1f29b63
Revises: b3e91c47a2f5
Create Date: 2026-08-07 19:45:00.000000+00:00

Missing indexes for query performance:

- ix_tasks_purchase_batch_id: Task.purchase_batch_id is queried with
  ``IN (...)`` on every purchases list and batch detail; ``tasks`` is
  ever-growing, so the seq scan worsened with age.
- ix_tasks_breeding_record_id: filtered by _pending_tasks_for on every
  ultrasound/kidding/abort flow.
- ix_farms_owner_id: accessible_farms / farm-list lookups on every
  login-adjacent request.

Model-side ``index=True`` on the matching columns lands in the same commit.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e4a7c1f29b63"
down_revision: str | Sequence[str] | None = "b3e91c47a2f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(op.f("ix_farms_owner_id"), "farms", ["owner_id"], unique=False)
    op.create_index(
        op.f("ix_tasks_breeding_record_id"), "tasks", ["breeding_record_id"], unique=False
    )
    op.create_index(
        op.f("ix_tasks_purchase_batch_id"), "tasks", ["purchase_batch_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tasks_purchase_batch_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_breeding_record_id"), table_name="tasks")
    op.drop_index(op.f("ix_farms_owner_id"), table_name="farms")
