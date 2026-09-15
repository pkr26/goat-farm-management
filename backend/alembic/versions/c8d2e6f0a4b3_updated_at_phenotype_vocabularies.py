"""updated_at on animals/farms, phenotype columns, bounded vocabularies

Revision ID: c8d2e6f0a4b3
Revises: b7c1d5e9f3a2
Create Date: 2026-09-14 00:00:00.000000+00:00

Audit remediation wave (backend-core workstream):

- ``animals.updated_at`` / ``farms.updated_at`` — row-mutation timestamps
  (server default ``timezone('UTC', now())``, naive-UTC convention from
  b2c3d4e5f6a8), backfilled from created_at and then NOT NULL.
- ``animals.coat_color`` / ``animals.horned`` — the Osmanabadi phenotype
  record (pure-line tracking for Bakrid premiums), both nullable.
- Bounded vocabularies, hardcoded as tuples in this revision (migrations
  must not drift with models/enums.py after they have run; the rendered
  bytes are pinned by tests/test_domain_check_constraints.py):
  - ``ck_animals_coat_color`` over CoatColor;
  - ``ck_animals_disposal_method`` over DisposalMethod — the column was free
    text, so legacy values are first normalized (case-insensitive spelling
    map) and any unmappable value refuses the upgrade, naming its row ids;
  - ``ck_health_events_route`` over AdministrationRoute — same recipe
    (upper-cased, common spellings mapped, unmappable refused);
  - ``ck_animals_breed_nonempty`` — breed is canonicalized (trimmed,
    case-normalized) and blank legacy rows fall back to the Osmanabadi
    default before the CHECK lands.

All CHECKs are created NOT VALID and validated in the same revision (the
f8a2c4e6b1d9 / b6d8f0a2c4e6 pattern): brief ACCESS EXCLUSIVE for the catalog
swap, then SHARE UPDATE EXCLUSIVE validation that never stalls reads/writes.
Every lock wait is bounded by SET LOCAL lock_timeout.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "c8d2e6f0a4b3"
down_revision: str | Sequence[str] | None = "b7c1d5e9f3a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DISPOSAL_METHOD_VALUES = ("DEEP_BURIAL", "BURNING", "RENDERING", "COMPOSTING", "OTHER")
_COAT_COLOR_VALUES = ("black", "black_patched", "brown", "white", "spotted")
_ROUTE_VALUES = ("SC", "IM", "IV", "ORAL", "TOPICAL", "INTRANASAL")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _add_validated_check(name: str, table: str, condition: str) -> None:
    """NOT VALID swap-in, then fail-closed validation (b6d8f0a2c4e6 pattern)."""
    op.create_check_constraint(name, table, condition, postgresql_not_valid=True)
    op.execute(sa.text(f'ALTER TABLE "{table}" VALIDATE CONSTRAINT "{name}"'))


def _normalize_or_refuse(table: str, column: str, mapping_sql: str, vocabulary: str) -> None:
    """Map legacy free-text spellings onto the vocabulary; refuse the rest.

    One bounded scan per column; failures name up to 30 row ids so an
    operator can repair exactly the offending rows and retry the whole
    (still transactional) revision — the f2c3d4e5f6a7 preflight style.
    """
    op.execute(
        sa.text(
            f'UPDATE "{table}" SET "{column}" = {mapping_sql} '
            f'WHERE "{column}" IS NOT NULL AND "{column}" NOT IN ({vocabulary})'
        )
    )
    op.execute(
        sa.text(
            f"""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM "{table}"
                         WHERE "{column}" IS NOT NULL
                           AND "{column}" NOT IN ({vocabulary})) THEN
                RAISE EXCEPTION '{table}.{column} values outside the new vocabulary exist: %',
                  (SELECT string_agg(id::text, ', ') FROM (
                     SELECT id FROM "{table}"
                     WHERE "{column}" IS NOT NULL
                       AND "{column}" NOT IN ({vocabulary})
                     ORDER BY id LIMIT 30
                   ) offending);
              END IF;
            END $$;
            """
        )
    )


def upgrade() -> None:
    # Bound every lock wait: the normalization UPDATEs, the updated_at
    # NOT-NULL swap and the CHECK validations queue behind ordinary farm
    # traffic otherwise.
    op.execute(sa.text("SET LOCAL lock_timeout = '10s'"))
    for table in ("animals", "farms"):
        op.add_column(table, sa.Column("updated_at", sa.DateTime(), nullable=True))
        op.execute(f"UPDATE {table} SET updated_at = created_at WHERE updated_at IS NULL")
        op.alter_column(
            table,
            "updated_at",
            nullable=False,
            server_default=sa.text("timezone('UTC', now())"),
        )

    op.add_column("animals", sa.Column("coat_color", sa.String(length=20), nullable=True))
    op.add_column("animals", sa.Column("horned", sa.Boolean(), nullable=True))
    _add_validated_check(
        "ck_animals_coat_color",
        "animals",
        f"coat_color IS NULL OR coat_color IN ({_in_list(_COAT_COLOR_VALUES)})",
    )

    if context.is_offline_mode():
        op.execute(
            "-- WARNING: offline generation skipped the vocabulary normalization. "
            "Audit animals.disposal_method and health_events.route for unmappable "
            "values (or run 'alembic upgrade' online) before applying this script."
        )
    else:
        _normalize_or_refuse(
            "animals",
            "disposal_method",
            "CASE "
            "WHEN lower(disposal_method) ~ '(buried|burial|deep pit)' THEN 'DEEP_BURIAL' "
            "WHEN lower(disposal_method) ~ '(burn|incinerat)' THEN 'BURNING' "
            "WHEN lower(disposal_method) ~ 'render' THEN 'RENDERING' "
            "WHEN lower(disposal_method) ~ 'compost' THEN 'COMPOSTING' "
            "ELSE disposal_method END",
            _in_list(_DISPOSAL_METHOD_VALUES),
        )
        _normalize_or_refuse(
            "health_events",
            "route",
            "CASE "
            "WHEN upper(route) ~ 'SUBCUT|SC' THEN 'SC' "
            "WHEN upper(route) ~ 'INTRAMUSCULAR|\\mIM\\M' THEN 'IM' "
            "WHEN upper(route) ~ 'INTRAVENOUS|\\mIV\\M' THEN 'IV' "
            "WHEN upper(route) ~ 'ORAL|DRENCH|BOLUS' THEN 'ORAL' "
            "WHEN upper(route) ~ 'TOPICAL|POUR.?ON|SPRAY|DIP' THEN 'TOPICAL' "
            "WHEN upper(route) ~ 'INTRANASAL|NASAL' THEN 'INTRANASAL' "
            "ELSE route END",
            _in_list(_ROUTE_VALUES),
        )
        # Breed canonicalization: blank legacy rows take the default before
        # the non-empty CHECK lands (the service trims/case-normalizes writes).
        op.execute(
            "UPDATE animals SET breed = initcap(btrim(breed)) "
            "WHERE breed <> initcap(btrim(breed)) AND btrim(breed) <> ''"
        )
        op.execute("UPDATE animals SET breed = 'Osmanabadi' WHERE btrim(breed) = ''")

    _add_validated_check(
        "ck_animals_disposal_method",
        "animals",
        f"disposal_method IS NULL OR disposal_method IN ({_in_list(_DISPOSAL_METHOD_VALUES)})",
    )
    _add_validated_check(
        "ck_health_events_route",
        "health_events",
        f"route IS NULL OR route IN ({_in_list(_ROUTE_VALUES)})",
    )
    _add_validated_check("ck_animals_breed_nonempty", "animals", "btrim(breed) <> ''")


def downgrade() -> None:
    op.drop_constraint("ck_animals_breed_nonempty", "animals", type_="check")
    op.drop_constraint("ck_health_events_route", "health_events", type_="check")
    op.drop_constraint("ck_animals_disposal_method", "animals", type_="check")
    op.drop_column("animals", "horned")
    op.drop_constraint("ck_animals_coat_color", "animals", type_="check")
    op.drop_column("animals", "coat_color")
    op.drop_column("farms", "updated_at")
    op.drop_column("animals", "updated_at")
