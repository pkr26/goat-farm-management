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


_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "t", "y"})
_FALSE_WORDS = frozenset({"", "0", "false", "no", "off", "f", "n"})


def _bool(name: str, value: str) -> bool:
    """Parse exactly the boolean spellings pydantic-settings accepts."""
    normalized = value.strip().lower()
    if normalized in _TRUE_WORDS:
        return True
    if normalized in _FALSE_WORDS:
        return False
    raise SystemExit(f"{name} must be a boolean")


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
    # Same default as Settings.screening_worker_max_consecutive_cycle_failures
    # (and set to the identical value by both Compose files), so the probe and
    # the worker apply one shared definition of "persistently broken".
    max_failures = _positive_int(
        "GOATFARM_SCREENING_WORKER_MAX_CONSECUTIVE_CYCLE_FAILURES", 3
    )
    enabled = _bool("GOATFARM_SCREENING_ENABLED", os.environ.get("GOATFARM_SCREENING_ENABLED", "false"))
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
    if status == "error":
        consecutive = payload.get("consecutive_failures")
        if (
            not isinstance(consecutive, int)
            or isinstance(consecutive, bool)
            or consecutive < 0
        ):
            raise SystemExit("screening worker heartbeat has a malformed failure count")
        if consecutive >= max_failures:
            raise SystemExit(
                f"screening worker reports persistent cycle failures "
                f"({consecutive}/{max_failures})"
            )
        # A whole-cycle exception inside the worker's own recovery window:
        # the loop is alive and retrying at the next poll, exactly as
        # designed. Failing the container here would race the worker's own
        # exit threshold and restart a worker that was about to recover.
        return
    # ``working`` is renewed while a valid large batch is still running. A
    # completed cycle promotes it to ``ok``. Both healthy states prove the
    # event loop remains alive without marking a long provider call as a
    # false container failure.
    allowed = {"disabled"} if not enabled else {"starting", "working", "ok"}
    if status not in allowed:
        raise SystemExit(f"screening worker reports unhealthy state: {status!r}")


if __name__ == "__main__":
    main()
