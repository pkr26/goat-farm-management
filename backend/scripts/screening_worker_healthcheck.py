#!/usr/bin/env python3
"""Docker health probe for the disease-screening worker.

The worker writes a tiny atomic heartbeat after every successful cycle. A
live-but-broken poll loop therefore becomes unhealthy instead of looking fine
merely because its Python process still exists.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path

DEFAULT_PATH = str(Path(tempfile.gettempdir()) / "goatfarm-screening-worker.json")


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int) -> int:
    try:
        parsed = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc
    if parsed < 1:
        raise SystemExit(f"{name} must be positive")
    return parsed


def main() -> None:
    path = Path(os.environ.get("GOATFARM_SCREENING_WORKER_HEARTBEAT_PATH", DEFAULT_PATH))
    max_age = _positive_int("GOATFARM_SCREENING_WORKER_HEALTH_MAX_AGE_SECONDS", 900)
    enabled = _bool(os.environ.get("GOATFARM_SCREENING_ENABLED", "false"))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        status = payload["status"]
        updated_at = float(payload["updated_at"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"screening worker heartbeat is unavailable: {exc}") from exc
    # Python's permissive json decoder accepts NaN/Infinity even though JSON
    # itself does not. Comparisons against NaN are always false, which would
    # otherwise accidentally bypass the stale-heartbeat test below.
    if not math.isfinite(updated_at):
        raise SystemExit("screening worker heartbeat has a non-finite timestamp")
    age = time.time() - updated_at
    if age < -5 or age > max_age:
        raise SystemExit(f"screening worker heartbeat is stale ({age:.0f}s; max {max_age}s)")
    # ``working`` is renewed while a valid large batch is still running. A
    # completed cycle promotes it to ``ok``; an exception promotes it to
    # ``error``. Both healthy states prove the event loop remains alive
    # without marking a long provider call as a false container failure.
    allowed = {"disabled"} if not enabled else {"starting", "working", "ok"}
    if status not in allowed:
        raise SystemExit(f"screening worker reports unhealthy state: {status!r}")


if __name__ == "__main__":
    main()
