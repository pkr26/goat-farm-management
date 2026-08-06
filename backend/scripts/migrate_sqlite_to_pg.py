"""One-shot migration: v1 SQLite database (goatfarm.db) → v2 PostgreSQL backend.

Wipes every table known to Base.metadata (TRUNCATE ... RESTART IDENTITY
CASCADE) and reloads it from the SQLite file in a single transaction, then
resets each serial id sequence past the migrated MAX(id). Idempotent: safe to
re-run. `alembic_version` is not in Base.metadata and is left untouched.

Dry-run by default; pass --yes to write:

    python scripts/migrate_sqlite_to_pg.py [sqlite_path] \
        [--database-url postgresql+asyncpg://host:5432/dbname] [--yes]
"""

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Table, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.types import TypeEngine

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

import app.models  # noqa: E402, F401  (registers all tables on Base.metadata)
from app.core.config import get_settings  # noqa: E402
from app.db import Base  # noqa: E402

# Seeded on startup by the new backend too; the SQLite copies win because we
# TRUNCATE first (user edits live in the v1 database).
REFERENCE_TABLES = (
    "bucket_definitions",
    "vaccine_templates",
    "feed_recipes",
    "feed_recipe_lines",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "sqlite_path",
        nargs="?",
        default=str(REPO_ROOT / "goatfarm.db"),
        help="path to the v1 SQLite database (default: goatfarm.db at repo root)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="target PostgreSQL URL (default: GOATFARM_DATABASE_URL env or app settings)",
    )
    parser.add_argument("--yes", action="store_true", help="actually write (default is dry-run)")
    return parser.parse_args(argv)


def read_sqlite(sqlite_path: Path) -> dict[str, list[dict[str, Any]]]:
    """SELECT * per table (in FK order) into plain dicts."""
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        return {
            table.name: [
                dict(row) for row in conn.execute(f'SELECT * FROM "{table.name}"').fetchall()
            ]
            for table in Base.metadata.sorted_tables
        }
    finally:
        conn.close()


def convert_value(column_type: TypeEngine[Any], value: Any) -> Any:
    """Adapt SQLite storage types to the PG column type. None stays None."""
    if value is None:
        return None
    if isinstance(column_type, Boolean):
        return bool(value)  # SQLite stores booleans as 0/1 integers
    if isinstance(column_type, DateTime):
        # SQLite text like 'YYYY-MM-DD HH:MM:SS.ffffff'; fromisoformat (3.11+)
        # accepts both the space and the 'T' separator.
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(column_type, Date):
        return date.fromisoformat(value) if isinstance(value, str) else value
    return value


def convert_row(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: convert_value(table.c[key].type, value) for key, value in row.items() if key in table.c
    }


def has_serial_id(table: Table) -> bool:
    col = table.c.get("id")
    return col is not None and col.primary_key and isinstance(col.type, Integer)


async def migrate(url: str, data: dict[str, list[dict[str, Any]]]) -> None:
    """Wipe-and-load in ONE transaction; rollback (and raise) on any error."""
    engine = create_async_engine(url)
    try:
        tables = Base.metadata.sorted_tables
        quoted_names = ", ".join(f'"{t.name}"' for t in tables)
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {quoted_names} RESTART IDENTITY CASCADE"))
            for table in tables:
                for row in data[table.name]:
                    await conn.execute(table.insert().values(**convert_row(table, row)))
            for table in tables:
                if has_serial_id(table):
                    await conn.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                            f'GREATEST(COALESCE((SELECT MAX(id) FROM "{table.name}"), 0) + 1, 1))'
                        )
                    )
    finally:
        await engine.dispose()


async def verify(url: str, data: dict[str, list[dict[str, Any]]]) -> bool:
    """Compare per-table row counts SQLite vs PG; print the report."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            ok = True
            print(f"\n{'table':<24} {'sqlite':>8} {'postgres':>8}  result")
            print("-" * 54)
            for table in Base.metadata.sorted_tables:
                sqlite_count = len(data[table.name])
                pg_count = (
                    await conn.execute(text(f'SELECT COUNT(*) FROM "{table.name}"'))
                ).scalar_one()
                match = sqlite_count == pg_count
                ok = ok and match
                note = " (reference rows from SQLite)" if table.name in REFERENCE_TABLES else ""
                print(
                    f"{table.name:<24} {sqlite_count:>8} {pg_count:>8}  "
                    f"{'OK' if match else 'MISMATCH'}{note}"
                )
            print("-" * 54)
            print(f"reference tables ({', '.join(REFERENCE_TABLES)}) were reloaded from SQLite;")
            print("startup-seeded copies were replaced (user edits live in the SQLite data).")
            try:
                version = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one_or_none()
                print(f"alembic_version: {version} (untouched)")
            except Exception:
                print("alembic_version: table not found (untouched)")
            return ok
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sqlite_path = Path(args.sqlite_path).resolve()
    url = (
        args.database_url or os.environ.get("GOATFARM_DATABASE_URL") or get_settings().database_url
    )
    if not sqlite_path.is_file():
        print(f"ERROR: SQLite database not found: {sqlite_path}", file=sys.stderr)
        return 2

    data = read_sqlite(sqlite_path)
    total = sum(len(rows) for rows in data.values())
    print(f"source : {sqlite_path}")
    print(f"target : {url}")
    print(f"tables : {len(data)} (FK order from Base.metadata.sorted_tables)")
    for table_name, rows in data.items():
        print(f"  {table_name:<24} {len(rows):>6} rows")
    print(f"  {'TOTAL':<24} {total:>6} rows")

    if not args.yes:
        print("\ndry-run only — re-run with --yes to write.")
        return 0

    print("\nwriting (TRUNCATE + reload in one transaction)…")
    try:
        asyncio.run(migrate(url, data))
    except Exception as exc:
        print(
            f"ERROR: migration failed; transaction rolled back, target unchanged: {exc}",
            file=sys.stderr,
        )
        return 1
    print("committed.")

    ok = asyncio.run(verify(url, data))
    if not ok:
        print("ERROR: row-count mismatch — see report above.", file=sys.stderr)
        return 1
    print("verification passed: all row counts match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
