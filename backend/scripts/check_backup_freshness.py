#!/usr/bin/env python3
"""Fail when no complete whole-system backup set is fresh enough.

A complete set is an archive plus its checksum and bound recovery-inventory
sidecars. Optionally publish a node_exporter textfile metric atomically so the
same executable check powers both systemd failure and Prometheus alerting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__:
    from .recovery_inventory import (  # type: ignore[import-not-found]
        InventoryError,
        authenticate_inventory,
        inventory_generated_at,
        verify_bound_inventory,
    )
else:
    from recovery_inventory import (
        InventoryError,
        authenticate_inventory,
        inventory_generated_at,
        verify_bound_inventory,
    )


_CHECKSUM_RECORD = re.compile(r"([0-9a-fA-F]{64})  ([^/\s]+)\n?\Z")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_checksum(archive: Path) -> None:
    checksum_path = archive.with_name(f"{archive.name}.sha256")
    try:
        record = checksum_path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"checksum is unreadable: {exc}") from exc
    match = _CHECKSUM_RECORD.fullmatch(record)
    if match is None or match.group(2) != archive.name:
        raise ValueError("checksum must contain one SHA-256 record naming the archive")
    if match.group(1).lower() != _sha256_path(archive):
        raise ValueError("archive checksum mismatch")


def _complete_backups(
    directory: Path,
    *,
    inventory_max_age_hours: int,
    signer: str | None = None,
) -> tuple[list[tuple[Path, datetime]], list[str]]:
    candidates = [
        *directory.glob("goatfarm-*.dump"),
        *directory.glob("goatfarm-*.dump.gpg"),
    ]
    ranked: list[tuple[Path, datetime]] = []
    invalid: list[str] = []
    for path in candidates:
        checksum = path.with_name(f"{path.name}.sha256")
        inventory = path.with_name(f"{path.name}.recovery.json")
        if (
            not path.is_file()
            or path.is_symlink()
            or not checksum.is_file()
            or checksum.is_symlink()
            or not inventory.is_file()
            or inventory.is_symlink()
        ):
            invalid.append(f"{path.name}: missing or unsafe sidecar")
            continue
        try:
            raw_inventory = json.loads(inventory.read_text(encoding="utf-8"))
            if not isinstance(raw_inventory, dict):
                raise InventoryError("recovery inventory root must be an object")
            authenticate_inventory(raw_inventory, signer=signer)
            generated_at = inventory_generated_at(raw_inventory)
        except (InventoryError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            invalid.append(f"{path.name}: invalid recovery timestamp: {exc}")
            continue
        ranked.append((path, generated_at))

    # Archive mtimes are mutable (copying or `touch`ing an old set must never
    # make the RPO monitor green). Rank by the timestamp inside the bound
    # recovery inventory, then fully verify the newest candidate before use.
    ranked.sort(key=lambda candidate: candidate[1], reverse=True)
    complete: list[tuple[Path, datetime]] = []
    for path, ranked_generated_at in ranked:
        inventory = path.with_name(f"{path.name}.recovery.json")
        try:
            _verify_checksum(path)
            verified = verify_bound_inventory(
                inventory,
                path,
                max_age_hours=inventory_max_age_hours,
                signer=signer,
            )
        except (InventoryError, OSError, ValueError) as exc:
            invalid.append(f"{path.name}: {exc}")
            continue
        verified_generated_at = inventory_generated_at(verified)
        if verified_generated_at != ranked_generated_at:
            invalid.append(f"{path.name}: recovery timestamp changed during verification")
            continue
        complete.append((path, verified_generated_at))
        # Hashing a large archive is intentionally bounded to one known-good
        # candidate per check. Continue only past newer broken/partial sets so
        # the monitor can still report the latest usable recovery point.
        break
    return complete, invalid


def _write_metric(
    path: Path,
    *,
    fresh: bool,
    age_seconds: float | None,
    recovery_timestamp_seconds: float | None,
    invalid_sets: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    age_value = -1 if age_seconds is None else int(age_seconds)
    recovery_timestamp_value = (
        -1 if recovery_timestamp_seconds is None else int(recovery_timestamp_seconds)
    )
    payload = (
        "# HELP goatfarm_backup_fresh Whether a complete whole-system backup is within policy.\n"
        "# TYPE goatfarm_backup_fresh gauge\n"
        f"goatfarm_backup_fresh {1 if fresh else 0}\n"
        "# HELP goatfarm_backup_age_seconds Age of the newest complete backup, or -1 if absent.\n"
        "# TYPE goatfarm_backup_age_seconds gauge\n"
        f"goatfarm_backup_age_seconds {age_value}\n"
        "# HELP goatfarm_backup_recovery_timestamp_seconds "
        "Verified recovery-point Unix timestamp, or -1 if absent.\n"
        "# TYPE goatfarm_backup_recovery_timestamp_seconds gauge\n"
        f"goatfarm_backup_recovery_timestamp_seconds {recovery_timestamp_value}\n"
        "# HELP goatfarm_backup_invalid_sets Newest backup sets rejected before a valid set.\n"
        "# TYPE goatfarm_backup_invalid_sets gauge\n"
        f"goatfarm_backup_invalid_sets {invalid_sets}\n"
    )
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--max-age-hours", type=int, default=26)
    parser.add_argument("--inventory-max-age-hours", type=int, default=24 * 31)
    parser.add_argument("--textfile", type=Path)
    parser.add_argument(
        "--signer",
        help="Trusted signer fingerprint; defaults to configured backup/restore signer",
    )
    args = parser.parse_args()
    if args.max_age_hours < 1:
        parser.error("--max-age-hours must be positive")
    if args.inventory_max_age_hours < args.max_age_hours:
        parser.error("--inventory-max-age-hours must be at least --max-age-hours")
    if not args.directory.is_dir():
        print(f"backup directory does not exist: {args.directory}", file=sys.stderr)
        if args.textfile:
            _write_metric(
                args.textfile,
                fresh=False,
                age_seconds=None,
                recovery_timestamp_seconds=None,
                invalid_sets=0,
            )
        return 2
    backups, invalid = _complete_backups(
        args.directory,
        inventory_max_age_hours=args.inventory_max_age_hours,
        signer=args.signer,
    )
    age_seconds = None if not backups else max(0.0, time.time() - backups[0][1].timestamp())
    recovery_timestamp_seconds = None if not backups else backups[0][1].timestamp()
    fresh = age_seconds is not None and age_seconds <= args.max_age_hours * 3600
    if args.textfile:
        _write_metric(
            args.textfile,
            fresh=fresh,
            age_seconds=age_seconds,
            recovery_timestamp_seconds=recovery_timestamp_seconds,
            invalid_sets=len(invalid),
        )
    for problem in invalid:
        print(f"invalid backup set: {problem}", file=sys.stderr)
    if age_seconds is None or age_seconds > args.max_age_hours * 3600:
        state = "absent" if age_seconds is None else f"{age_seconds / 3600:.1f}h old"
        print(
            f"newest complete whole-system backup is {state}; policy is {args.max_age_hours}h",
            file=sys.stderr,
        )
        return 2
    print(f"fresh complete backup: {backups[0][0]} ({age_seconds / 3600:.1f}h old)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
