#!/usr/bin/env python3
"""Render per-campaign verdict tables from results/all.jsonl (report source of truth)."""

import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
rows = defaultdict(dict)  # (campaign, id) -> latest record
for line in (HERE / "results/all.jsonl").read_text().splitlines():
    if not line.strip():
        continue
    rec = json.loads(line)
    rows[(rec["campaign"], rec["id"])] = rec

by_campaign = defaultdict(list)
for (c, _), rec in rows.items():
    by_campaign[c].append(rec)

for c in sorted(by_campaign):
    recs = sorted(by_campaign[c], key=lambda r: r["id"])
    counts = defaultdict(int)
    for r in recs:
        counts[r["verdict"]] += 1
    print(f"## {c} — {len(recs)} mutants")
    for v, n in sorted(counts.items()):
        print(f"    {v}: {n}")
    for r in recs:
        if r["verdict"].startswith("SURVIVED") or r["verdict"].startswith("TIMEOUT"):
            ft = (r.get("failing_tests") or [""])[0][:70]
            print(f"  SURVIVOR {r['id']:28s} {r['description'][:80]}")
    print()
