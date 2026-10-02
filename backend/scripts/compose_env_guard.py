#!/usr/bin/env python3
"""Fail a Compose rollout when its dotenv file contains a misspelt knob.

Docker Compose interpolates only names that appear in its manifest. A typo in
the root deployment file is therefore normally invisible to the API's normal
process-environment guard: Compose simply never forwards it. This tiny,
least-privilege one-shot service reads the same dotenv file as Compose before
the migration job is allowed to run. It validates names, and — since the
production secrets became deliverable either as plain values or as file
mounts (2026-10-01 audit, 09-1) — that each required secret is delivered by
exactly one of the two routes; each consuming process remains responsible for
validating its own values and secrets.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values

# This is backend/ when run from a checkout and /app when copied into the
# production image (where the project is deliberately NOT installed into the
# venv), so the same command works in either operational context. Without
# this, running `python scripts/compose_env_guard.py` puts only the script's
# own directory on sys.path and `import app` fails inside the container.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import (  # noqa: E402
    MigrationSettings,
    ScreeningWorkerSettings,
    Settings,
    _known_goatfarm_env_names,
)

# Production secrets that docker-compose.production.yml accepts either as a
# plain value or through a file-delivered ``*_FILE`` container path
# (2026-10-01 audit, 09-1). Compose cannot express "exactly one of two
# interpolations" with ``:?`` guards once both routes exist, so this preflight
# restores the fail-closed property the required interpolations used to
# provide: the env file must deliver each one exactly once. "Both" is the
# dangerous case — a stale plain value left behind after a file migration
# would look live while the app silently prefers the file.
REQUIRED_EITHER_DELIVERY_VARS = (
    "GOATFARM_DATABASE_URL",
    "GOATFARM_MIGRATION_DATABASE_URL",
    "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET",
)

# Required in production but legitimately optional in development (legacy
# TOTP ciphertext readability), so absence passes here and the application's
# boot validator decides by environment. Ambiguity is still refused.
AMBIGUOUS_DELIVERY_VARS = ("GOATFARM_TOTP_ENCRYPTION_KEY",)


def unknown_names(path: Path) -> set[str]:
    """Return case-insensitive unknown ``GOATFARM_*`` dotenv names."""
    values = dotenv_values(path)
    fields = (
        frozenset(Settings.model_fields)
        | frozenset(MigrationSettings.model_fields)
        | frozenset(ScreeningWorkerSettings.model_fields)
    )
    known = _known_goatfarm_env_names(fields)
    return {
        name.upper() for name in values if name is not None and name.upper().startswith("GOATFARM_")
    } - known


def _present(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def delivery_problems(path: Path) -> list[str]:
    """Secret-delivery ambiguity/absence problems in a Compose dotenv file."""
    values = {
        name.upper(): value for name, value in dotenv_values(path).items() if name is not None
    }
    problems: list[str] = []
    for name in REQUIRED_EITHER_DELIVERY_VARS:
        plain = _present(values.get(name))
        from_file = _present(values.get(f"{name}_FILE"))
        if plain and from_file:
            problems.append(f"{name} and {name}_FILE are both set; deliver exactly one")
        if not plain and not from_file:
            problems.append(f"neither {name} nor {name}_FILE is set; deliver exactly one")
    for name in AMBIGUOUS_DELIVERY_VARS:
        if _present(values.get(name)) and _present(values.get(f"{name}_FILE")):
            problems.append(f"{name} and {name}_FILE are both set; deliver exactly one")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dotenv", type=Path, help="the exact Compose --env-file to validate")
    args = parser.parse_args()

    path = args.dotenv
    if not path.is_file():
        parser.error(f"dotenv file is not a regular readable file: {path}")
    if unknown := unknown_names(path):
        parser.error(
            "unknown GOATFARM_* environment variable(s): "
            f"{', '.join(sorted(unknown))}; check the deployment template"
        )
    if problems := delivery_problems(path):
        parser.error("; ".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
