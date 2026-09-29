"""notification tenant hardening (2026-09-28 audit, N4)

Revision ID: d7e9f1a3b5c7
Revises: c3d4e5f6a7b8
Create Date: 2026-09-28 00:00:00.000000+00:00

The notifications wave (e5a7c9d1b3f5) predates the tenant composite-FK
program: ``notification_log.recipient_id`` was a bare single-column FK, so a
log row could point across farms via direct SQL. This revision:

* adds the ``uq_notification_recipients_farm_id_id`` candidate key and swaps
  the log's recipient FK for the composite ``(farm_id, recipient_id)`` guard
  every sibling tenant table carries (NOT VALID -> VALIDATE: the metadata
  lock stays brief, validation is fail-closed inside this transaction);
* pins the server-owned vocabularies with CHECKs — ``alert_class`` and
  ``status``, the latter including the new SENDING claim state introduced by
  the dedupe-claim fix (2026-09-28 audit, N2);
* adds the ``phone`` nonblank CHECK;
* retypes ``local_date`` String(10) -> Date (every other date column in the
  schema is a Date; stored values are ISO text and cast cleanly).

The CHECK literals keep in step with app/models/notifications.py
(ALERT_CLASSES / NOTIFICATION_LOG_STATUSES via sql_in_values member order);
the rendered bytes are pinned by tests/test_domain_check_constraints.py.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7e9f1a3b5c7"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALERT_CLASSES = (
    "DAILY_DIGEST",
    "SCREENING_FLAG",
    "KIDDING_WATCH",
    "OVERDUE_CRITICAL",
    "FEED_REORDER",
    "MOVEMENT_RESTRICTION",
)
NOTIFICATION_LOG_STATUSES = ("SENDING", "SENT", "FAILED", "SKIPPED_QUIET", "SKIPPED_CAP")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_notification_recipients_farm_id_id", "notification_recipients", ["farm_id", "id"]
    )
    op.create_check_constraint(
        "ck_notification_recipients_phone_nonblank",
        "notification_recipients",
        "btrim(phone) <> ''",
    )
    op.drop_constraint("notification_log_recipient_id_fkey", "notification_log", type_="foreignkey")
    op.create_foreign_key(
        "fk_notification_log_farm_id_recipient_id",
        "notification_log",
        "notification_recipients",
        ["farm_id", "recipient_id"],
        ["farm_id", "id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.execute(
        'ALTER TABLE "notification_log" '
        'VALIDATE CONSTRAINT "fk_notification_log_farm_id_recipient_id"'
    )
    op.create_check_constraint(
        "ck_notification_log_alert_class",
        "notification_log",
        f"alert_class IN ({_in_list(ALERT_CLASSES)})",
        postgresql_not_valid=True,
    )
    op.execute(
        'ALTER TABLE "notification_log" VALIDATE CONSTRAINT "ck_notification_log_alert_class"'
    )
    op.create_check_constraint(
        "ck_notification_log_status",
        "notification_log",
        f"status IN ({_in_list(NOTIFICATION_LOG_STATUSES)})",
        postgresql_not_valid=True,
    )
    op.execute('ALTER TABLE "notification_log" VALIDATE CONSTRAINT "ck_notification_log_status"')
    op.alter_column(
        "notification_log",
        "local_date",
        existing_type=sa.String(length=10),
        type_=sa.Date(),
        postgresql_using="local_date::date",
    )


def downgrade() -> None:
    op.alter_column(
        "notification_log",
        "local_date",
        existing_type=sa.Date(),
        type_=sa.String(length=10),
        postgresql_using="local_date::text",
    )
    op.drop_constraint("ck_notification_log_status", "notification_log", type_="check")
    op.drop_constraint("ck_notification_log_alert_class", "notification_log", type_="check")
    op.drop_constraint(
        "fk_notification_log_farm_id_recipient_id", "notification_log", type_="foreignkey"
    )
    op.create_foreign_key(
        "notification_log_recipient_id_fkey",
        "notification_log",
        "notification_recipients",
        ["recipient_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "ck_notification_recipients_phone_nonblank", "notification_recipients", type_="check"
    )
    op.drop_constraint(
        "uq_notification_recipients_farm_id_id", "notification_recipients", type_="unique"
    )
