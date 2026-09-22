"""Drop redundant single-column ledger/health/task indexes

Redundant-index hygiene (2026-09-21 audit, B-small batch). Three
single-column indexes duplicate tenant-scoped composites that serve every
query the application actually runs:

* ix_transactions_date — superseded by ix_transactions_farm_date_id
  (farm_id, date DESC, id DESC); every ledger read is farm-scoped.
* ix_health_events_date — superseded by ix_health_events_farm_date_id
  (farm_id, date DESC, id DESC); every events feed is farm-scoped.
* ix_tasks_recurring_series_id — superseded by the full UNIQUE constraint
  uq_task_recurring_series_due (farm_id, recurring_series_id, due_date),
  which serves every spawn-dedup lookup (always tenant-scoped).

The audit also flagged ~10 single-column farm_id indexes as candidates, but
the review kept them: every farm-leading composite on those tables is a
PARTIAL index (postgresql_where), so the plain farm_id singles remain the
only full indexes able to serve farm_id equality scans (FK enforcement on
farm deletes, unscoped maintenance lookups). Dropping them would have been
the wrong trade.

Drops run CONCURRENTLY (IF EXISTS) so a primary running ingest is never
locked out of its index.
"""

from alembic import op

revision = "b1c3d5e7f9a2"
down_revision = "cad1e2f3a4b5"
branch_labels = None
depends_on = None

# (index, table, column) — the column is only needed to rebuild on downgrade.
_DROPS: tuple[tuple[str, str, str], ...] = (
    ("ix_transactions_date", "transactions", "date"),
    ("ix_health_events_date", "health_events", "date"),
    ("ix_tasks_recurring_series_id", "tasks", "recurring_series_id"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for index_name, _table_name, _column in _DROPS:
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"')


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for index_name, table_name, column in _DROPS:
            op.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{index_name}" '
                f'ON "{table_name}" ("{column}")'
            )
