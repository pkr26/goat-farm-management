#!/usr/bin/env python3
"""Fail a Compose rollout when its dotenv file contains a misspelt knob.

Docker Compose interpolates only names that appear in its manifest. A typo in
the root deployment file is therefore normally invisible to the API's normal
process-environment guard: Compose simply never forwards it. This tiny,
least-privilege one-shot service reads the same dotenv file as Compose before
the migration job is allowed to run. It validates names only; each consuming
process remains responsible for validating its own values and secrets.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import dotenv_values

from app.core.config import (
    MigrationSettings,
    ScreeningWorkerSettings,
    Settings,
    _known_goatfarm_env_names,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
