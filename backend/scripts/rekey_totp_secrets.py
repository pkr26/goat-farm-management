#!/usr/bin/env python3
"""Rewrap TOTP secrets under the current independent encryption key.

Run this while the JWT signer that encrypted any legacy raw TOTP rows is
still active, and before its signing-key cutover:

    cd backend
    .venv/bin/python scripts/rekey_totp_secrets.py          # inventory / cutover check
    .venv/bin/python scripts/rekey_totp_secrets.py --apply  # write v2 rows

The script never needs an old JWT private key after the migration. It reads
legacy raw rows only via the currently configured signer-derived v1 reader,
and rewrites them (or a v2 predecessor-key row) with the current stable TOTP
key. It is safe to re-run: already-current rows are counted as unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# This is backend/ when run from a checkout and /app when copied into the
# production image, so the same command works in either operational context.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.db import create_engine  # noqa: E402
from app.models import User  # noqa: E402
from app.security import (  # noqa: E402
    TotpSecretUnavailableError,
    decrypt_totp_secret_with_metadata,
    encrypt_totp_secret,
    validate_jwt_keypair,
)

UNAVAILABLE_PREVIEW_LIMIT = 20


@dataclass
class RekeyTotals:
    scanned: int = 0
    rekeyed: int = 0
    unchanged: int = 0
    unavailable_count: int = 0
    unavailable_preview_ids: list[int] = field(default_factory=list)

    def record_unavailable(self, user_id: int) -> None:
        """Count every failed row while retaining only bounded diagnostics."""
        self.unavailable_count += 1
        if len(self.unavailable_preview_ids) < UNAVAILABLE_PREVIEW_LIMIT:
            self.unavailable_preview_ids.append(user_id)


async def _rekey(*, apply: bool, batch_size: int) -> RekeyTotals:
    settings = get_settings()
    if settings.totp_encryption_key is None:
        raise RuntimeError("GOATFARM_TOTP_ENCRYPTION_KEY is required before running this migration")

    # A raw pre-v2 row can only be opened by the currently configured active
    # signer. Validate it up front so a broken key mount fails before any
    # partial work is committed. Do this before a JWT cutover; no old private
    # key should be retained by the long-running API just for TOTP recovery.
    validate_jwt_keypair()

    engine = create_engine(settings)
    sessions = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
    totals = RekeyTotals()
    after_id = 0
    try:
        while True:
            async with sessions() as db:
                query = (
                    select(User)
                    .where(User.id > after_id, User.totp_secret_enc.is_not(None))
                    .order_by(User.id)
                    .limit(batch_size)
                )
                # This is a maintenance operation, not an opportunistic
                # background worker: wait for any concurrent TOTP operation
                # rather than silently skipping a row past the keyset cursor.
                if apply:
                    query = query.with_for_update()
                rows = list((await db.execute(query)).scalars())
                if not rows:
                    break
                after_id = rows[-1].id
                for user in rows:
                    totals.scanned += 1
                    encrypted = user.totp_secret_enc
                    if encrypted is None:  # defensive: selected non-null above
                        continue
                    try:
                        decrypted = decrypt_totp_secret_with_metadata(encrypted)
                    except TotpSecretUnavailableError:
                        totals.record_unavailable(user.id)
                        continue
                    if decrypted.needs_rewrap:
                        totals.rekeyed += 1
                        if apply:
                            user.totp_secret_enc = encrypt_totp_secret(decrypted.secret)
                    else:
                        totals.unchanged += 1
                if apply:
                    await db.commit()
                else:
                    await db.rollback()
    finally:
        await engine.dispose()
    return totals


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory or rekey stored TOTP secrets under the current stable key."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform writes; without this flag the command is a dry-run inventory",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=250,
        choices=range(1, 1001),
        metavar="1..1000",
        help="locked rows per transaction (default: 250)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        totals = asyncio.run(_rekey(apply=args.apply, batch_size=args.batch_size))
    except RuntimeError as exc:
        print(f"TOTP rekey did not start: {exc}", file=sys.stderr)
        return 2
    mode = "applied" if args.apply else "dry-run"
    print(
        f"TOTP rekey {mode}: scanned={totals.scanned} rekeyed={totals.rekeyed} "
        f"unchanged={totals.unchanged} unavailable={totals.unavailable_count}"
    )
    if totals.unavailable_count:
        preview = ", ".join(str(user_id) for user_id in totals.unavailable_preview_ids)
        suffix = (
            f" (first {UNAVAILABLE_PREVIEW_LIMIT})"
            if totals.unavailable_count > UNAVAILABLE_PREVIEW_LIMIT
            else ""
        )
        print(f"Undecryptable TOTP user ids{suffix}: {preview}", file=sys.stderr)
        print(
            "Do not rotate or retire the active JWT signer until unavailable=0. "
            "Investigate the affected ciphertext/key configuration, then rerun this command.",
            file=sys.stderr,
        )
        return 2
    if not args.apply and totals.rekeyed:
        print(
            "Dry run found rows that still need rekeying. Do not rotate or retire "
            "the active JWT signer: rerun with --apply, then run this verification "
            "again and require rekeyed=0 and unavailable=0.",
            file=sys.stderr,
        )
        # A dry-run inventory that discovers work is deliberately not a
        # cutover-success result. Automation that treats an exit status of 0
        # as permission to rotate JWT keys would otherwise strand every raw
        # v1 row it merely counted.
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
