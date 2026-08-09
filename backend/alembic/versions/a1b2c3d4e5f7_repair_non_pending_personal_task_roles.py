"""repair personal task roles left behind by the PENDING-only backfill

Revision ID: a1b2c3d4e5f7
Revises: f4e5f6a7b8c9
Create Date: 2026-08-09 09:40:00.000000+00:00

``f3d4e5f6a7b8`` backfilled ``tasks.assigned_role_id`` only for rows whose
status was already ``PENDING``, because that is all
``ck_tasks_user_assignment_has_role`` rejects at INSERT time.  The constraint
is ``status <> 'PENDING' OR assigned_user_id IS NULL OR assigned_role_id IS NOT
NULL``, so it also fires on any UPDATE that moves a row back *into* PENDING —
which is exactly what verification rejection does.  A DONE/VERIFIED/SKIPPED
personal duty carried over from a release that derived the role solely from the
request payload therefore becomes permanently un-rejectable: the flush raises
CheckViolation and the endpoint 500s, with no in-app way to reassign the duty.

Repair every remaining personal row regardless of status, from the same source
of truth the earlier revision used (the assignee's retained same-farm
membership and its live role).  Rows that still cannot be resolved are left
alone deliberately: they are already legal for their current status, and
failing the release would block a deployment over history that the running
application repairs in bounded background batches.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | Sequence[str] | None = "f4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Module level so the regression test can exercise the exact shipped statement
# against a row this revision has already repaired on the suite's database.
REPAIR_PERSONAL_TASK_ROLES = """
    UPDATE tasks AS task
    SET assigned_role_id = membership.role_id
    FROM farm_memberships AS membership
    JOIN roles AS role
      ON role.farm_id = membership.farm_id
     AND role.id = membership.role_id
     AND role.deleted_at IS NULL
    WHERE task.assigned_user_id IS NOT NULL
      AND task.assigned_role_id IS NULL
      AND membership.farm_id = task.farm_id
      AND membership.user_id = task.assigned_user_id
"""


def upgrade() -> None:
    op.execute(sa.text(REPAIR_PERSONAL_TASK_ROLES))


def downgrade() -> None:
    # Intentionally irreversible: the repaired rows are indistinguishable from
    # duties that always carried a role, and clearing them would re-arm the
    # check-constraint violation this revision exists to remove.
    pass
