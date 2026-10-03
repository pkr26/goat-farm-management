"""Structured pytest outcomes for mutation measurement (not output parsing)."""

import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

reports: list[dict[str, Any]] = []
collection_errors: list[str] = []


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
