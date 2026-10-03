"""Campaign receipts and compatible latest-attempt folding."""

import hashlib
import json
from pathlib import Path
from typing import Any

TERMINAL = frozenset({"KILLED", "SURVIVED", "INVALID"})


def sha_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def input_identity(backend: Path) -> dict[str, Any]:
    def tree(directory: str) -> dict[str, str | None]:
        return {
            path.relative_to(backend).as_posix(): sha_file(path)
            for path in sorted((backend / directory).rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }

    return {
        "sources": tree("app"),
        "tests": tree("tests"),
        "harness": {
            path.name: sha_file(path) for path in sorted((backend / "mutation").glob("*.py"))
        },
        "locks": {name: sha_file(backend / name) for name in ("uv.lock", "pyproject.toml")},
    }


def read_results(path: Path) -> list[dict[str, Any]]:
    records = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                record = json.loads(line)
                if (
                    isinstance(record, dict)
                    and isinstance(record.get("id"), str)
                    and isinstance(record.get("status"), str)
                ):
                    records.append(record)
            except (ValueError, TypeError):
                continue
    return records


def latest_compatible(
    records: list[dict[str, Any]], campaign_id: str, manifest: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    latest = {}
    for record in records:
        mid = record.get("id")
        if not isinstance(mid, str):
            continue
        mutant = manifest.get(mid)
        if (
            mutant
            and record.get("campaign_id") == campaign_id
            and record.get("mutant_digest") == digest_json(mutant)
        ):
            latest[record["id"]] = record
    return latest


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)
