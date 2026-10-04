"""Alembic environment — async (asyncpg) engine, URL from app settings.

Zero-downtime posture:
- ``lock_timeout`` is set on the migration connection so a DDL statement
  that can't grab its lock fails fast instead of queueing behind (or
  stalling) live traffic.
- ``statement_timeout`` comes from ``migration_statement_timeout_ms`` (default
  15 minutes), never from the API's request-path backstop. DDL is not OLTP:
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
from contextlib import suppress
from logging.config import fileConfig

from alembic.script.revision import RangeNotAncestorError, RevisionError
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context, util
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

# f9 takes an EXCLUSIVE findings lock while preserving legacy review evidence.
# fd adds/backfills privacy-cleanup state across the hot screening image queue,
# replaces its lease-refresh trigger, and validates new invariants. A populated
# upgrade crossing either step must be a deliberate write-quiesced cutover.
QUIESCENCE_REQUIRED_REVISIONS = frozenset({"f9a3b7c1d5e2", "fd4e5f6a7b8c"})

# These applied revisions consume live query results. Preserve their exact
# preflights and immutable history: offline ranges crossing them are explicitly
# online-only until an equivalent, independently reviewed SQL adapter exists.
ONLINE_ONLY_OFFLINE_STEPS = {
    "upgrade": frozenset({"c3d4e5f6a7b1", "cad1e2f3a4b5"}),
    "downgrade": frozenset({"d4e5f6a7b8c9", "f3d4e5f6a7b8"}),
}


def preflight_offline_range() -> None:
    """Reject unsupported historical walks before emitting any SQL."""
    start = context.get_starting_revision_argument() or "base"
    destination = context.get_revision_argument() or "base"
    try:
        revisions = list(context.script.iterate_revisions(destination, start))
        direction = "upgrade"
    except RangeNotAncestorError:
        revisions = list(context.script.iterate_revisions(start, destination))
        direction = "downgrade"
    blocked = sorted(
        revision.revision
        for revision in revisions
        if revision.revision in ONLINE_ONLY_OFFLINE_STEPS[direction]
    )
    if blocked:
        raise util.CommandError(
            f"Offline {direction} range is online-only because applied revision(s) "
            f"{', '.join(blocked)} require live-data preflights. No SQL was emitted. "
            "Rehearse and run this range online against a disposable restored "
            "database; see README's offline migration support boundary."
        )


def run_migrations_offline() -> None:
    settings = get_migration_settings()
    preflight_offline_range()
    context.configure(
        url=settings.migration_database_url or settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_server_default=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    settings = get_migration_settings()
    if not settings.migration_database_url:
        raise util.CommandError(
            "Online migrations require an explicit GOATFARM_MIGRATION_DATABASE_URL "
            "(or GOATFARM_MIGRATION_DATABASE_URL_FILE); refusing the implicit "
            "development database target"
        )
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.migration_database_url
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
            marker_exists = await connection.scalar(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            )
            current_revisions: tuple[str, ...] = ()
            if marker_exists:
                current_revisions = tuple(
                    (
                        await connection.execute(
                            text("SELECT version_num FROM alembic_version ORDER BY version_num")
                        )
                    ).scalars()
                )
            # ``get_revision_argument`` resolves symbolic targets such as
            # ``head`` to concrete revision ids.  Do not silently substitute
            # the repository head here: operators and migration regression
            # tests routinely walk to an intermediate revision, and a
            # preflight for a *later* migration must not inspect columns that
            # do not exist at that requested stop point.  Commands such as
            # ``current``/``check`` have no destination and therefore have no
            # migration range to preflight.
            # Inspection/autogenerate commands (notably ``current`` and
            # ``check``) do not install ``destination_rev`` in Alembic's
            # EnvironmentContext at all.  ``get_revision_argument()`` raises
            # KeyError in that case; it does not return None.  Treat the
            # missing option as an inspection with no migration range, while
            # preserving None as Alembic's resolved spelling of ``base``.
            try:
                requested_target = context.get_revision_argument()
            except KeyError:
                requested_target = None
            if requested_target is None:
                target_revisions: tuple[str, ...] = ()
            elif isinstance(requested_target, tuple):
                target_revisions = requested_target
            else:
                target_revisions = (requested_target,)
            parsed_target = make_url(settings.migration_database_url)
            host = parsed_target.host or "local-socket"
            port = f":{parsed_target.port}" if parsed_target.port is not None else ""
            database = parsed_target.database or "<missing-database>"
            util.msg(
                "Migration target "
                f"{host}{port}/{database}; current="
                f"{','.join(current_revisions) if current_revisions else 'base'}; "
                f"target={','.join(target_revisions) if target_revisions else 'base/inspection'}"
            )

            pending: set[str] = set()
            if requested_target is not None:
                # A downgrade (or branch-to-branch walk) has no
                # upgrade-only write-quiescence step pending.
                # Absolute downgrades raise RangeNotAncestorError here.
                # Relative downgrades (``-1``, ``-2``) are parsed as an
                # impossible relative *upgrade* by iterate_revisions and raise
                # RevisionError instead.  In either case no upgrade-only
                # quiescence step is pending; Alembic's actual command
                # callback validates and executes the downgrade afterwards.
                with suppress(RangeNotAncestorError, RevisionError):
                    pending = {
                        candidate.revision
                        for candidate in context.script.iterate_revisions(
                            requested_target, current_revisions or "base"
                        )
                    }
            quiescence_steps = sorted(pending & QUIESCENCE_REQUIRED_REVISIONS)
            # A brand-new database has no application writers to drain. Any
            # populated/previously stamped database crossing a flagged step
            # needs an explicit release-procedure acknowledgement.
            if current_revisions and quiescence_steps:
                findings_exists = await connection.scalar(
                    text("SELECT to_regclass('public.screening_findings') IS NOT NULL")
                )
                review_revision_exists = False
                if findings_exists:
                    review_revision_exists = bool(
                        await connection.scalar(
                            text(
                                "SELECT EXISTS ("
                                "SELECT 1 FROM pg_attribute "
                                "WHERE attrelid = 'public.screening_findings'::regclass "
                                "AND attname = 'review_revision' "
                                "AND attnum > 0 AND NOT attisdropped)"
                            )
                        )
                    )
                legacy_rows: int | None = None
                if review_revision_exists:
                    legacy_rows = int(
                        await connection.scalar(
                            text(
                                "SELECT count(*) FROM screening_findings "
                                "WHERE review_revision = 0 "
                                "AND status IN ('CONFIRMED', 'REJECTED') "
                                "AND reviewed_by_id IS NOT NULL AND reviewed_at IS NOT NULL"
                            )
                        )
                        or 0
                    )
                util.msg(
                    "Write-quiescence migration rehearsal: revisions="
                    f"{','.join(quiescence_steps)} eligible_legacy_review_rows="
                    f"{legacy_rows if legacy_rows is not None else 'not-yet-queryable'}"
                )
                if not settings.migration_writes_quiesced:
                    raise util.CommandError(
                        "Pending migration(s) "
                        f"{', '.join(quiescence_steps)} require application writes to be "
                        "drained. Rehearse against a restored disposable database, stop "
                        "API/worker writers, then set GOATFARM_MIGRATION_WRITES_QUIESCED=true "
                        "for the one-shot migration job."
                    )
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
