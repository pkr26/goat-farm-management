"""Alembic environment — async (asyncpg) engine, URL from app settings.

Zero-downtime posture:
- ``lock_timeout`` is set on the migration connection so a DDL statement
  that can't grab its lock fails fast instead of queueing behind (or
  stalling) live traffic.
- Migrations run inside a transaction (safe DDL rollback). A future
  revision that adds an index to a large/hot table should instead build it
  with ``op.create_index(..., postgresql_concurrently=True)`` from a
  revision that calls ``context.configure(transactional_ddl=False)`` —
  CREATE INDEX CONCURRENTLY cannot run inside a transaction. Existing
  revisions keep plain create_index: they already run on every provisioned
  database and their tables are small enough that builds take milliseconds.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings
from app.db import Base
from app.models import *  # noqa: F403 — register all tables on Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# DDL that can't acquire its lock within this window aborts the migration
# (loud, retryable) rather than piling up blocked sessions behind it.
LOCK_TIMEOUT = "10s"


def run_migrations_offline() -> None:
    settings = get_settings()
    context.configure(
        url=settings.migration_database_url or settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    settings = get_settings()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.migration_database_url or settings.database_url
    # Alembic is a separate engine from app.db. Carry the same wire-TLS and
    # statement-timeout guarantees across instead of silently falling back to
    # asyncpg/libpq defaults during the most privileged database operation.
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        connect_args={
            "ssl": settings.db_sslmode,
            "server_settings": {"statement_timeout": str(settings.db_statement_timeout_ms)},
        },
    )
    async with connectable.connect() as connection:
        # Session-level SET autobegins a transaction in SQLAlchemy 2.0 —
        # commit it, or Alembic would join that outer transaction and every
        # migration would roll back when the connection closes.
        await connection.execute(text(f"SET lock_timeout = '{LOCK_TIMEOUT}'"))
        await connection.commit()
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
