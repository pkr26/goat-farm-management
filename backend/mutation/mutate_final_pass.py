"""Definitive last pass over SURVIVED mutants: gap files always in selection.

The campaign's coverage-guided selection samples at most 60 tests
round-robin across covering files; for heavily-covered lines a killing test
can be left out of the sample, and module-level statements (constants) are
context-attributed to whichever test imported the module first — so literal
pin tests in the gap files never run. This pass re-measures every SURVIVED
mutant against selection = covering-sample(60) ∪ ALL gap test files. A
mutant that still survives this has genuinely no killing test.

Usage: .venv/bin/python mutation/mutate_final_pass.py [--workers N]
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

    manifest = {m["id"]: m for m in json.loads((MUTDIR / "manifest.json").read_text())}
    last: dict[str, dict[str, Any]] = {}
    for line in (MUTDIR / "results.jsonl").read_text().splitlines():
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
    todo = [manifest[mid] for mid in sorted(survivor_ids)]
    print(f"final pass over {len(todo)} survivors (gap files force-included)")

    class GapRunner(Runner):
        def select_tests(self, m: dict[str, Any]) -> tuple[list[str], bool]:
            selection, module_level = super().select_tests(m)
            merged = sorted(set(selection) | set(GAP_FILES))
            return merged, module_level

    # Route through Runner.run — the per-file-locked worker loop. Ad-hoc
    # threads calling execute() directly cross-contaminate same-file mutants
    # (bit twice on 2026-09-30; those passes were discarded).
    for m in todo:
        last.pop(m["id"], None)
    with (MUTDIR / "results.jsonl").open("w") as fh:
        for rec in last.values():
            fh.write(json.dumps(rec) + "\n")
    runner = GapRunner(workers=args.workers, max_seconds=None)
    todo = [m for m in todo if m["id"] not in runner.done_ids]
    if todo:
        runner.run(todo)

    # Dedupe-rewrite (the runner appends; keep last verdict per id).
    records: dict[str, dict[str, Any]] = {}
    for line in (MUTDIR / "results.jsonl").read_text().splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        records[rec["id"]] = rec
    with (MUTDIR / "results.jsonl").open("w") as fh:
        for rec in records.values():
            fh.write(json.dumps(rec) + "\n")
    counter = collections.Counter(r["status"] for r in records.values())
    print(f"final verdicts: {dict(counter)} over {len(records)} mutants")


if __name__ == "__main__":
    main()
