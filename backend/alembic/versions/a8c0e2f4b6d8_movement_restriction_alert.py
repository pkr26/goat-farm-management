"""Movement-restriction alert class (ITEM 4 remediation, 2026-09-21)

Sixth per-class opt-in on ``notification_recipients``: the scheduled-disease
restriction placements (health log + mortality) fan out to recipients who
opted into ``MOVEMENT_RESTRICTION``. Plain nullable-free ADD COLUMN with a
false server default — existing recipients stay opted out.
"""

import sqlalchemy as sa

from alembic import op

revision = "a8c0e2f4b6d8"
down_revision = "f6b8d0e2a4c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_recipients",
        sa.Column(
            "movement_restriction",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("notification_recipients", "movement_restriction")
