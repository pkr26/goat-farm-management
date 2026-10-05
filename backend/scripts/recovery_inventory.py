#!/usr/bin/env python3
"""Create, validate, bind, and verify whole-system recovery inventories.

The inventory never contains secret bytes. It records one-way identities for
the complete stable material/key rings a recovered database depends on,
external escrow receipts, and a digest/receipt for the versioned
screening-object recovery point. Backup artifacts bind a validated inventory
to their exact name and SHA-256 digest.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REQUIRED_KEY_MATERIAL = frozenset(
    {
        "jwt_private",
        "jwt_public",
        "totp_encryption",
        "idempotency_hmac",
        "database_ca",
        "backup_gpg",
    }
)
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
SAFE_ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")


class InventoryError(ValueError):
    """A recovery inventory is incomplete, stale, or does not match."""


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_readable(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise InventoryError(f"{label} must be a regular, non-symlink file: {path}")
    try:
        with path.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        raise InventoryError(f"{label} is not readable: {path}: {exc}") from exc
    return path


def _parse_pairs(values: list[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        name, separator, payload = value.partition("=")
        if not separator or not name or not payload:
            raise InventoryError(f"{label} must use NAME=VALUE: {value!r}")
        if name in parsed:
            raise InventoryError(f"duplicate {label} name: {name}")
        parsed[name] = payload
    return parsed


def _load(path: Path) -> dict[str, Any]:
    _regular_readable(path, "recovery inventory")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"invalid recovery inventory JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InventoryError("recovery inventory root must be an object")
    return value


def _generated_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise InventoryError("generated_at must be an RFC 3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InventoryError("generated_at must be a valid RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise InventoryError("generated_at must include a timezone")
    return parsed.astimezone(UTC)


def inventory_generated_at(inventory: dict[str, Any]) -> datetime:
    """Whole-system recovery is only as recent as its oldest recovered component."""
    return min(
        _generated_at(inventory["database_artifact"].get("recovered_at")),
        _generated_at(inventory["screening_objects"].get("recovered_at")),
    )


def _canonical_payload(inventory: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in inventory.items() if key != "authentication"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _signer(value: str | None) -> str:
    value = (
        value
        or os.environ.get("GOATFARM_RESTORE_GPG_SIGNER_FINGERPRINT")
        or os.environ.get("GOATFARM_BACKUP_GPG_SIGNER_FINGERPRINT", "")
    )
    if not re.fullmatch(r"[0-9A-Fa-f]{40}([0-9A-Fa-f]{24})?", value):
        raise InventoryError("a trusted GPG signer fingerprint must be configured externally")
    return value.upper()


def authenticate_inventory(inventory: dict[str, Any], *, signer: str | None = None) -> None:
    """Verify exact manifest bytes against an external pinned signer, never a self-declared key."""
    expected = _signer(signer)
    auth = inventory.get("authentication")
    if not isinstance(auth, dict) or auth.get("format") != "openpgp-detached":
        raise InventoryError("recovery inventory has no authenticated manifest signature")
    try:
        signature = base64.b64decode(auth["signature"], validate=True)
        if not signature or len(signature) > 65536:
            raise ValueError("invalid signature length")
        payload = _canonical_payload(inventory)
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise InventoryError("invalid recovery manifest signature encoding") from exc
    with tempfile.TemporaryDirectory(prefix="goatfarm-recovery-verify-") as directory:
        content = Path(directory) / "manifest.json"
        sig = Path(directory) / "manifest.sig"
        content.write_bytes(payload)
        sig.write_bytes(signature)
        try:
            result = subprocess.run(
                ["gpg", "--batch", "--status-fd", "1", "--verify", str(sig), str(content)],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InventoryError("could not verify recovery manifest signature") from exc
    statuses = result.stdout.decode("utf-8", errors="replace").splitlines()
    valid = [line.split() for line in statuses if line.startswith("[GNUPG:] VALIDSIG ")]
    forbidden = ("BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "NO_PUBKEY")
    if (
        result.returncode
        or len(valid) != 1
        or any(
            any(line.startswith(f"[GNUPG:] {status} ") for status in forbidden) for line in statuses
        )
    ):
        raise InventoryError("recovery manifest signature verification failed")
    fields = valid[0]
    if len(fields) < 12 or expected not in {fields[2].upper(), fields[11].upper()}:
        raise InventoryError("recovery manifest signature is from an unexpected signer")
    try:
        signed_at = datetime.fromtimestamp(int(fields[4]), UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise InventoryError("invalid manifest signature timestamp") from exc
    if signed_at > datetime.now(UTC) + timedelta(minutes=5):
        raise InventoryError("manifest signature timestamp is in the future")
    for timestamp in (
        inventory.get("generated_at"),
        inventory.get("database_artifact", {}).get("recovered_at"),
        inventory.get("screening_objects", {}).get("recovered_at"),
    ):
        if _generated_at(timestamp) > signed_at + timedelta(minutes=5):
            raise InventoryError("recovery timestamp postdates its authenticated signature")


def validate_inventory(
    inventory: dict[str, Any], *, max_age_hours: int, require_bound: bool | None
) -> None:
    if inventory.get("schema_version") != 2:
        raise InventoryError("unsupported recovery inventory schema_version")
    generated = _generated_at(inventory.get("generated_at"))
    now = datetime.now(UTC)
    if generated > now + timedelta(minutes=5):
        raise InventoryError("recovery inventory timestamp is in the future")
    if now - generated > timedelta(hours=max_age_hours):
        raise InventoryError(f"recovery inventory is older than the {max_age_hours}-hour limit")

    objects = inventory.get("screening_objects")
    if not isinstance(objects, dict):
        raise InventoryError("screening_objects must be an object")
    for field in ("bucket", "prefix", "recovery_point", "restore_receipt"):
        if not isinstance(objects.get(field), str) or not objects[field].strip():
            raise InventoryError(f"screening_objects.{field} must be non-blank")
    if objects.get("versioning") != "Enabled":
        raise InventoryError("screening object bucket versioning must be Enabled")
    object_time = _generated_at(objects.get("recovered_at"))
    if object_time > generated + timedelta(minutes=5):
        raise InventoryError("object recovery timestamp postdates inventory capture")
    if now - object_time > timedelta(hours=max_age_hours):
        raise InventoryError("object recovery point is older than the age limit")
    manifest = objects.get("manifest")
    if not isinstance(manifest, dict):
        raise InventoryError("screening_objects.manifest must be an object")
    if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        raise InventoryError("screening object manifest name must be non-blank")
    if not isinstance(manifest.get("bytes"), int) or manifest["bytes"] < 1:
        raise InventoryError("screening object manifest must be non-empty")
    if not isinstance(manifest.get("sha256"), str) or not HEX_SHA256.fullmatch(manifest["sha256"]):
        raise InventoryError("screening object manifest SHA-256 is invalid")

    key_material = inventory.get("key_material")
    if not isinstance(key_material, dict):
        raise InventoryError("key_material must be an object")
    missing = sorted(REQUIRED_KEY_MATERIAL - key_material.keys())
    if missing:
        raise InventoryError(f"missing key material recovery entries: {', '.join(missing)}")
    for name in sorted(REQUIRED_KEY_MATERIAL):
        entry = key_material.get(name)
        if not isinstance(entry, dict):
            raise InventoryError(f"key_material.{name} must be an object")
        fingerprint = entry.get("sha256")
        receipt = entry.get("escrow_receipt")
        if not isinstance(fingerprint, str) or not HEX_SHA256.fullmatch(fingerprint):
            raise InventoryError(f"key_material.{name}.sha256 is invalid")
        if not isinstance(receipt, str) or not receipt.strip():
            raise InventoryError(f"key_material.{name}.escrow_receipt must be non-blank")

    binding = inventory.get("database_artifact")
    if require_bound is True and not isinstance(binding, dict):
        raise InventoryError("recovery inventory is not bound to a database artifact")
    if require_bound is False and binding is not None:
        raise InventoryError("source recovery inventory must not already be database-bound")
    if isinstance(binding, dict):
        database_time = _generated_at(binding.get("recovered_at"))
        if database_time > now + timedelta(minutes=5):
            raise InventoryError("database recovery timestamp is in the future")
        if now - database_time > timedelta(hours=max_age_hours):
            raise InventoryError("database recovery point is older than the age limit")
        if not isinstance(binding.get("name"), str) or not SAFE_ARTIFACT_NAME.fullmatch(
            binding["name"]
        ):
            raise InventoryError("database artifact name must be a safe basename")
        if not isinstance(binding.get("sha256"), str) or not HEX_SHA256.fullmatch(
            binding["sha256"]
        ):
            raise InventoryError("database artifact SHA-256 is invalid")


def _capture(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        raise InventoryError(f"refusing to overwrite recovery inventory: {output}")
    manifest_path = _regular_readable(Path(args.object_manifest), "object manifest")
    key_paths = _parse_pairs(args.key_file, "--key-file")
    identities = _parse_pairs(args.identity, "--identity")
    escrow = _parse_pairs(args.escrow_receipt, "--escrow-receipt")
    overlap = sorted(set(key_paths) & set(identities))
    if overlap:
        raise InventoryError(
            "key material must use either --key-file or --identity, not both: " + ", ".join(overlap)
        )
    supplied = set(key_paths) | set(identities)
    if supplied != REQUIRED_KEY_MATERIAL:
        raise InventoryError(
            "key identities must be exactly: " + ", ".join(sorted(REQUIRED_KEY_MATERIAL))
        )
    if set(escrow) != REQUIRED_KEY_MATERIAL:
        raise InventoryError(
            "escrow receipts must be exactly: " + ", ".join(sorted(REQUIRED_KEY_MATERIAL))
        )

    versioning = args.object_versioning_status
    if args.check_s3_versioning:
        import boto3

        access_key = os.environ.get("GOATFARM_S3_ACCESS_KEY_ID") or None
        secret_key = os.environ.get("GOATFARM_S3_SECRET_ACCESS_KEY") or None
        client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("GOATFARM_S3_ENDPOINT_URL") or None,
            region_name=os.environ.get("GOATFARM_S3_REGION", "us-east-1"),
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        versioning = client.get_bucket_versioning(Bucket=args.object_bucket).get("Status")
    if versioning != "Enabled":
        raise InventoryError("screening object bucket versioning is not Enabled")

    key_material: dict[str, dict[str, str]] = {}
    for name in sorted(REQUIRED_KEY_MATERIAL):
        if name in key_paths:
            source = _regular_readable(Path(key_paths[name]), f"key material {name}")
            fingerprint = _sha256_path(source)
        else:
            fingerprint = hashlib.sha256(identities[name].encode("utf-8")).hexdigest()
        key_material[name] = {
            "sha256": fingerprint,
            "escrow_receipt": escrow[name],
        }

    inventory: dict[str, Any] = {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "screening_objects": {
            "bucket": args.object_bucket,
            "prefix": args.object_prefix,
            "versioning": versioning,
            "recovery_point": args.object_recovery_point,
            "recovered_at": args.object_recovered_at,
            "restore_receipt": args.object_restore_receipt,
            "manifest": {
                "name": manifest_path.name,
                "bytes": manifest_path.stat().st_size,
                "sha256": _sha256_path(manifest_path),
            },
        },
        "key_material": key_material,
    }
    validate_inventory(inventory, max_age_hours=args.max_age_hours, require_bound=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(inventory, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _validate(args: argparse.Namespace) -> None:
    inventory = _load(Path(args.inventory))
    require_bound = {"bound": True, "unbound": False, "either": None}[args.binding]
    validate_inventory(inventory, max_age_hours=args.max_age_hours, require_bound=require_bound)


def _bind(args: argparse.Namespace) -> None:
    source = _load(Path(args.inventory))
    validate_inventory(source, max_age_hours=args.max_age_hours, require_bound=False)
    if not HEX_SHA256.fullmatch(args.archive_sha256):
        raise InventoryError("archive SHA-256 is invalid")
    if not SAFE_ARTIFACT_NAME.fullmatch(args.archive_name):
        raise InventoryError("archive name must be a safe basename")
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        raise InventoryError(f"refusing to overwrite bound recovery inventory: {output}")
    source["database_artifact"] = {
        "name": args.archive_name,
        "sha256": args.archive_sha256,
        "recovered_at": args.database_recovered_at,
    }
    validate_inventory(source, max_age_hours=args.max_age_hours, require_bound=True)
    signer = _signer(args.signer)
    try:
        result = subprocess.run(
            ["gpg", "--batch", "--yes", "--local-user", signer, "--detach-sign", "--output", "-"],
            input=_canonical_payload(source),
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InventoryError("could not sign recovery manifest") from exc
    if result.returncode or not result.stdout:
        raise InventoryError("GPG failed to sign recovery manifest")
    source["authentication"] = {
        "format": "openpgp-detached",
        "signature": base64.b64encode(result.stdout).decode("ascii"),
    }
    authenticate_inventory(source, signer=signer)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(source, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _verify(args: argparse.Namespace) -> None:
    verify_bound_inventory(
        Path(args.inventory),
        Path(args.archive),
        max_age_hours=args.max_age_hours,
        signer=args.signer,
    )


def verify_bound_inventory(
    inventory_path: Path,
    archive_path: Path,
    *,
    max_age_hours: int,
    signer: str | None = None,
) -> dict[str, Any]:
    """Validate a bound inventory and its exact archive, returning its data.

    The public helper lets the backup-freshness monitor apply the same schema,
    age, and archive-binding rules as the restore path. Keeping one verifier
    prevents a set of merely present sidecar filenames from being reported as
    recoverable when either sidecar is stale or names different bytes.
    """
    inventory = _load(inventory_path)
    authenticate_inventory(inventory, signer=signer)
    validate_inventory(inventory, max_age_hours=max_age_hours, require_bound=True)
    archive = _regular_readable(archive_path, "database backup artifact")
    binding = inventory["database_artifact"]
    if binding["name"] != archive.name:
        raise InventoryError(f"inventory names {binding['name']!r}, not archive {archive.name!r}")
    if binding["sha256"] != _sha256_path(archive):
        raise InventoryError("database backup artifact does not match recovery inventory")
    return inventory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture")
    capture.add_argument("--output", required=True)
    capture.add_argument("--object-bucket", required=True)
    capture.add_argument("--object-prefix", required=True)
    capture.add_argument("--object-recovery-point", required=True)
    capture.add_argument("--object-recovered-at", required=True)
    capture.add_argument("--object-restore-receipt", required=True)
    capture.add_argument("--object-manifest", required=True)
    capture.add_argument("--object-versioning-status", default="Enabled")
    capture.add_argument("--check-s3-versioning", action="store_true")
    capture.add_argument("--key-file", action="append", default=[])
    capture.add_argument("--identity", action="append", default=[])
    capture.add_argument("--escrow-receipt", action="append", default=[])
    capture.add_argument("--max-age-hours", type=int, default=26)
    capture.set_defaults(handler=_capture)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--inventory", required=True)
    validate.add_argument("--max-age-hours", type=int, default=26)
    validate.add_argument("--binding", choices=("bound", "unbound", "either"), default="either")
    validate.set_defaults(handler=_validate)

    bind = subparsers.add_parser("bind")
    bind.add_argument("--inventory", required=True)
    bind.add_argument("--output", required=True)
    bind.add_argument("--archive-name", required=True)
    bind.add_argument("--archive-sha256", required=True)
    bind.add_argument("--database-recovered-at", required=True)
    bind.add_argument("--signer")
    bind.add_argument("--max-age-hours", type=int, default=26)
    bind.set_defaults(handler=_bind)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--inventory", required=True)
    verify.add_argument("--archive", required=True)
    verify.add_argument("--signer")
    verify.add_argument("--max-age-hours", type=int, default=24 * 31)
    verify.set_defaults(handler=_verify)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if getattr(args, "max_age_hours", 1) < 1:
        raise InventoryError("--max-age-hours must be positive")
    args.handler(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InventoryError as exc:
        print(f"recovery inventory error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
