"""Alembic environment — async (asyncpg) engine, URL from app settings.

Zero-downtime posture:
- ``lock_timeout`` is set on the migration connection so a DDL statement
  that can't grab its lock fails fast instead of queueing behind (or
  stalling) live traffic.
- ``statement_timeout`` comes from ``migration_statement_timeout_ms`` (default
  0 = unbounded), never from the API's request-path backstop. DDL is not OLTP:
  a table rewrite, a constraint validation, or a CREATE INDEX CONCURRENTLY —
  which waits twice for every concurrent transaction to drain — legitimately
  runs for minutes, and cancelling one mid-flight aborts the release and can
  leave an invalid index behind.
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
from app.core.config import get_migration_settings
from app.db import Base, database_ssl_connect_arg
from app.models import *  # noqa: F403 — register all tables on Base.metadata

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers=False: the default True silences every logger
    # the host process (or the app's own logging config) had already wired —
    # running `alembic` from a shell that imported app logging then lost all
    # its output (P3, 2026-09-20 audit).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
# DELIBERATELY no naming_convention retrofit on this MetaData (2026-09-20
# audit P3, investigated and rejected): early revisions create tables with
# UNNAMED ForeignKeyConstraints and later revisions drop them by their
# PostgreSQL default names (e.g. farm_memberships_user_id_fkey). Alembic's
# runtime op.create_table resolves unnamed constraint names through this
# metadata, so attaching a convention renames those constraints at replay
# time and aborts the chain with UndefinedObjectError — verified on a fresh
# database. A convention can only arrive together with a chain-wide rename
# revision (or a from-scratch chain), not as a retrofit.

# DDL that can't acquire its lock within this window aborts the migration
# (loud, retryable) rather than piling up blocked sessions behind it.
LOCK_TIMEOUT = "10s"
# Serialize every supported schema writer with restore.sh. A session lock
# survives the SET transaction commit below and is released automatically when
# this one-shot Alembic connection closes, including after a failed migration.
RELEASE_WRITER_ADVISORY_LOCK_ID = 718204614


def run_migrations_offline() -> None:
    settings = get_migration_settings()
    context.configure(
        url=settings.migration_database_url or settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_server_default=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    settings = get_migration_settings()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.migration_database_url or settings.database_url
    # Alembic is a separate engine from app.db. Carry the same wire-TLS
    # guarantee across instead of silently falling back to the asyncpg/libpq
    # default during the most privileged database operation. The per-statement
    # budget is migration-specific (see the module docstring).
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        connect_args={
            "ssl": database_ssl_connect_arg(settings),
            "server_settings": {"statement_timeout": str(settings.migration_statement_timeout_ms)},
        },
    )
    try:
        async with connectable.connect() as connection:
            release_lock_acquired = await connection.scalar(
                text(f"SELECT pg_try_advisory_lock({RELEASE_WRITER_ADVISORY_LOCK_ID})")
            )
            if release_lock_acquired is not True:
                raise RuntimeError(
                    "Another supported migration or restore writer owns the target database"
                )
            # Session-level SET autobegins a transaction in SQLAlchemy 2.0 —
            # commit it, or Alembic would join that outer transaction and every
            # migration would roll back when the connection closes.
            await connection.execute(text(f"SET lock_timeout = '{LOCK_TIMEOUT}'"))
            await connection.commit()
            await connection.run_sync(do_run_migrations)
    finally:
        # Physically close the pooled connection on every failure path too, so
        # the session advisory lock never survives in an embedded invocation.
        await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
