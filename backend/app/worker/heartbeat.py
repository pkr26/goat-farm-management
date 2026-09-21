"""Atomic, local liveness record for the screening-worker container."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Literal

HeartbeatStatus = Literal["starting", "working", "ok", "error", "disabled", "stopped"]


def write_heartbeat(path: Path, status: HeartbeatStatus, *, consecutive_failures: int = 0) -> None:
    """Publish one worker state transition without exposing failure details.

    A same-directory replace means the health probe never observes a partial
    JSON document. The file contains no tenant data or credentials, but mode
    0600 keeps the operational signal private to the container user.
    ``consecutive_failures`` lets the probe apply the same recovery-window
    tolerance the worker loop itself uses, instead of failing the container
    on the first transient cycle error.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # A fixed ``.{name}.tmp`` races if an orchestrator overlaps old and new
    # workers, and opening it with O_TRUNC would follow an attacker-created
    # symlink. mkstemp creates an unpredictable, exclusive same-directory
    # inode (0600), so replace remains atomic while neither writer can clobber
    # an unrelated file.
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    payload = json.dumps(
        {"status": status, "updated_at": time.time(), "consecutive_failures": consecutive_failures},
        separators=(",", ":"),
    )
    try:
        # mkstemp defaults to 0600, but enforce it explicitly so the privacy
        # contract does not depend on a platform-specific implementation.
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
