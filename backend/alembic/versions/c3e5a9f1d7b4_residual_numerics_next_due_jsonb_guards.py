"""residual exact numerics, next-due ceiling, jsonb shape, business-date default

Revision ID: c3e5a9f1d7b4
Revises: d0f1a2b3c4d6
Create Date: 2026-09-17 00:00:00.000000+00:00

Audit remediation wave (backend-core workstream):

- Residual float8 columns the exact-numerics program missed move to NUMERIC
  with the f2c3d4e5f6a7 / d9e3f7a1b5c4 recipe: a classifying preflight
  refuses non-finite / overflow / sub-scale legacy values fail-closed
  (naming row ids) instead of silently rounding them, then ALTER COLUMN TYPE
  rewrites with ``round(col::numeric, s)``. The NaN / Infinity CHECK guards
  stay: numeric still accepts 'NaN'.
  * ``purchase_batches.avg_age_months`` / ``avg_weight_kg`` -> numeric(8, 2) —
    the batch averages seed estimated DOBs and first weight records, so
    binary drift compounds into schedule facts. Ages are fractional (6.5
    months is a real market fact), hence scale 2 like every weight.
  * ``feed_recipe_lines.kg_per_100kg`` -> numeric(15, 6) — decimal-exact
    instead of binary float, but at the recipe lines' own six-decimal
    tolerance: the codebase's feed-quantity contract explicitly keeps
    percent-level ratios from being truncated to grams
    (test_feed_quantity_numeric).
  * ``vaccine_templates.first_dose_age_months`` / ``booster_weeks`` /
    ``repeat_months`` -> numeric(8, 2) — cadences feed Decimal due-date
    arithmetic, and a drifted 3.999999-week booster would land duties on the
    wrong day.
- ``health_events.next_due_date`` gains the immutability ceiling
  ``withdrawal_until`` already has (b1c2d3e4f5a6): the CHECK is created NOT
  VALID and validated after a fail-closed preflight names offending ids.
  Validator twin: MAX_NEXT_DUE_DAYS in app/schemas/health.py.
- ``tasks.title_args`` gains the jsonb shape CHECK every other JSONB column
  carries (``jsonb_typeof(title_args) = 'object'``), same NOT VALID ->
  VALIDATE + preflight pattern.
- ``bucket_moves.effective_date`` drops its CURRENT_DATE server default: the
  ORM ``today`` default resolves the farm's IANA timezone while CURRENT_DATE
  stamps the UTC calendar day, so an out-of-band writer silently recorded
  the wrong business date around midnight; it now fails NOT NULL loudly.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "c3e5a9f1d7b4"
down_revision: str | Sequence[str] | None = "d0f1a2b3c4d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_NEXT_DUE_DAYS = 3650

# (table, column, precision, scale, nullable) for the residual float8 ->
# numeric rewrites. Precisions match the domain bounds the existing CHECK
# constraints already enforce (weights/ages <= 1000/240; recipe proportion
# <= 100), so numeric(8, 2) and the gram-scale numeric(15, 3) leave headroom.
_NUMERIC_COLUMNS: tuple[tuple[str, str, int, int, bool], ...] = (
    ("purchase_batches", "avg_age_months", 8, 2, True),
    ("purchase_batches", "avg_weight_kg", 8, 2, True),
    ("feed_recipe_lines", "kg_per_100kg", 15, 6, False),
    ("vaccine_templates", "first_dose_age_months", 8, 2, True),
    ("vaccine_templates", "booster_weeks", 8, 2, True),
    ("vaccine_templates", "repeat_months", 8, 2, True),
)


def _numeric_max(precision: int, scale: int) -> str:
    """Largest finite literal the target numeric(p, s) can store."""
    return "9" * (precision - scale) + "." + "9" * scale


def _preflight_numeric(table: str, column: str, precision: int, scale: int) -> None:
    """One classifying scan per column; deterministic samples enable repair.

    CASE (unlike a bare OR chain) guarantees ordered, lazy evaluation, so the
    numeric casts in the later arms never execute on a non-finite value.
    PostgreSQL's float8 -> numeric cast removes ordinary binary artifacts; a
    remaining difference past the target scale is real legacy information and
    is never silently rounded away.
    """
    classify = (
        "CASE "
        f"WHEN \"{column}\"::text IN ('NaN', 'Infinity', '-Infinity') THEN 'non-finite' "
        f"WHEN abs(\"{column}\"::numeric) > {_numeric_max(precision, scale)} THEN 'overflow' "
        f'WHEN "{column}"::numeric <> round("{column}"::numeric, {scale}) '
        f"THEN 'sub-{scale}-dp' "
        "END"
    )
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                f'SELECT id, {classify} AS kind FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL AND {classify} IS NOT NULL '
                "ORDER BY id LIMIT 30"
            )
        )
        .all()
    )
    if rows:
        problems: dict[str, list[int]] = {}
        for row in rows:
            problems.setdefault(str(row.kind), []).append(int(row.id))
        details = "; ".join(
            f"{kind} values at row ids {ids}" for kind, ids in sorted(problems.items())
        )
        raise RuntimeError(
            f"Refusing exact-numeric migration: {table}.{column} contains "
            f"{details}; correct them explicitly before retrying"
        )


def _preflight_constraint_rows() -> None:
    """Fail closed with small deterministic samples; never invent corrections."""
    op.execute(
        sa.text(
            f"""
            DO $integrity_preflight$
            DECLARE
                bad_ids text;
            BEGIN
                SELECT string_agg(id::text, ', ' ORDER BY id)
                INTO bad_ids
                FROM (
                    SELECT id
                    FROM health_events
                    WHERE next_due_date IS NOT NULL
                      AND next_due_date > date + {MAX_NEXT_DUE_DAYS}
                    ORDER BY id
                    LIMIT 20
                ) AS invalid;
                IF bad_ids IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot install next-due ceiling: health_events ids % '
                        'exceed % days. Health events are immutable; '
                        'reconcile each record explicitly before retrying.',
                        bad_ids, {MAX_NEXT_DUE_DAYS};
                END IF;

                SELECT string_agg(id::text, ', ' ORDER BY id)
                INTO bad_ids
                FROM (
                    SELECT id
                    FROM tasks
                    WHERE jsonb_typeof(title_args) IS DISTINCT FROM 'object'
                    ORDER BY id
                    LIMIT 20
                ) AS invalid;
                IF bad_ids IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot install title_args shape guard: tasks ids % '
                        'carry a non-object payload. Correct them explicitly '
                        'before retrying.',
                        bad_ids;
                END IF;
            END
            $integrity_preflight$
            """
        )
    )


def _add_validated_check(name: str, table: str, condition: str) -> None:
    # NOT VALID keeps the initial metadata lock short; explicit validation is
    # still fail-closed and runs inside this migration's transaction
    # (b1c2d3e4f5a6 pattern). The preflights above prove validation cannot
    # fail on existing rows; the constraint still guards every future write.
    op.create_check_constraint(name, table, condition, postgresql_not_valid=True)
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    # Bound lock waits for the ALTER COLUMN TYPE rewrites (ACCESS EXCLUSIVE
    # on live tables); the preflights only read already-committed rows.
    op.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    # Offline (--sql) generation cannot inspect data; emit the caveat into
    # the generated script and keep the preflights for online runs.
    if context.is_offline_mode():
        op.execute(
            "-- WARNING: offline generation skipped the exact-numeric and "
            "constraint preflights. Audit the converted columns for "
            "non-finite/overflow/sub-scale float values, health_events for "
            "next_due_date beyond the ceiling, and tasks for non-object "
            "title_args (or run 'alembic upgrade' online) before applying "
            "this script."
        )
    else:
        for table, column, precision, scale, _nullable in _NUMERIC_COLUMNS:
            _preflight_numeric(table, column, precision, scale)
        _preflight_constraint_rows()

    for table, column, precision, scale, nullable in _NUMERIC_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Float(),
            type_=sa.Numeric(precision, scale),
            existing_nullable=nullable,
            # The preflight proves this cannot change a meaningful value;
            # round only supplies the target column's explicit scale.
            postgresql_using=f'round("{column}"::numeric, {scale})',
        )

    # CURRENT_DATE resolves in the server's zone (UTC here), not the farm's
    # IANA business timezone the ORM ``today`` default uses. With the default
    # gone, an out-of-band writer that omits the business date fails NOT NULL
    # loudly instead of silently stamping the wrong calendar day.
    op.alter_column(
        "bucket_moves",
        "effective_date",
        existing_type=sa.Date(),
        existing_nullable=False,
        existing_server_default=sa.text("CURRENT_DATE"),
        server_default=None,
    )

    _add_validated_check(
        "ck_health_events_next_due_bounded",
        "health_events",
        f"next_due_date IS NULL OR next_due_date <= date + {MAX_NEXT_DUE_DAYS}",
    )
    _add_validated_check(
        "ck_tasks_title_args_json_object",
        "tasks",
        "jsonb_typeof(title_args) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_title_args_json_object", "tasks", type_="check")
    op.drop_constraint("ck_health_events_next_due_bounded", "health_events", type_="check")

    # Restore the UTC-day default the upgrade removed; the application
    # release that dropped it is gone with this downgrade.
    op.alter_column(
        "bucket_moves",
        "effective_date",
        existing_type=sa.Date(),
        existing_nullable=False,
        server_default=sa.text("CURRENT_DATE"),
    )

    # Back inside the transaction: the float8 rewrite takes ACCESS EXCLUSIVE.
    op.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    for table, column, precision, scale, nullable in reversed(_NUMERIC_COLUMNS):
        op.alter_column(
            table,
            column,
            existing_type=sa.Numeric(precision, scale),
            type_=sa.Float(),
            existing_nullable=nullable,
            postgresql_using=f'"{column}"::double precision',
        )
