"""store role permissions and saved plan/scenario JSON as jsonb

Revision ID: c4f6a8b0d2e5
Revises: b6d8f0a2c4e6
Create Date: 2026-09-08 12:20:00.000000+00:00

roles.permissions, planner_plans.targets, planner_plans.assumptions and
simulation_scenarios.assumptions stored JSON in Text with no json_valid
CHECK: a truncated or hand-edited value only failed at read time, far from
the write that corrupted it. One revision moves all four to jsonb:

- preflight parses every row's JSON and refuses fail-closed, naming row ids
  (the c8f1d3a5e709 money-migration preflight style) — the type conversion
  must never silently coerce or drop a legacy value;
- ALTER COLUMN TYPE jsonb USING <col>::jsonb rewrites each table under
  ACCESS EXCLUSIVE. All three tables are tenant-bounded and small (<= 50
  roles / 25 plans / 25 scenarios per farm), and lock_timeout bounds queue
  time, so no maintenance window is warranted;
- a jsonb_typeof CHECK pins each column's shape: permissions and
  planner_plans.targets are arrays (targets holds a list of PlannerTarget
  dumps — enforced as an array, NOT the object shape of the assumption
  columns), while both assumptions columns are objects. Valid-but-wrong-shape
  JSON now fails at the write, not on the next model_validate.

Downgrade restores Text via ::text — semantically identical JSON whose
whitespace/key order may differ from the originally written bytes (jsonb
normalization); no data is lost.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "c4f6a8b0d2e5"
down_revision: str | Sequence[str] | None = "b6d8f0a2c4e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, jsonb shape). The shape is per-column reality, not uniform:
# planner_plans.targets is a list of PlannerTarget dumps on every write path
# (api/planner.py serializes a list), so it is pinned as an array.
_JSON_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("roles", "permissions", "array"),
    ("planner_plans", "targets", "array"),
    ("planner_plans", "assumptions", "object"),
    ("simulation_scenarios", "assumptions", "object"),
)


def _preflight_json(table: str, column: str, shape: str) -> None:
    """Refuse before any DDL when a stored value is not convertible JSON.

    One scan per column over a tenant-bounded table; failures are grouped by
    kind and capped at 30 row ids so an operator can repair exactly the
    offending rows and retry the whole (still transactional) revision.
    """
    rows = op.get_bind().execute(sa.text(f'SELECT id, "{column}" FROM "{table}" ORDER BY id')).all()
    problems: dict[str, list[int]] = {}
    for row in rows:
        raw = row[1]
        try:
            decoded = json.loads(raw) if raw is not None else None
        except ValueError:
            kind = "invalid-json"
        else:
            if shape == "array" and not isinstance(decoded, list):
                kind = "not-an-array"
            elif shape == "object" and not isinstance(decoded, dict):
                kind = "not-an-object"
            else:
                continue
        problems.setdefault(kind, []).append(int(row[0]))
    if problems:
        details = "; ".join(
            f"{kind} values at row ids {ids[:30]}" for kind, ids in sorted(problems.items())
        )
        raise RuntimeError(
            f"Refusing jsonb migration: {table}.{column} contains {details}; "
            "correct them explicitly before retrying"
        )


def upgrade() -> None:
    # Offline (--sql) generation cannot inspect data, and refusing to render
    # would block DBA-reviewed deployments entirely; emit the caveat into the
    # generated script instead and keep the preflight for online runs.
    if context.is_offline_mode():
        op.execute(
            "-- WARNING: offline generation skipped the JSON preflight. Audit "
            "roles.permissions, planner_plans.targets, planner_plans.assumptions "
            "and simulation_scenarios.assumptions for unparseable or wrong-shape "
            "JSON (or run 'alembic upgrade' online) before applying this script."
        )
    else:
        for table, column, shape in _JSON_COLUMNS:
            _preflight_json(table, column, shape)

    for table, column, shape in _JSON_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Text(),
            type_=postgresql.JSONB(astext_type=sa.Text()),
            existing_nullable=False,
            postgresql_using=f"{column}::jsonb",
        )
        op.create_check_constraint(
            f"ck_{table}_{column}_json_{shape}",
            table,
            f"jsonb_typeof({column}) = '{shape}'",
        )


def downgrade() -> None:
    for table, column, shape in reversed(_JSON_COLUMNS):
        op.drop_constraint(f"ck_{table}_{column}_json_{shape}", table, type_="check")
        op.alter_column(
            table,
            column,
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            type_=sa.Text(),
            existing_nullable=False,
            postgresql_using=f"{column}::text",
        )
