"""notification_recipients membership tenant FK (2026-09-29 audit, L2)

Revision ID: c1d3e5f7a9b4
Revises: d9e0f2a4b6c8
Create Date: 2026-09-29 00:00:00.000000+00:00

d7e9f1a3b5c7 closed the tenant hole for ``notification_log.recipient_id``
but left the one remaining single-column tenant reference in place:
``notification_recipients.membership_id`` — exactly the cross-farm class
that migration's docstring claims every sibling table no longer carries.
This revision finishes the program:

* adds the ``uq_farm_memberships_farm_id_id`` candidate key (roles already
  carries its own) so the composite FK has a target;
* swaps the membership FK for the composite ``(farm_id, membership_id)``
  guard (NOT VALID -> VALIDATE, the same brief-metadata-lock pattern as
  d7e9f1a3b5c7; validation is fail-closed inside this transaction).

No query plan changes: the existing ``uq_notification_recipients_membership``
unique index already leads with the FK's exact column pair.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c1d3e5f7a9b4"
down_revision: str | Sequence[str] | None = "d9e0f2a4b6c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_farm_memberships_farm_id_id", "farm_memberships", ["farm_id", "id"]
    )
    op.drop_constraint(
        "notification_recipients_membership_id_fkey",
        "notification_recipients",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_notification_recipients_farm_id_membership_id",
        "notification_recipients",
        "farm_memberships",
        ["farm_id", "membership_id"],
        ["farm_id", "id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.execute(
        'ALTER TABLE "notification_recipients" '
        'VALIDATE CONSTRAINT "fk_notification_recipients_farm_id_membership_id"'
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_notification_recipients_farm_id_membership_id",
        "notification_recipients",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "notification_recipients_membership_id_fkey",
        "notification_recipients",
        "farm_memberships",
        ["membership_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_farm_memberships_farm_id_id", "farm_memberships", type_="unique")
