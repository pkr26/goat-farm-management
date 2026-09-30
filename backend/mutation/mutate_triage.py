"""Bucket survivors into actionable classes for the fix phase.

Reads manifest + results.jsonl, groups every SURVIVED mutant by file, and
tags each with a heuristic class so triage can proceed class-first:

  * log/format-only   — mutation only changes a log line or display string
  * message-trunc     — message truncation counts / ellipsis thresholds
  * timing/noise      — log attempt counters, sleep arithmetic in logs
  * boundary          — <= / >= / ±1 on a real decision boundary
  * scoping           — tenant/identity predicate in SQL (== -> !=)
  * permissions       — mode bits / file permission arithmetic
  * plain             — everything else: inspect individually

Usage: .venv/bin/python mutation/mutate_triage.py [--file SUBSTR]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from typing import Any

MUTDIR = Path(__file__).resolve().parent.parent / "mutation"

LOG_HINTS = re.compile(r"logger\.|logging\.|_logger|log\.", re.I)


def classify(m: dict[str, Any], rec: dict[str, Any]) -> str:
    stmt = m.get("orig_stmt") or ""
    if "logger" in stmt or "_logger" in stmt:
        return "log/format-only"
    if "…" in stmt or "[:5]" in stmt or "ellipsis" in stmt:
        return "message-trunc"
    if "chmod" in stmt or "0o600" in stmt or "0o700" in stmt or "st_mode" in stmt:
        return "permissions"
    if re.search(r"farm_id ==|\.farm_id\b", stmt) and rec["kind"] == "compare":
        return "scoping"
    if rec["kind"] == "compare" or (rec["kind"] == "intconst" and "if " not in stmt):
        return "boundary"
    return "plain"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=None)
    args = ap.parse_args()

    manifest = {m["id"]: m for m in json.loads((MUTDIR / "manifest.json").read_text())}
    last: dict[str, dict[str, Any]] = {}
    for line in (MUTDIR / "results.jsonl").read_text().splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        last[rec["id"]] = rec

    survivors = [r for r in last.values() if r["status"] == "SURVIVED"]
    if args.file:
        survivors = [r for r in survivors if args.file in r["file"]]

    by_class: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for rec in survivors:
        m = manifest.get(rec["id"], {})
        by_class[classify(m, rec)].append(rec)

    print(f"survivors: {len(survivors)}")
    for cls in sorted(by_class, key=lambda c: -len(by_class[c])):
        print(f"\n== {cls} ({len(by_class[cls])}) ==")
        by_file = collections.Counter(r["file"] for r in by_class[cls])
        for f, c in by_file.most_common():
            print(f"  {c:4d}  {f}")


if __name__ == "__main__":
    main()
