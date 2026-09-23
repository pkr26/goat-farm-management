"""Mutation-test runner: coverage-guided test selection, parallel workers.

Usage:
    .venv/bin/python mutation/mutate_run.py [--workers N] [--max-seconds S]
        [--tiers 1,2] [--limit N] [--status-filter all|pending]

Safety model:
  * mutants are applied IN PLACE to app/ sources, then byte-exact restored
    (sha256-verified) in a finally block;
  * a per-file checkout lock guarantees no two workers mutate the same file
    concurrently;
  * every worker runs pytest as a subprocess with its own throwaway Postgres
    database (GOATFARM_TEST_DB=goatfarm_mut<i>_test) and a hard timeout —
    timeouts count as killed (hang mutants);
  * results append to mutation/results.jsonl — re-runs resume (id dedupe).
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
MUTDIR = BACKEND / "mutation"
VENV_PY = BACKEND / ".venv" / "bin" / "python"

SAMPLE_CAP = 60  # phase-1 sampled tests
FULL_CAP = 400  # phase-2 escalation cap
MODULE_LEVEL_FILE_SAMPLES = 3  # whole test files for module-level mutants


def load_coverage_contexts() -> dict[str, dict[int, set[str]]]:
    import coverage

    cov = coverage.Coverage(data_file=str(BACKEND / ".coverage-mut"))
    cov.load()
    data = cov.get_data()
    by_file: dict[str, dict[int, set[str]]] = {}
    for f in data.measured_files():
        rel = str(Path(f).relative_to(BACKEND))
        by_file[rel] = {
            line: {c.rsplit("|", 1)[0] for c in ctxs if c}
            for line, ctxs in (data.contexts_by_lineno(f) or {}).items()
        }
    return by_file


def file_popularity(ctx: dict[str, dict[int, set[str]]]) -> dict[str, dict[str, int]]:
    """src file -> {test file: covering-line count} for import-level mutants."""
    pop: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for src, lines in ctx.items():
        per_tf_lines: dict[str, set[int]] = collections.defaultdict(set)
        for line, tests in lines.items():
            for t in tests:
                per_tf_lines[t.split("::")[0]].add(line)
        for tf, lns in per_tf_lines.items():
            pop[src][tf] = len(lns)
    return {k: dict(v) for k, v in pop.items()}


def tests_per_file(ctx: dict[str, dict[int, set[str]]]) -> dict[str, int]:
    n: dict[str, set[str]] = collections.defaultdict(set)
    for lines in ctx.values():
        for tests in lines.values():
            for t in tests:
                n[t.split("::")[0]].add(t)
    return {f: len(s) for f, s in n.items()}


def sample_tests(tests: list[str], cap: int) -> list[str]:
    if len(tests) <= cap:
        return tests
    by_file: dict[str, list[str]] = collections.defaultdict(list)
    for t in tests:
        by_file[t.split("::")[0]].append(t)
    picked: list[str] = []
    files = sorted(by_file)
    i = 0
    while len(picked) < cap:
        progressed = False
        for f in files:
            if i < len(by_file[f]) and len(picked) < cap:
                picked.append(by_file[f][i])
                progressed = True
        if not progressed:
            break
        i += 1
    return picked


class Runner:
    def __init__(self, workers: int, max_seconds: float | None):
        self.workers = workers
        self.deadline = time.monotonic() + max_seconds if max_seconds else None
        self.ctx = load_coverage_contexts()
        self.pop = file_popularity(self.ctx)
        self.tpf = tests_per_file(self.ctx)
        self.file_locks: dict[str, threading.Lock] = {}
        self.locks_guard = threading.Lock()
        self.stop = threading.Event()
        self.counter = collections.Counter()
        self.print_lock = threading.Lock()
        self.results_path = MUTDIR / "results.jsonl"
        self.done_ids: set[str] = set()
        if self.results_path.exists():
            for line in self.results_path.read_text().splitlines():
                try:
                    self.done_ids.add(json.loads(line)["id"])
                except Exception:
                    pass

    def lock_for(self, rel: str) -> threading.Lock:
        with self.locks_guard:
            return self.file_locks.setdefault(rel, threading.Lock())

    def select_tests(self, m: dict) -> tuple[list[str], bool]:
        """Return (selection, module_level)."""
        lines = self.ctx.get(m["file"], {})
        tests = sorted(lines.get(m["line"], set()))
        if tests:
            return tests, False
        # No test covers the mutated line directly. For import-time statements
        # (module/class scope — constants, frozensets) run small focused test
        # files: rank by module-lines-covered per test (a 300-test file that
        # covers the module is a poor pick next to a 12-test one that does).
        if m.get("scope") == "module" and lines:
            eff = [
                (f, cov / max(1, self.tpf.get(f, 1)))
                for f, cov in self.pop.get(m["file"], {}).items()
            ]
            top = sorted(eff, key=lambda kv: -kv[1])[:MODULE_LEVEL_FILE_SAMPLES]
            return [f for f, _ in top], True
        return [], False

    def run_pytest(self, node_ids: list[str], worker: int) -> tuple[str, str, float]:
        env = os.environ.copy()
        env["GOATFARM_TEST_DB"] = f"goatfarm_mut{worker}_test"
        env.pop("COVERAGE_FILE", None)
        cmd = [
            str(VENV_PY),
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
            out, _ = proc.communicate(timeout=300)
            status = "fail" if proc.returncode != 0 else "pass"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            out, _ = proc.communicate()
            status = "timeout"
        return status, out or "", time.monotonic() - t0

    def first_failure(self, out: str) -> str:
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("FAILED") or line.startswith("ERROR"):
                return line[:220]
        for line in out.splitlines():
            if "::_" not in line and ("/tests/" in line or line.startswith("E ")):
                return line[:220]
        return out.strip().splitlines()[-1][:220] if out.strip() else ""

    def apply_mutant(self, m: dict) -> str:
        """Splice the mutated statement into the file; returns mutated text."""
        path = BACKEND / m["file"]
        source = path.read_text()
        lines = source.splitlines(keepends=True)
        s, e = m["stmt_line"] - 1, m["stmt_end_line"]  # half-open span
        replacement = " " * m["stmt_col"] + m["mut_stmt"] + "\n"
        return "".join(lines[:s]) + replacement + "".join(lines[e:])

    def execute(self, m: dict, worker: int) -> dict:
        path = BACKEND / m["file"]
        original = path.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        rec: dict = dict(
            id=m["id"],
            file=m["file"],
            line=m["line"],
            kind=m["kind"],
            detail=m["detail"],
            tier=m["tier"],
            stmt_line=m["stmt_line"],
        )
        try:
            path.write_text(self.apply_mutant(m))
            selection, module_level = self.select_tests(m)
            rec["module_level"] = module_level
            if not selection:
                rec["status"] = "NOT_COVERED"
                rec["n_tests"] = 0
                return rec
            phase1 = sample_tests(selection, SAMPLE_CAP)
            rec["n_tests"] = len(selection)
            # escalating nested phases: a kill usually falls in the first few
            # tests; survivors pay for the capped spread (60 tests across all
            # covering files) — enough spread that a 4th full phase adds cost
            # without changing verdicts.
            phases = (
                [FULL_CAP]
                if os.environ.get("MUTATE_FULL_PHASE")
                else [15, SAMPLE_CAP]
            )
            total_dur = 0.0
            for pi, cap in enumerate(phases, start=1):
                subset = sample_tests(selection, cap)
                status, out, dur = self.run_pytest(subset, worker)
                total_dur += dur
                if status == "fail":
                    rec.update(
                        status="KILLED",
                        phase=pi,
                        dur=round(total_dur, 1),
                        fail=self.first_failure(out),
                    )
                    return rec
                if status == "timeout":
                    rec.update(status="TIMEOUT", phase=pi, dur=round(total_dur, 1))
                    return rec
                if len(selection) <= cap:
                    rec.update(status="SURVIVED", phase=pi, dur=round(total_dur, 1))
                    return rec
            rec.update(status="SURVIVED", phase=len(phases), dur=round(total_dur, 1))
            return rec
        finally:
            path.write_bytes(original)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"RESTORE FAILED for {m['file']}")

    def worker_loop(self, worker: int, queue: "collections.deque[dict]", qlock: threading.Lock):
        while not self.stop.is_set():
            if self.deadline and time.monotonic() > self.deadline:
                return
            m = None
            held: str | None = None
            with qlock:
                for cand in queue:
                    if cand is None:
                        continue
                    lock = self.lock_for(cand["file"])
                    if not lock.acquire(blocking=False):
                        continue
                    held = cand["file"]
                    m = cand
                    break
                if m is not None:
                    queue.remove(m)
            if m is None:
                return
            try:
                try:
                    rec = self.execute(m, worker)
                except Exception as exc:  # runner-level error: record, keep going
                    rec = dict(
                        id=m["id"],
                        file=m["file"],
                        line=m["line"],
                        kind=m["kind"],
                        detail=m["detail"],
                        tier=m["tier"],
                        status="RUN_ERROR",
                        error=repr(exc)[:300],
                    )
                with self.print_lock:
                    self.counter[rec["status"]] += 1
                    self.counter["total"] += 1
                    print(
                        f"[w{worker}] {rec['status']:11s} {m['file']}:{m['line']} "
                        f"{m['kind']}/{m['detail']} ({self.counter['total']})",
                        flush=True,
                    )
                    with open(self.results_path, "a") as fh:
                        fh.write(json.dumps(rec) + "\n")
            finally:
                if held is not None:
                    self.lock_for(held).release()

    def run(self, mutants: list[dict]):
        queue = collections.deque(mutants)
        qlock = threading.Lock()
        threads = [
            threading.Thread(target=self.worker_loop, args=(i, queue, qlock), daemon=True)
            for i in range(self.workers)
        ]
        t0 = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        dt = time.monotonic() - t0
        print(f"\n== done in {dt/60:.1f} min: {dict(self.counter)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-seconds", type=float, default=None)
    ap.add_argument("--tiers", type=str, default="1,2,3")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--files", type=str, default=None, help="comma-separated file filter")
    ap.add_argument("--kinds", type=str, default=None)
    args = ap.parse_args()

    manifest = json.loads((MUTDIR / "manifest.json").read_text())
    tiers = {int(t) for t in args.tiers.split(",")}
    todo = [m for m in manifest if m["tier"] in tiers]
    if args.files:
        pats = args.files.split(",")
        todo = [m for m in todo if any(p in m["file"] for p in pats)]
    if args.kinds:
        kinds = set(args.kinds.split(","))
        todo = [m for m in todo if m["kind"] in kinds]
    runner = Runner(args.workers, args.max_seconds)
    todo = [m for m in todo if m["id"] not in runner.done_ids]
    # cheapest-first inside each tier: fewest covering tests = fastest kills
    def cost(m: dict) -> tuple:
        lines = runner.ctx.get(m["file"], {})
        return (m["tier"], len(lines.get(m["line"], ())) or 10_000)

    todo.sort(key=cost)
    if args.limit:
        todo = todo[: args.limit]
    print(f"mutants to run: {len(todo)} (skipping {len(runner.done_ids)} already done)")
    if todo:
        runner.run(todo)


if __name__ == "__main__":
    main()
