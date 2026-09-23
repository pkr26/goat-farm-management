#!/usr/bin/env python3
"""Break-glass: drop the lost second factor from exactly one account.

Run this when BOTH the authenticator device and every unused recovery code
are lost — the documented account-recovery path of last resort. It runs
against the privileged migration URL (``GOATFARM_MIGRATION_DATABASE_URL``,
falling back to ``GOATFARM_DATABASE_URL`` in development) because the
production Compose file has no ``db`` service by design (external PostgreSQL)
and the backend image deliberately ships no ``psql``. Dry-run by default;
``--apply`` performs the reset:

    cd backend
    .venv/bin/python scripts/totp_breakglass.py --email owner@example.in
    .venv/bin/python scripts/totp_breakglass.py --email owner@example.in --apply

In the production Compose topology, borrow the one-shot ``migrate`` service —
the only container carrying the DDL-role URL and the database CA:

    docker compose --env-file /secure/goatfarm.production.env \\
      -f docker-compose.production.yml run --rm --no-deps migrate \\
      python scripts/totp_breakglass.py --email owner@example.in --apply

The ``token_version`` bump signs out every existing session (bearer tokens,
refresh cookies and any in-flight login challenge), so the reset itself
cannot be ridden by a stolen credential. Re-enroll immediately after signing
back in — a factor-less account has no second factor until then.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

# This is backend/ when run from a checkout and /app when copied into the
# production image, so the same command works in either operational context.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import MigrationSettings, get_migration_settings  # noqa: E402
from app.db import database_ssl_connect_arg  # noqa: E402
from app.models import TotpRecoveryCode, User  # noqa: E402


def _engine_for(settings: MigrationSettings) -> AsyncEngine:
    """Mirror alembic/env.py's engine: the privileged URL plus wire TLS.

    No API settings are imported — like the Alembic release job, this is a
    one-shot maintenance process that must not need cookie/JWT secrets.
    """
    return create_async_engine(
        settings.migration_database_url or settings.database_url,
        connect_args={
            "ssl": database_ssl_connect_arg(settings),
            "server_settings": {"statement_timeout": str(settings.migration_statement_timeout_ms)},
        },
    )


async def _breakglass(email: str, *, apply: bool) -> int:
    settings = get_migration_settings()
    engine = _engine_for(settings)
    sessions = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    normalized = email.strip().lower()
    try:
        async with sessions() as db:
            # Lock the row before deciding: the whole point of break-glass is
            # running during a live incident, and a concurrent disable/re-enroll
            # must order against this reset rather than interleave with it.
            matches = list(
                (
                    await db.execute(
                        select(User)
                        .where(func.lower(User.email) == normalized)
                        .order_by(User.id)
                        .with_for_update(of=User)
                    )
                )
                .scalars()
                .all()
            )
            live = [user for user in matches if user.deleted_at is None]
            if not live:
                print(f"No account matches email {email!r}.", file=sys.stderr)
                return 2
            if len(live) > 1:
                ids = ", ".join(str(user.id) for user in live)
                print(
                    f"Multiple live accounts match {email!r} (user ids {ids}); "
                    "refusing an ambiguous reset.",
                    file=sys.stderr,
                )
                return 2
            user = live[0]
            unused_codes = (
                await db.execute(
                    select(func.count())
                    .select_from(TotpRecoveryCode)
                    .where(
                        TotpRecoveryCode.user_id == user.id,
                        TotpRecoveryCode.used_at.is_(None),
                    )
                )
            ).scalar_one()
            print(
                f"account: user_id={user.id} email={user.email} "
                f"totp_state={user.totp_state!r} unused_recovery_codes={unused_codes}"
            )
            has_second_factor = (
                user.totp_state is not None or user.totp_secret_enc is not None or unused_codes > 0
            )
            if not has_second_factor:
                print("No second factor present — nothing to break.")
                return 0
            if not apply:
                print(
                    "Dry run only: rerun with --apply to drop the second factor "
                    "(all sessions of this account will be signed out).",
                    file=sys.stderr,
                )
                # A dry run that finds break-glass work is not a success
                # result: automation that treats 0 as "recovered" would strand
                # the operator mid-incident (same convention as
                # rekey_totp_secrets.py).
                return 2
            reset = cast(
                CursorResult[Any],
                await db.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(
                        totp_secret_enc=None,
                        totp_state=None,
                        totp_last_step=None,
                        token_version=User.token_version + 1,
                    )
                ),
            )
            wiped_codes = cast(
                CursorResult[Any],
                await db.execute(
                    delete(TotpRecoveryCode).where(TotpRecoveryCode.user_id == user.id)
                ),
            )
            await db.commit()
            print(
                f"applied: second factor dropped for user_id={user.id} "
                f"(users rows updated={reset.rowcount}, recovery codes "
                f"deleted={wiped_codes.rowcount}); token_version bumped — every "
                "session of this account is now signed out. Re-enroll TOTP "
                "immediately after signing back in."
            )
            return 0
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Break-glass reset: drop the lost TOTP second factor from one "
            "account via the migration database URL."
        )
    )
    parser.add_argument("--email", required=True, help="exact account email to recover")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the reset; without this flag the command is a dry-run",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_breakglass(args.email, apply=args.apply))
    except (OSError, ValueError, RuntimeError) as exc:
        # Configuration/URL/TLS problems are operator-actionable: print the
        # cause instead of a traceback mid-incident.
        print(f"TOTP break-glass did not start: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
