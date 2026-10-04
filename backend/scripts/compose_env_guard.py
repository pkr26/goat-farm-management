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

Compose interpolation precedence is shell environment over ``--env-file``
(2026-10-02 audit): an operator shell export of the plain variable therefore
bypasses a file-only check against the dotenv — Compose forwards BOTH routes
to the consuming service and the app silently prefers the file, leaving the
stale plain value exactly as "live-looking" as the dangerous case above. The
production manifest forwards the same interpolations into THIS container's
environment (they resolve identically — shell first, file fallback), so the
delivery check below consults the process environment alongside the file:
for each knob, whichever source Compose would actually interpolate counts.
"""

from __future__ import annotations

import argparse
import os
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
    "GOATFARM_WORKER_DATABASE_URL",
    "GOATFARM_MIGRATION_DATABASE_URL",
    "GOATFARM_IDEMPOTENCY_REQUEST_HMAC_SECRET",
)

# Optional or feature-gated credentials may be absent, but every supported
# plain/``*_FILE`` pair still has exactly one authoritative route when used.
# Keep this list in lockstep with the production manifest's config-guard
# environment and the settings projections' ambiguity checks.
OPTIONAL_EITHER_DELIVERY_VARS = (
    "GOATFARM_TOTP_ENCRYPTION_KEY",
    "GOATFARM_METRICS_BEARER_TOKEN",
    "GOATFARM_S3_ACCESS_KEY_ID",
    "GOATFARM_S3_SECRET_ACCESS_KEY",
    "GOATFARM_SCREENING_ANTHROPIC_API_KEY",
    "GOATFARM_SCREENING_OPENAI_API_KEY",
    "GOATFARM_MSG91_AUTH_KEY",
)


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
    """Secret-delivery ambiguity/absence across BOTH delivery sources.

    The dotenv file is one source; the process environment is the other —
    Compose interpolates shell values ahead of the file, and the production
    manifest forwards the same interpolations into this container precisely
    so they are visible here. An explicitly empty environment override wins
    over a stale nonempty dotenv value, just as it does in Compose.
    """
    values = {
        name.upper(): value for name, value in dotenv_values(path).items() if name is not None
    }

    def _value(name: str) -> str | None:
        return os.environ[name] if name in os.environ else values.get(name)

    def _delivered(name: str) -> bool:
        # Presence, including an explicit empty override, follows Compose
        # interpolation precedence. A stale dotenv value must not reappear.
        return _present(_value(name))

    problems: list[str] = []
    for name in REQUIRED_EITHER_DELIVERY_VARS:
        plain = _delivered(name)
        from_file = _delivered(f"{name}_FILE")
        if plain and from_file:
            problems.append(f"{name} and {name}_FILE are both set; deliver exactly one")
        if not plain and not from_file:
            problems.append(f"neither {name} nor {name}_FILE is set; deliver exactly one")
    for name in OPTIONAL_EITHER_DELIVERY_VARS:
        if _delivered(name) and _delivered(f"{name}_FILE"):
            problems.append(f"{name} and {name}_FILE are both set; deliver exactly one")
    if _delivered("GOATFARM_APP_SECRET_DIR"):
        problems.append(
            "GOATFARM_APP_SECRET_DIR is retired; split its contents into "
            "GOATFARM_API_SECRET_DIR, GOATFARM_MIGRATION_SECRET_DIR and "
            "GOATFARM_WORKER_SECRET_DIR before rollout (see README)"
        )
    directories = [
        Path(_value(name) or default).expanduser().resolve()
        for name, default in (
            ("GOATFARM_API_SECRET_DIR", "./secrets/api"),
            ("GOATFARM_MIGRATION_SECRET_DIR", "./secrets/migration"),
            ("GOATFARM_WORKER_SECRET_DIR", "./secrets/worker"),
        )
    ]
    if any(
        a == b or a in b.parents or b in a.parents
        for i, a in enumerate(directories)
        for b in directories[i + 1 :]
    ):
        problems.append(
            "service secret directories must be distinct and must not contain each other"
        )
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
