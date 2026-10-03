"""Re-measure formerly logged survivors as new isolated, full-selection attempts.

The log determines candidates only, never a trusted verdict. Current source
fingerprints, clean coverage provenance and passing exact-selection baselines
are enforced by the shared Runner. Results append without deleting history.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

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

    os.environ["MUTATE_FULL_PHASE"] = "1"
    runner = Runner(workers=args.workers, max_seconds=None)
    try:
        runner.run(todo)
    finally:
        runner.close()


if __name__ == "__main__":
    main()
