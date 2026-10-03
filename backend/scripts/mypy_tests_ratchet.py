"""Permanent zero-error mypy --strict gate over tests/.

The original 1,778-error ratchet reached zero on 2026-10-03. Both the
checked-in baseline and the current strict run must stay at zero. The
compatible ``--update`` command can record a successful zero-error run,
but cannot accept new typing debt.

Run locally:  python scripts/mypy_tests_ratchet.py [--update]
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BASELINE = Path(__file__).resolve().parent.parent / "tests" / ".mypy-strict-baseline"


def current_count() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "tests"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    for line in reversed(proc.stdout.splitlines()):
        match = re.fullmatch(
            r"Found (\d+) errors? in \d+ files? \(checked \d+ source files?\)", line
        )
        if match is not None and proc.returncode == 1:
            return int(match.group(1))
    if proc.returncode == 0 and any(
        line.startswith("Success: no issues found") for line in proc.stdout.splitlines()
    ):
        return 0
    # Anything else (a crashed mypy — bad flag, unreadable file, fatal
    # error) prints no summary at all; treating that as zero would announce
    # a bogus "improvement". Fail the run instead of misreporting.
    print("mypy produced no error summary — the run crashed or misconfigured:")
    print(proc.stdout.strip() or proc.stderr.strip() or "(no output)")
    raise SystemExit(1)


def main() -> int:
    count = current_count()
    if count != 0:
        print(f"mypy --strict over tests/ found {count} errors; the permanent baseline is zero.")
        print("Fix the typing errors before updating the baseline or passing CI.")
        return 1
    if "--update" in sys.argv:
        BASELINE.write_text("0\n")
        print("zero-error baseline confirmed")
        return 0
    try:
        recorded = int(BASELINE.read_text().strip())
    except (OSError, ValueError) as exc:
        print(f"Cannot read the zero-error test typing baseline: {exc}")
        return 1
    if recorded != 0:
        print("The permanent test typing baseline must be zero; it cannot be raised.")
        return 1
    print("mypy --strict over tests/ passes with zero errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
