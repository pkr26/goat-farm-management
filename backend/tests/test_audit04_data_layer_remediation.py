"""Round-trip coverage for the 2026-10-01 audit 04 data-layer remediation.

The out-of-band-writer parity revision (e7b9d1f3a5c2, audit findings 04-2
and the NotificationRecipient Info item) is exercised on throwaway
databases exactly like the other migration suites:

* the screening_images BEFORE UPDATE trigger refreshes ``updated_at`` for
  raw-SQL writers while preserving statement-supplied timestamps (the
  ORM/Core ``onupdate`` contract), and the downgrade removes precisely
  that behavior;
* the notification_recipients opt-in booleans accept a direct-SQL INSERT
  without the flag list once the ``DEFAULT false`` backfill lands, and
  refuse it again after downgrade.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

import asyncpg  # type: ignore[import-untyped]
import pytest

from .test_ops_migration_integrity import _admin, _alembic, _throwaway_name

PARENT_REVISION = "c1d3e5f7a9b4"
PARITY_REVISION = "e7b9d1f3a5c2"

OLD_UPDATED_AT = datetime(2026, 1, 1, 12, 0)
EXPLICIT_UPDATED_AT = datetime(2025, 6, 1, 8, 30)


async def _insert_screening_image(database_url: str) -> int:
    connection = await asyncpg.connect(database_url)
    try:
        user_id = await connection.fetchval(
            """
            INSERT INTO users (email, password_hash, created_at)
            VALUES ('audit04-trigger@example.test', 'not-used', timezone('UTC', now()))
            RETURNING id
            """
        )
        farm_id = await connection.fetchval(
            """
            INSERT INTO farms (name, owner_id, created_at)
            VALUES ('Audit04 trigger farm', $1, timezone('UTC', now()))
            RETURNING id
            """,
            user_id,
        )
        batch_id = await connection.fetchval(
            """
            INSERT INTO screening_batches (farm_id, created_by_id, created_at)
            VALUES ($1, $2, timezone('UTC', now()))
            RETURNING id
            """,
            farm_id,
            user_id,
        )
        return int(
            await connection.fetchval(
                """
                INSERT INTO screening_images (
                  farm_id, batch_id, s3_bucket, s3_key, status, error, updated_at
                ) VALUES ($1, $2, 'audit04-bucket', 'raw/1/2026-01-01/pen-a.jpg',
                          'ERROR', 'seed failure', $3)
                RETURNING id
                """,
                farm_id,
                batch_id,
                OLD_UPDATED_AT,
            )
        )
    finally:
        await connection.close()


async def _image_updated_at(database_url: str, image_id: int) -> datetime | None:
    connection = await asyncpg.connect(database_url)
    try:
        return cast(
            "datetime | None",
            await connection.fetchval(
                "SELECT updated_at FROM screening_images WHERE id = $1", image_id
            ),
        )
    finally:
        await connection.close()


async def test_screening_images_updated_at_trigger_round_trip() -> None:
    """Raw SQL must not freeze the pipeline's lease/retry marker (04-2).

    Before the revision a raw UPDATE left ``updated_at`` untouched — the
    exact out-of-band-writer gap the audit flagged for the one ``updated_at``
    column that both feeds staleness logic and receives direct SQL in
    production. After it, an UPDATE that does not set the column gets the
    same UTC wall clock the server_default uses, while a statement-supplied
    timestamp (what ORM ``onupdate`` and the lease touch emit) wins verbatim.
    """
    database = _throwaway_name("audit04_trigger")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    try:
        await _alembic(database, "upgrade", PARENT_REVISION)
        image_id = await _insert_screening_image(database_url)

        # Pre-fix behavior: a raw UPDATE leaves updated_at frozen.
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(
                """
                UPDATE screening_images
                SET error = 'audit04 pre-fix raw write'
                WHERE id = $1
                """,
                image_id,
            )
        finally:
            await connection.close()
        assert await _image_updated_at(database_url, image_id) == OLD_UPDATED_AT

        await _alembic(database, "upgrade", PARITY_REVISION)

        # The trigger refreshes the marker for the same raw write.
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(
                """
                UPDATE screening_images
                SET error = 'audit04 post-fix raw write'
                WHERE id = $1
                """,
                image_id,
            )
        finally:
            await connection.close()
        bumped = await _image_updated_at(database_url, image_id)
        assert bumped is not None and bumped > OLD_UPDATED_AT

        # A statement-supplied timestamp (the ORM/onupdate shape) is the
        # writer's decision, never the trigger's.
        connection = await asyncpg.connect(database_url)
        try:
            await connection.execute(
                """
                UPDATE screening_images
                SET error = 'audit04 explicit timestamp',
                    updated_at = $2
                WHERE id = $1
                """,
                image_id,
                EXPLICIT_UPDATED_AT,
            )
        finally:
            await connection.close()
        assert await _image_updated_at(database_url, image_id) == EXPLICIT_UPDATED_AT

        # Downgrade removes exactly the trigger (+ function); the raw write
        # freezes the marker again, as before the revision.
        await _alembic(database, "downgrade", PARENT_REVISION)
        connection = await asyncpg.connect(database_url)
        try:
            triggers = await connection.fetchval(
                """
                SELECT count(*) FROM pg_trigger
                WHERE tgname = 'trg_screening_images_updated_at_refresh'
                """
            )
            functions = await connection.fetchval(
                """
                SELECT count(*) FROM information_schema.routines
                WHERE routine_name = 'refresh_screening_images_updated_at'
                """
            )
            await connection.execute(
                """
                UPDATE screening_images
                SET error = 'audit04 downgraded raw write'
                WHERE id = $1
                """,
                image_id,
            )
            downgraded_updated_at = await connection.fetchval(
                "SELECT updated_at FROM screening_images WHERE id = $1", image_id
            )
        finally:
            await connection.close()
        assert triggers == 0
        assert functions == 0
        assert downgraded_updated_at == EXPLICIT_UPDATED_AT
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')


async def _insert_recipient_farm_and_membership(database_url: str) -> tuple[int, int]:
    connection = await asyncpg.connect(database_url)
    try:
        user_id = await connection.fetchval(
            """
            INSERT INTO users (email, password_hash, created_at)
            VALUES ('audit04-recipient@example.test', 'not-used', timezone('UTC', now()))
            RETURNING id
            """
        )
        farm_id = await connection.fetchval(
            """
            INSERT INTO farms (name, owner_id, created_at)
            VALUES ('Audit04 recipient farm', $1, timezone('UTC', now()))
            RETURNING id
            """,
            user_id,
        )
        role_id = await connection.fetchval(
            """
            INSERT INTO roles (farm_id, name, permissions, created_at)
            VALUES ($1, 'Audit04 role', '[]', timezone('UTC', now()))
            RETURNING id
            """,
            farm_id,
        )
        membership_id = await connection.fetchval(
            """
            INSERT INTO farm_memberships (farm_id, user_id, role_id, is_active)
            VALUES ($1, $2, $3, true)
            RETURNING id
            """,
            farm_id,
            user_id,
            role_id,
        )
        return farm_id, membership_id
    finally:
        await connection.close()


async def test_notification_recipients_opt_in_server_defaults_round_trip() -> None:
    """Direct-SQL INSERT symmetry for the opt-in booleans (04-Info).

    At the parent revision five of the six flags are NOT NULL without a
    server default, so a raw INSERT supplying only the phone fails. The
    revision backfills ``DEFAULT false`` — matching the ORM's Python
    defaults and the movement_restriction precedent — and the downgrade
    removes them again.
    """
    database = _throwaway_name("audit04_recipients")
    await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    await _admin(f'CREATE DATABASE "{database}"')
    database_url = f"postgresql://localhost:5432/{database}"
    try:
        await _alembic(database, "upgrade", PARENT_REVISION)
        farm_id, membership_id = await _insert_recipient_farm_and_membership(database_url)

        connection = await asyncpg.connect(database_url)
        try:
            with pytest.raises(asyncpg.NotNullViolationError):
                await connection.execute(
                    """
                    INSERT INTO notification_recipients (farm_id, membership_id, phone)
                    VALUES ($1, $2, '+919888877777')
                    """,
                    farm_id,
                    membership_id,
                )
        finally:
            await connection.close()

        await _alembic(database, "upgrade", PARITY_REVISION)
        connection = await asyncpg.connect(database_url)
        try:
            recipient_id = await connection.fetchval(
                """
                INSERT INTO notification_recipients (farm_id, membership_id, phone)
                VALUES ($1, $2, '+919888877777')
                RETURNING id
                """,
                farm_id,
                membership_id,
            )
            row = await connection.fetchrow(
                """
                SELECT daily_digest, screening_flags, kidding_watch,
                       overdue_critical, feed_reorder, movement_restriction,
                       verified
                FROM notification_recipients WHERE id = $1
                """,
                recipient_id,
            )
        finally:
            await connection.close()
        assert row is not None
        flags = dict(row)
        assert set(flags) == {
            "daily_digest",
            "screening_flags",
            "kidding_watch",
            "overdue_critical",
            "feed_reorder",
            "movement_restriction",
            "verified",
        }
        assert set(flags.values()) == {False}

        await _alembic(database, "downgrade", PARENT_REVISION)
        connection = await asyncpg.connect(database_url)
        try:
            with pytest.raises(asyncpg.NotNullViolationError):
                await connection.execute(
                    """
                    INSERT INTO notification_recipients (farm_id, membership_id, phone)
                    VALUES ($1, $2, '+919800000000')
                    """,
                    farm_id,
                    membership_id,
                )
            column_defaults = {
                row["column_name"]: row["column_default"]
                for row in await connection.fetch(
                    """
                    SELECT column_name, column_default
                    FROM information_schema.columns
                    WHERE table_name = 'notification_recipients'
                      AND column_name = ANY($1::text[])
                    """,
                    [
                        "daily_digest",
                        "screening_flags",
                        "kidding_watch",
                        "overdue_critical",
                        "feed_reorder",
                        "verified",
                    ],
                )
            }
        finally:
            await connection.close()
        assert set(column_defaults) == {
            "daily_digest",
            "screening_flags",
            "kidding_watch",
            "overdue_critical",
            "feed_reorder",
            "verified",
        }
        assert all(default is None for default in column_defaults.values())
    finally:
        await _admin(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
