"""Full-selection re-verification of every SURVIVED mutant.

The campaign's phase-2 caps a survivor's covering selection at 60 tests
(spread round-robin across covering files); the 2026-09-22 campaign showed a
false-survivor rate at that cap. This script re-runs every SURVIVED mutant
against its FULL covering selection (capped at 400 by sample_tests) with
MUTATE_FULL_PHASE=1, then rewrites results.jsonl deduped by id (last record
wins).

The survivor set is rebuilt from campaign.log (`[wN] SURVIVED file:line
kind/detail (n)` lines carry every field needed to re-derive the manifest
id), so the script is idempotent against a results.jsonl that a previous
re-verify pass already rewrote.

Execution goes through Runner.run — the same per-file-locked worker loop the
campaign uses. NEVER call Runner.execute from ad-hoc threads: concurrent
unlocked mutations of one file cross-contaminate verdicts (and have, on
2026-09-30; that run was discarded and the tree restored from git).

Usage:
    .venv/bin/python mutation/mutate_reverify.py [--workers N] [--dry-run]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
MUTDIR = BACKEND / "mutation"
sys.path.insert(0, str(MUTDIR))
from mutate_run import Runner  # noqa: E402

SURVIVOR_LINE = re.compile(
    r"^\[w\d+\]\s+SURVIVED\s+(\S+):(\d+)\s+(\S+)/(\S.*?)\s+\(\d+\)\s*$",
)


def survivor_ids_from_log() -> set[str]:
    manifest = json.loads((MUTDIR / "manifest.json").read_text())
    by_key: dict[tuple[str, int, str, str], list[str]] = collections.defaultdict(list)
    for m in manifest:
        by_key[(m["file"], m["line"], m["kind"], m["detail"])].append(m["id"])

    ids: set[str] = set()
    ambiguous = 0
    for line in (MUTDIR / "campaign.log").read_text().splitlines():
        match = SURVIVOR_LINE.match(line.strip())
        if not match:
            continue
        file = match.group(1)
        lineno = int(match.group(2))
        kind, detail = match.group(3), match.group(4)
        candidates = by_key.get((file, lineno, kind, detail), [])
        if candidates:
            ids.update(candidates)
            if len(candidates) > 1:
                ambiguous += 1
        else:
            print(f"  !! no manifest match for campaign.log line: {line.strip()}")
    if ambiguous:
        print(f"  !! {ambiguous} survivor lines matched multiple manifest ids (all included)")
    return ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ids = survivor_ids_from_log()
    print(f"survivors rebuilt from campaign.log: {len(ids)}")

    manifest = {m["id"]: m for m in json.loads((MUTDIR / "manifest.json").read_text())}
    todo = [manifest[mid] for mid in sorted(ids)]
    if args.dry_run:
        for m in todo[:8]:
            print("  would re-verify", m["file"], m["line"], m["kind"], m["detail"])
        return

    results_path = MUTDIR / "results.jsonl"
    records: dict[str, dict[str, Any]] = {}
    for line in results_path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        records[rec["id"]] = rec  # last wins
    drop = sum(1 for mid in ids if records.pop(mid, None) is not None)
    with results_path.open("w") as fh:
        for rec in records.values():
            fh.write(json.dumps(rec) + "\n")
    print(f"dropped {drop} stale survivor records; {len(records)} settled verdicts kept")

    os.environ["MUTATE_FULL_PHASE"] = "1"
    runner = Runner(workers=args.workers, max_seconds=None)
    todo = [m for m in todo if m["id"] not in runner.done_ids]
    print(f"mutants to re-verify: {len(todo)}")
    if todo:
        runner.run(todo)

    # Dedupe-rewrite (the runner appends; keep last verdict per id).
    records = {}
    for line in results_path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        records[rec["id"]] = rec
    with results_path.open("w") as fh:
        for rec in records.values():
            fh.write(json.dumps(rec) + "\n")
    verdicts = collections.Counter(r["status"] for r in records.values())
    print(f"final verdicts: {dict(verdicts)} over {len(records)} mutants")


if __name__ == "__main__":
    main()
