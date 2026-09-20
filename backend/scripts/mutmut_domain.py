#!/usr/bin/env python3
"""Run one per-domain mutmut campaign (weekly CI matrix leg).

The committed pyproject.toml keeps the financial-core [tool.mutmut] profile
untouched; this script swaps in a domain fragment from backend/mutmut-configs/
for the duration of one `mutmut run`, then restores pyproject.toml so a
crashed or timed-out leg can never leak a domain scope into the default
profile. Scores are read from mutmut's own stats file and printed as one
machine-greppable line per domain for the workflow summary.

Usage (from backend/):
  .venv/bin/python scripts/mutmut_domain.py --domain breeding_kidding \
      [--timeout-seconds 20000] [--max-children 4] [extra mutmut args...]

Exit codes mirror mutmut: 0 = campaign completed, 124 = window exhausted,
anything else = mutmut failed before/while running.
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PYPROJECT = BACKEND / "pyproject.toml"
CONFIGS = BACKEND / "mutmut-configs"
STATS = BACKEND / "mutants" / "mutmut-stats.json"
RESULTS_CACHE = BACKEND / "mutants"


def replace_mutmut_section(pyproject_text: str, fragment: dict) -> str:
    """Swap the [tool.mutmut] table for the fragment, preserving the rest."""
    lines = pyproject_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    found = False
    while i < len(lines):
        line = lines[i]
        if line.strip() == "[tool.mutmut]":
            found = True
            # emit the fragment instead
            out.append("[tool.mutmut]\n")
            for key, value in fragment.items():
                if isinstance(value, list):
                    out.append(f"{key} = [\n")
                    for item in value:
                        out.append(f'    "{item}",\n')
                    out.append("]\n")
                elif isinstance(value, bool):
                    out.append(f"{key} = {'true' if value else 'false'}\n")
                elif isinstance(value, str):
                    out.append(f'{key} = "{value}"\n')
                else:
                    out.append(f"{key} = {value}\n")
            # skip the old section up to the next top-level [table]
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("["):
                i += 1
            continue
        out.append(line)
        i += 1
    if not found:
        raise SystemExit("pyproject.toml has no [tool.mutmut] section to swap")
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domain", required=True,
                    help="fragment name under mutmut-configs/ (e.g. breeding_kidding)")
    ap.add_argument("--timeout-seconds", type=int, default=20000)
    ap.add_argument("--max-children", type=int, default=4)
    args, mutmut_extra = ap.parse_known_args()

    fragment_path = CONFIGS / f"{args.domain}.toml"
    if not fragment_path.exists():
        raise SystemExit(f"unknown domain {args.domain!r} (no {fragment_path})")
    fragment = tomllib.loads(fragment_path.read_text())

    original = PYPROJECT.read_text()
    PYPROJECT.write_text(replace_mutmut_section(original, fragment))
    shutil.rmtree(RESULTS_CACHE, ignore_errors=True)
    try:
        cmd = [
            str(BACKEND / ".venv/bin/mutmut"), "run",
            "--max-children", str(args.max_children),
            *mutmut_extra,
        ]
        try:
            proc = subprocess.run(cmd, cwd=BACKEND, timeout=args.timeout_seconds)
            status = proc.returncode
        except subprocess.TimeoutExpired:
            # mutmut reacts to SIGTERM by checkpointing classified mutants.
            status = 124
    finally:
        PYPROJECT.write_text(original)

    # Score line: one per domain leg, consumed by the workflow summary step.
    if STATS.exists():
        stats = json.loads(STATS.read_text())
        killed = int(stats.get("killed", 0))
        survived = int(stats.get("survived", 0))
        timeout = int(stats.get("timeout", 0))
        suspicious = int(stats.get("suspicious", 0))
        skipped = int(stats.get("skipped", 0))
        total = killed + survived + timeout + suspicious
        score = (killed / total * 100.0) if total else 0.0
        print(
            f"MUTATION_SCORE domain={args.domain} "
            f"killed={killed} survived={survived} timeout={timeout} "
            f"suspicious={suspicious} skipped={skipped} total={total} "
            f"score={score:.1f}"
        )
    else:
        print(f"MUTATION_SCORE domain={args.domain} no-stats (mutmut exit {status})")
    return status


if __name__ == "__main__":
    sys.exit(main())
