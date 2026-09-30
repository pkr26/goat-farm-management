"""mypy --strict over tests/ — counted ratchet (2026-09-29 audit, T5).

The 113-file test suite predates strict typing (1,778 strict errors at the
ratchet's introduction, after a mechanical pass annotated every bare
``dict`` generic). Full strictness is the destination; the ratchet is the
vehicle: CI fails when the count GROWS, and every deliberate decrease is
committed together with the lowered baseline file (same pattern as the
coverage ``fail_under`` and the English-literal ceiling).

Run locally:  python scripts/mypy_tests_ratchet.py [--update]
"""

from __future__ import annotations

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
        if line.startswith("Found ") and " error" in line:
            return int(line.split()[1])
    # Zero errors is the ratchet's destination and prints a Success line.
    if any(line.startswith("Success: no issues found") for line in proc.stdout.splitlines()):
        return 0
    # Anything else (a crashed mypy — bad flag, unreadable file, fatal
    # error) prints no summary at all; treating that as zero would announce
    # a bogus "improvement". Fail the run instead of misreporting.
    print("mypy produced no error summary — the run crashed or misconfigured:")
    print(proc.stdout.strip() or proc.stderr.strip() or "(no output)")
    raise SystemExit(1)


def main() -> int:
    count = current_count()
    recorded = int(BASELINE.read_text().strip())
    if "--update" in sys.argv:
        BASELINE.write_text(f"{count}\n")
        print(f"baseline updated to {count}")
        return 0
    if count > recorded:
        print(
            f"mypy --strict over tests/ grew: {recorded} -> {count} errors.\n"
            "New test code must be typed (see backend/AGENTS conventions); fix the\n"
            "new errors, or — for a deliberate, reviewed bulk decrease — run\n"
            "python scripts/mypy_tests_ratchet.py --update after improving types."
        )
        return 1
    if count < recorded:
        print(
            f"mypy --strict over tests/ improved: {recorded} -> {count} errors.\n"
            "Commit the lowered baseline with this change:\n"
            "python scripts/mypy_tests_ratchet.py --update"
        )
        return 1
    print(f"mypy --strict over tests/ holds at {count} errors (ratchet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
