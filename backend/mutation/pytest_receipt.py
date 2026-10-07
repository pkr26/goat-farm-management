"""Structured pytest outcomes for mutation measurement (not output parsing)."""

import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

reports: list[dict[str, Any]] = []
collection_errors: list[str] = []


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Honor explicit oracle order even when pytest merges broader file selectors."""
    selection_path = os.environ.get("MUTATION_SELECTION_PATH")
    if not selection_path:
        return
    selected: list[str] = json.loads(Path(selection_path).read_text())
    ranks: dict[str, int] = {}
    for index, selector in enumerate(selected):
        ranks.setdefault(selector, index)

    def priority(item: pytest.Item) -> int:
        parts = item.nodeid.split("[", 1)[0].split("::")
        ancestors = [item.nodeid] + ["::".join(parts[:i]) for i in range(1, len(parts) + 1)]
        return min(ranks.get(node, len(selected)) for node in ancestors)

    # Redundant selectors may collect the same item twice. Execute each
    # concrete node once, retaining every distinct coverer and parameter case.
    unique: dict[str, pytest.Item] = {}
    for item in items:
        unique.setdefault(item.nodeid, item)
    items[:] = sorted(unique.values(), key=priority)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[Any]
) -> Generator[None, Any]:
    outcome = yield
    report = outcome.get_result()
    assertion = call.excinfo is not None and isinstance(
        call.excinfo.value, (AssertionError, pytest.fail.Exception)
    )
    reports.append(
        {
            "nodeid": report.nodeid,
            "when": report.when,
            "outcome": report.outcome,
            "assertion": assertion,
        }
    )


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        collection_errors.append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
    path = Path(os.environ["MUTATION_RECEIPT_PATH"])
    payload = {
        "exit_code": int(exitstatus),
        "collected": session.testscollected,
        "reports": reports,
        "collection_errors": collection_errors,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)
