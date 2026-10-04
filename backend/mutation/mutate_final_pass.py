"""Full-selection verification with explicit gap test files included.

This produces measured attempt receipts, not proof that no killing test exists.
Dry-run displays the candidate set and exits before runner/process creation or
artifact writes. Execution stays isolated in the shared Runner.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
MUTDIR = BACKEND / "mutation"
sys.path.insert(0, str(MUTDIR))
from mutate_identity import read_artifact  # noqa: E402
from mutate_run import Runner  # noqa: E402

GAP_FILES = [
    "tests/test_calibration_curve_math.py",
    "tests/test_chronology_mutation_gaps.py",
    "tests/test_dashboard_age_math.py",
    "tests/test_feeding_split_gaps.py",
    "tests/test_health_note_gaps.py",
    "tests/test_kidding_suffix_gaps.py",
    "tests/test_lifecycle_attribution_gaps.py",
    "tests/test_notifications_mutation_gaps.py",
    "tests/test_ratelimit_sweep.py",
    "tests/test_retention_mutation_gaps.py",
    "tests/test_screening_provider_gaps.py",
    "tests/test_security_module_gaps.py",
    "tests/test_security_mutation_gaps.py",
    "tests/test_settings_defaults.py",
    "tests/test_totp_rfc_gaps.py",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--survivors-from-logs",
        default=None,
        help=(
            "comma-separated campaign-style log files; rebuild the survivor id "
            "set from their SURVIVED lines instead of results.jsonl (use after "
            "a later pass rewrote verdicts, e.g. a discarded racy pass)"
        ),
    )
    args = ap.parse_args()

    manifest = {
        m["id"]: m
        for m in json.loads(
            read_artifact(
                MUTDIR / "manifest.json", instruction="run python mutation/mutate_gen.py first"
            )
        )
    }
    last: dict[str, dict[str, Any]] = {}
    for line in (
        read_artifact(MUTDIR / "results.jsonl", instruction="run a mutation campaign first")
        .decode()
        .splitlines()
    ):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        last[rec["id"]] = rec

    if args.survivors_from_logs:
        from mutate_reverify import SURVIVOR_LINE

        by_key: dict[tuple[str, int, str, str], list[str]] = collections.defaultdict(list)
        for m in manifest.values():
            by_key[(m["file"], m["line"], m["kind"], m["detail"])].append(m["id"])
        survivor_ids = set()
        for log_name in args.survivors_from_logs.split(","):
            for line in (MUTDIR / log_name).read_text().splitlines():
                match = SURVIVOR_LINE.match(line.strip())
                if not match:
                    continue
                key = (
                    match.group(1),
                    int(match.group(2)),
                    match.group(3),
                    match.group(4),
                )
                survivor_ids.update(by_key.get(key, []))
        print(f"rebuilt {len(survivor_ids)} survivor ids from logs")
    else:
        survivor_ids = {mid for mid, r in last.items() if r["status"] == "SURVIVED"}
    todo = [manifest[mid] for mid in sorted(survivor_ids) if mid in manifest]
    print(f"final pass over {len(todo)} survivors (gap files force-included)")

    class GapRunner(Runner):
        def select_tests(self, m: dict[str, Any]) -> tuple[list[str], bool]:
            selection, module_level = super().select_tests(m)
            merged = sorted(set(selection) | set(GAP_FILES))
            return merged, module_level

    if args.dry_run:
        print(
            json.dumps(
                {
                    "targets": [m["id"] for m in todo],
                    "gap_files": GAP_FILES,
                    "full": True,
                    "writes": False,
                },
                indent=2,
            )
        )
        return
    import os

    os.environ["MUTATE_FULL_PHASE"] = "1"
    runner = GapRunner(workers=args.workers, max_seconds=None)
    try:
        runner.run(todo)
    finally:
        runner.close()


if __name__ == "__main__":
    main()
