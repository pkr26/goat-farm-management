"""Verify the edge verdicts: TIMEOUTs and full-selection survivor runs.

Two verification gaps this closes (2026-09-30 campaign):

* TIMEOUT verdicts were never re-measured — a timeout at the runner's 300 s
  cap can be a true hang OR a slow-but-finite selection (especially under a
  loaded machine). Each timeout mutant is re-run with a single FULL phase
  and a generous budget: completing yields a real KILLED/SURVIVED verdict;
  hitting the long budget again confirms a true hang (stays TIMEOUT).
* SURVIVED verdicts from the final pass were measured against a 60-test
  spread plus the gap files — not the full (cap-400) covering selection.
  Each survivor is re-run with MUTATE_FULL_PHASE=1; a load-induced timeout
  is INCONCLUSIVE (the prior SURVIVED verdict stands, reported separately).

Both routes go through Runner.run — the per-file-locked worker loop.

Usage:
    .venv/bin/python mutation/mutate_verify_extremes.py --status TIMEOUT \
        [--workers 5] [--timeout 1200]
    .venv/bin/python mutation/mutate_verify_extremes.py --status SURVIVED \
        [--workers 5] [--timeout 900]
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
MUTDIR = BACKEND / "mutation"
sys.path.insert(0, str(MUTDIR))
from mutate_final_pass import GAP_FILES  # noqa: E402
from mutate_run import Runner  # noqa: E402


def build_runner(workers: int, phase_timeout: float) -> type[Runner]:
    class VerifyRunner(Runner):
        def select_tests(self, m: dict[str, Any]) -> tuple[list[str], bool]:
            selection, module_level = super().select_tests(m)
            return sorted(set(selection) | set(GAP_FILES)), module_level

        def run_pytest(self, node_ids: list[str], worker: int) -> tuple[str, str, float]:
            import subprocess

            env = os.environ.copy()
            env["GOATFARM_TEST_DB"] = f"goatfarm_mut{worker}_test"
            env.pop("COVERAGE_FILE", None)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            cmd = [
                str(BACKEND / ".venv" / "bin" / "python"),
                "-m",
                "pytest",
                "-q",
                "--no-header",
                "--tb=line",
                "-x",
                "-p",
                "no:cacheprovider",
                "--color=no",
                *node_ids,
            ]
            t0 = time.monotonic()
            proc = subprocess.Popen(
                cmd,
                cwd=str(BACKEND),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            try:
                out, _ = proc.communicate(timeout=phase_timeout)
                status = "fail" if proc.returncode != 0 else "pass"
            except subprocess.TimeoutExpired:
                import contextlib
                import signal

                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                out, _ = proc.communicate()
                status = "timeout"
            return status, out or "", time.monotonic() - t0

    return VerifyRunner


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", choices=["TIMEOUT", "SURVIVED"], required=True)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=1200.0)
    args = ap.parse_args()

    manifest = {m["id"]: m for m in json.loads((MUTDIR / "manifest.json").read_text())}
    last: dict[str, dict[str, Any]] = {}
    for line in (MUTDIR / "results.jsonl").read_text().splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        last[rec["id"]] = rec
    todo_ids = sorted(mid for mid, r in last.items() if r["status"] == args.status)
    todo = [manifest[mid] for mid in todo_ids]
    print(f"verifying {len(todo)} {args.status} mutants (full phase, "
          f"{args.timeout:.0f}s budget, gap files included)")

    # Single FULL phase for every mutant; a timeout in this pass is not a
    # kill (see module docstring) — map it to the honest verdict per status.
    os.environ["MUTATE_FULL_PHASE"] = "1"
    for m in todo:
        last.pop(m["id"], None)
    with (MUTDIR / "results.jsonl").open("w") as fh:
        for rec in last.values():
            fh.write(json.dumps(rec) + "\n")

    runner_cls = build_runner(args.workers, args.timeout)
    runner = runner_cls(workers=args.workers, max_seconds=None)
    runner.results_path = MUTDIR / "verify_extremes.jsonl"
    todo = [m for m in todo if m["id"] not in runner.done_ids]
    if todo:
        runner.run(todo)

    # Fold the verification records back with timeout semantics fixed.
    verdicts: dict[str, dict[str, Any]] = {}
    for line in runner.results_path.read_text().splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        verdicts[rec["id"]] = rec
    fixed: dict[str, dict[str, Any]] = {}
    for mid, rec in verdicts.items():
        if rec["status"] == "TIMEOUT":
            if args.status == "TIMEOUT":
                rec = {**rec, "status": "TIMEOUT", "note": "hang confirmed at long budget"}
            else:
                rec = {
                    **rec,
                    "status": "SURVIVED",
                    "note": "full-selection verification timed out under load; "
                    "prior SURVIVED verdict stands",
                }
        fixed[mid] = rec
        last[mid] = rec
    with (MUTDIR / "results.jsonl").open("w") as fh:
        for rec in last.values():
            fh.write(json.dumps(rec) + "\n")
    counter = collections.Counter(r["status"] for r in fixed.values())
    print(f"\n{args.status} verification: {dict(counter)}")


if __name__ == "__main__":
    main()
