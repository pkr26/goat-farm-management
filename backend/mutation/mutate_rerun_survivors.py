"""Re-measure every incompatible or inconclusive result as a new attempt.

Fresh source/test/harness/lock/coverage identity invalidates old kills as well
as survivors. Historical receipts remain in the append-only results log.
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

REDO = {"SURVIVED", "NOT_COVERED", "RUN_ERROR", "INFRA_ERROR", "INCONCLUSIVE_TIMEOUT", "TIMEOUT"}


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

    # A fresh campaign re-measures every incompatible result, including old
    # kills. Retain the historical append log as untrusted history.
    runner = Runner(workers=args.workers, max_seconds=None)
    try:
        runner.run([m for m in manifest.values() if m["id"] not in runner.done_ids])
    finally:
        runner.close()


if __name__ == "__main__":
    main()
