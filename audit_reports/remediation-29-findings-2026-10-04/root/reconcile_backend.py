"""Recheck complete affected files, then reconcile unique final pytest node IDs.

The original full-run XML and logs are preserved. A failure is resolved only
by a passing later receipt for that exact node; no failed evidence is erased.
Run from the repository root with backend/.venv/bin/python.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def receipts(path: Path) -> dict[str, dict[str, str]]:
    path = path.resolve()
    result: dict[str, dict[str, str]] = {}
    for case in ET.parse(path).iter("testcase"):
        parts = case.attrib["classname"].split(".")
        for end in range(len(parts), 0, -1):
            filename = "/".join(parts[:end]) + ".py"
            if (ROOT / "backend" / filename).is_file():
                node = "::".join([filename, *parts[end:], case.attrib["name"]])
                break
        else:
            raise ValueError(f"Unresolved test class: {case.attrib}")
        state = "passed"
        for failure in ("failure", "error", "skipped"):
            if case.find(failure) is not None:
                state = failure
                break
        # Pytest may produce a separate teardown-error case for a passed call.
        if result.get(node, {}).get("state") in {"failure", "error"}:
            continue
        result[node] = {"state": state, "receipt": str(path.relative_to(OUT))}
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["select", "verify"])
    parser.add_argument("--initial", type=Path, default=OUT / "backend-full.xml")
    parser.add_argument("--later", type=Path, action="append", default=[])
    parser.add_argument("--extra-file", action="append", default=[])
    args = parser.parse_args()
    initial = receipts(args.initial)
    collection = (OUT / "backend-final-collection.log").read_text().splitlines()
    nodes = {line for line in collection if line.startswith("tests/") and "::" in line}
    if args.mode == "select":
        files = {node.split("::")[0] for node, item in initial.items()
                 if item["state"] in {"failure", "error"}}
        files.update(node.split("::")[0] for node in nodes - initial.keys())
        files.update(args.extra_file)
        selection = sorted(files)
        (OUT / "backend-recheck-files.json").write_text(json.dumps(selection, indent=2) + "\n")
        print(json.dumps(selection))
        return
    final = initial.copy()
    for path in args.later:
        final.update(receipts(path))
    missing = sorted(nodes - final.keys())
    failed = {node: final[node] for node in sorted(nodes & final.keys())
              if final[node]["state"] in {"failure", "error"}}
    counts = {state: sum(final[node]["state"] == state for node in nodes & final.keys())
              for state in ("passed", "skipped", "failure", "error")}
    report = {
        "collected": len(nodes), "counts": counts, "missing": missing,
        "unresolved": failed, "extra_receipt_nodes": sorted(final.keys() - nodes),
        "initial_problem_nodes": {node: entry for node, entry in initial.items()
                                  if entry["state"] in {"failure", "error"}},
        "final_receipts": {node: final[node] for node in sorted(nodes & final.keys())},
        "status": "PASS" if not missing and not failed else "FAIL",
    }
    (OUT / "backend-reconciled-results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "collected", "counts", "missing", "unresolved")}, indent=2))
    if missing or failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
