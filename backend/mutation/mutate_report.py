"""Aggregate mutation testing results into a report.

Reads mutation/manifest.json + mutation/results.jsonl, prints per-module and
per-kind scores, and emits mutation/report.md with every surviving mutant
grouped by file (with original/mutated statement diff context).
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
MUTDIR = BACKEND / "mutation"


def main() -> None:
    manifest = {m["id"]: m for m in json.loads((MUTDIR / "manifest.json").read_text())}
    all_results = [json.loads(line) for line in (MUTDIR / "results.jsonl").read_text().splitlines()]
    # Join against the current manifest: ids not in it belong to the purged
    # annotation-only operator set (equivalent mutants, reported separately).
    purged = [r for r in all_results if r["id"] not in manifest]
    results = [r for r in all_results if r["id"] in manifest]
    missing = [mid for mid in manifest if mid not in {r["id"] for r in results}]

    by_status = collections.Counter(r["status"] for r in results)
    ran = [r for r in results if r["status"] not in ("NOT_COVERED",)]
    killed = [r for r in ran if r["status"] in ("KILLED", "TIMEOUT")]
    survived = [r for r in ran if r["status"] == "SURVIVED"]
    errors = [r for r in ran if r["status"] == "RUN_ERROR"]

    def score(rs) -> str:
        if not rs:
            return "n/a"
        return f"{100 * len([r for r in rs if r['status'] in ('KILLED', 'TIMEOUT')]) / len(rs):.1f}%"

    print(f"manifest: {len(manifest)} mutants | run: {len(results)} | missing: {len(missing)} | "
          f"| purged annotation-only records excluded: {len(purged)} "
          f"({dict(collections.Counter(r['status'] for r in purged))})")
    print(f"killed: {by_status['KILLED']} | timeout-killed: {by_status['TIMEOUT']} | "
          f"survived: {by_status['SURVIVED']} | not-covered: {by_status['NOT_COVERED']} | "
          f"errors: {by_status['RUN_ERROR']}")
    print(f"mutation score (run, covered-only): {score(ran)}")

    per_file = collections.defaultdict(list)
    for r in ran:
        per_file[r["file"]].append(r)
    rows = sorted(
        ((f, len(rs), score(rs)) for f, rs in per_file.items()),
        key=lambda x: (x[2].rstrip('%') == 'n/a', float(x[2].rstrip('%') if x[2] != 'n/a' else 0), -x[1]),
    )

    print("\nper-module (worst first):")
    for f, n, s in rows[:40]:
        print(f"  {s:>6}  {n:4d}  {f}")

    by_kind = collections.defaultdict(list)
    for r in ran:
        by_kind[r["kind"]].append(r)
    print("\nper-kind:")
    for k, rs in sorted(by_kind.items()):
        print(f"  {score(rs):>6}  {k:10s} ({len(rs)})")

    out = ["# Backend mutation testing report", ""]
    out.append(f"- mutants in manifest: **{len(manifest)}**")
    out.append(f"- executed: **{len(results)}** "
               f"(killed {by_status['KILLED']}, timeout {by_status['TIMEOUT']}, "
               f"survived {by_status['SURVIVED']}, not-covered {by_status['NOT_COVERED']}, "
               f"errors {by_status['RUN_ERROR']})")
    out.append(f"- **mutation score: {score(ran)}** (killed / executed-with-coverage)")
    out.append("")
    out.append("## Surviving mutants")
    out.append("")
    by_file = collections.defaultdict(list)
    for r in survived:
        m = manifest.get(r["id"], {})
        by_file[r["file"]].append((r, m))
    for f in sorted(by_file):
        out.append(f"### `{f}` ({len(by_file[f])} survivors)")
        out.append("")
        for r, m in sorted(by_file[f], key=lambda x: x[0]["line"]):
            orig = (m.get("orig_stmt") or "").strip()
            mut = (m.get("mut_stmt") or "").strip()
            out.append(f"- **L{r['line']}** `{r['kind']}` {r['detail']} — `{r['id']}`")
            out.append(f"  - `{orig}`")
            out.append(f"  - `{mut}`")
        out.append("")
    if errors:
        out.append("## Runner errors (rerun these)")
        out.append("")
        for r in errors:
            out.append(f"- {r['file']}:{r['line']} {r.get('error', '')}")
        out.append("")
    (MUTDIR / "report.md").write_text("\n".join(out))
    print(f"\nwrote {MUTDIR / 'report.md'}")


if __name__ == "__main__":
    main()
