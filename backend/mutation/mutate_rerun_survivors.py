"""Re-run the campaign for every mutant not yet KILLED/TIMEOUT.

After new gap-killing tests land, a fresh contexts baseline must be collected
first (`pytest --cov=app --cov-context=test`) so the runner's coverage-guided
selection sees the new tests. This driver then:
  * rewrites results.jsonl WITHOUT the SURVIVED / NOT_COVERED / RUN_ERROR
    records (they must be re-measured; KILLED verdicts stay valid),
  * runs the runner over exactly those ids.

Usage:
    .venv/bin/python mutation/mutate_rerun_survivors.py [--workers N] [--dry-run]
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

MUTDIR = Path(__file__).resolve().parent.parent / "mutation"
sys.path.insert(0, str(MUTDIR))
from mutate_run import Runner  # noqa: E402

REDO = {"SURVIVED", "NOT_COVERED", "RUN_ERROR"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    results_path = MUTDIR / "results.jsonl"
    last: dict[str, dict[str, Any]] = {}
    for line in results_path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        last[rec["id"]] = rec  # last record per id wins
    counts = collections.Counter(r["status"] for r in last.values())
    redo_ids = {mid for mid, r in last.items() if r["status"] in REDO}
    print(f"current verdicts: {dict(counts)}")
    print(f"will re-measure {len(redo_ids)} mutants ({', '.join(sorted(REDO))})")

    manifest = {m["id"]: m for m in json.loads((MUTDIR / "manifest.json").read_text())}
    todo = [manifest[mid] for mid in sorted(redo_ids) if mid in manifest]
    if args.dry_run:
        for m in todo[:8]:
            print("  redo:", m["file"], m["line"], m["kind"], m["detail"])
        return

    with results_path.open("w") as fh:
        for mid, rec in last.items():
            if mid not in redo_ids:
                fh.write(json.dumps(rec) + "\n")
    print(f"rewrote {results_path} with {len(last) - len(redo_ids)} settled verdicts")

    runner = Runner(workers=args.workers, max_seconds=None)
    todo = [m for m in todo if m["id"] not in runner.done_ids]
    print(f"mutants to run: {len(todo)}")
    if todo:
        runner.run(todo)


if __name__ == "__main__":
    main()
