"""Structured recursive pytest collection used to verify complete coverage shards."""

import json
import os
from pathlib import Path

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    target = os.environ.get("MUTATION_COVERAGE_INVENTORY_PATH")
    if target is None:
        return
    root = Path.cwd()
    Path(target).write_text(
        json.dumps(
            {
                "exit_code": int(exitstatus),
                "items": [
                    {"nodeid": item.nodeid, "file": item.path.relative_to(root).as_posix()}
                    for item in session.items
                ],
            },
            sort_keys=True,
        )
    )
