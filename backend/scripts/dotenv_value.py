#!/usr/bin/env python3
"""Read one setting with the application's python-dotenv grammar.

The backup and restore safety gates must interpret ``backend/.env`` exactly as
pydantic-settings does.  In particular, a hand-written ``sed`` parser is not
equivalent for ``export`` declarations, quoting, inline comments, escaping, or
variable interpolation.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} ENV_FILE KEY", file=sys.stderr)
        return 2

    env_file = Path(sys.argv[1])
    key = sys.argv[2]
    if not env_file.is_file():
        return 3

    try:
        from dotenv import dotenv_values
    except ImportError:
        print(
            "python-dotenv is required to read backend/.env safely; "
            "run this job with the backend virtual environment installed",
            file=sys.stderr,
        )
        return 2

    try:
        values = dotenv_values(env_file, interpolate=True, encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"Cannot safely read {env_file}: {exc}", file=sys.stderr)
        return 2

    # BaseSettings is case-insensitive by default. Preserve file order while
    # normalizing so the last spelling of a duplicate key wins the same way.
    normalized_values = {name.lower(): value for name, value in values.items()}
    normalized_key = key.lower()
    if normalized_key not in normalized_values or normalized_values[normalized_key] is None:
        return 3

    value = normalized_values[normalized_key]
    assert value is not None
    if "\x00" in value:
        print(f"Refusing NUL byte in {key} from {env_file}", file=sys.stderr)
        return 2
    sys.stdout.write(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
