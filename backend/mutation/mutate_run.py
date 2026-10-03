"""Isolated mutation runner with clean-selection baselines and attempt receipts.

Every worker mutates a private immutable checkout snapshot and uses a unique
throwaway database. Only structured assertion failures against a passing exact
selection baseline count as kills. Timeouts and infrastructure failures remain
inconclusive; incompatible historical receipts cannot be resumed or scored.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import textwrap
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from mutate_identity import digest_json, input_identity, latest_compatible, read_results, sha_file

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
    provenance = json.loads((BACKEND / ".coverage-mut.provenance.json").read_text())
    source_root = Path(provenance.get("source_root", str(BACKEND)))
    by_file: dict[str, dict[int, set[str]]] = {}
    for f in data.measured_files():
        rel = Path(f).relative_to(source_root).as_posix()
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


def classify_pytest(exit_code: int, receipt: dict[str, Any] | None) -> str:
    if receipt is None or receipt.get("exit_code") != exit_code:
        return "infra"
    if receipt.get("collection_errors") or receipt.get("collected", 0) == 0:
        return "infra"
    reports = receipt.get("reports", [])
    failures = [r for r in reports if r["outcome"] == "failed"]
    if (
        exit_code == 0
        and not failures
        and any(r["when"] == "call" and r["outcome"] == "passed" for r in reports)
    ):
        return "pass"
    if (
        exit_code == 1
        and failures
        and all(r["when"] == "call" and r.get("assertion") for r in failures)
    ):
        return "kill"
    return "infra"


def snapshot_ignore(directory: str, names: list[str]) -> set[str]:
    excluded = {
        ".git",
        ".venv",
        "node_modules",
        ".next",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "secrets",
        "keys",
        "audit_reports",
    }
    return {
        name
        for name in names
        if name in excluded
        or name.startswith(".coverage")
        or (name.startswith(".env") and not name.endswith(".example"))
        or name.endswith((".pyc", ".dump", ".dump.gpg"))
    }


class Runner:
    def __init__(self, workers: int, max_seconds: float | None, *, phase_timeout: float = 300):
        if workers < 1:
            raise ValueError("workers must be positive")
        self.workers = workers
        self.phase_timeout = phase_timeout
        self.deadline = time.monotonic() + max_seconds if max_seconds else None
        self.run_id = uuid.uuid4().hex
        self.inputs = input_identity(BACKEND)
        self.identity = {
            "schema": 2,
            **self.inputs,
            "manifest_sha256": sha_file(MUTDIR / "manifest.json"),
            "coverage_sha256": sha_file(BACKEND / ".coverage-mut"),
            "config": {
                "workers": workers,
                "phase_timeout": phase_timeout,
                "full": bool(os.environ.get("MUTATE_FULL_PHASE")),
                "sample_cap": SAMPLE_CAP,
            },
        }
        provenance_path = BACKEND / ".coverage-mut.provenance.json"
        try:
            coverage_provenance = json.loads(provenance_path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(
                "coverage has no clean-baseline provenance; run mutation/mutate_cover.py"
            ) from exc
        if (
            coverage_provenance.get("inputs") != self.inputs
            or coverage_provenance.get("coverage_sha256") != self.identity["coverage_sha256"]
            or coverage_provenance.get("baseline_exit_code") != 0
        ):
            raise ValueError("coverage source/tests/locks are stale; run mutation/mutate_cover.py")
        self.identity["coverage_provenance_sha256"] = sha_file(provenance_path)
        self.campaign_id = digest_json(self.identity)
        self.ctx = load_coverage_contexts()
        self.pop = file_popularity(self.ctx)
        self.tpf = tests_per_file(self.ctx)
        self.stop = threading.Event()
        self.counter: collections.Counter[str] = collections.Counter()
        self.print_lock = threading.Lock()
        self.results_path = MUTDIR / "results.jsonl"
        manifest = {m["id"]: m for m in json.loads((MUTDIR / "manifest.json").read_text())}
        self.done_ids = {
            mid
            for mid, rec in latest_compatible(
                read_results(self.results_path), self.campaign_id, manifest
            ).items()
            if rec.get("status") in {"KILLED", "SURVIVED", "INVALID"}
        }
        self._snapshot_tmp = tempfile.TemporaryDirectory(prefix="herdly-mutation-snapshot-")
        self.snapshot = Path(self._snapshot_tmp.name) / "repo"
        try:
            shutil.copytree(BACKEND.parent, self.snapshot, ignore=snapshot_ignore)
            # Reject a mixed snapshot taken while editors changed source/tests.
            if input_identity(self.snapshot / BACKEND.name) != self.inputs:
                raise RuntimeError(
                    "source/tests changed while snapshotting; retry a stable checkout"
                )
        except BaseException:
            self._snapshot_tmp.cleanup()
            raise
        self.local = threading.local()
        self.baselines: dict[str, tuple[str, str, float]] = {}
        self.baseline_lock = threading.Lock()

    def close(self) -> None:
        self._snapshot_tmp.cleanup()

    def select_tests(self, m: dict[str, Any]) -> tuple[list[str], bool]:
        lines = self.ctx.get(m["file"], {})
        tests = sorted(lines.get(m["line"], set()))
        if tests:
            return tests, False
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
        env["GOATFARM_TEST_DB"] = f"herdly_mut_{self.run_id[:10]}_{uuid.uuid4().hex[:8]}_test"
        env.pop("COVERAGE_FILE", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(self.local.workspace)
        receipt_path = self.local.workspace / f"receipt-{uuid.uuid4().hex}.json"
        env["MUTATION_RECEIPT_PATH"] = str(receipt_path)
        command = [
            str(VENV_PY),
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "--tb=line",
            "-x",
            "-p",
            "no:cacheprovider",
            "-p",
            "mutation.pytest_receipt",
            "--color=no",
            *node_ids,
        ]
        t0 = time.monotonic()
        try:
            proc = subprocess.Popen(
                command,
                cwd=self.local.workspace,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            return "infra", repr(exc), time.monotonic() - t0
        try:
            out, _ = proc.communicate(timeout=self.phase_timeout)
            try:
                receipt = json.loads(receipt_path.read_text())
            except (OSError, ValueError):
                receipt = None
            self.local.pytest_receipt = receipt
            status = classify_pytest(proc.returncode, receipt)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            status = "timeout"
            self.local.pytest_receipt = None
        return status, out or "", time.monotonic() - t0

    def apply_mutant(self, m: dict[str, Any]) -> str:
        path = self.local.workspace / m["file"]
        original = path.read_bytes()
        if not m.get("source_sha256") or sha_file(path) != m["source_sha256"]:
            raise ValueError("stale/missing source fingerprint; regenerate the manifest")
        source = original.decode("utf-8")
        lines = source.splitlines(keepends=True)
        s, e = m["stmt_line"] - 1, m["stmt_end_line"]
        if not m.get("orig_span") or "".join(lines[s:e]) != m["orig_span"]:
            raise ValueError("original statement bytes do not match manifest")
        replacement = textwrap.indent(str(m["mut_stmt"]), " " * int(m["stmt_col"])) + "\n"
        mutated = "".join(lines[:s]) + replacement + "".join(lines[e:])
        compile(mutated, str(path), "exec")
        return mutated

    def first_failure(self, out: str) -> str:
        return next(
            (
                line.strip()[:220]
                for line in out.splitlines()
                if line.strip().startswith(("FAILED", "ERROR"))
            ),
            out[-220:],
        )

    def execute(self, m: dict[str, Any], worker: int) -> dict[str, Any]:
        rec = {key: m[key] for key in ("id", "file", "line", "kind", "detail", "tier")}
        rec.update(
            campaign_id=self.campaign_id,
            run_id=self.run_id,
            attempt_id=uuid.uuid4().hex,
            mutant_digest=digest_json(m),
            provenance=self.identity,
            status_policy="assertions-only-timeouts-inconclusive",
        )
        with tempfile.TemporaryDirectory(prefix="herdly-mutant-") as temporary:
            workspace = Path(temporary) / "repo"
            shutil.copytree(self.snapshot, workspace)
            self.local.workspace = workspace / BACKEND.name
            path = self.local.workspace / m["file"]
            if not path.resolve().is_relative_to((self.local.workspace / "app").resolve()):
                return {
                    **rec,
                    "status": "INVALID",
                    "error": "mutation target must stay inside app/",
                }
            original = path.read_bytes()
            try:
                mutated = self.apply_mutant(m)
            except (ValueError, SyntaxError, UnicodeError) as exc:
                return {**rec, "status": "INVALID", "error": str(exc)}
            selection, module_level = self.select_tests(m)
            rec.update(n_tests=len(selection), module_level=module_level)
            if not selection:
                return {**rec, "status": "NOT_COVERED", "selection_mode": "uncovered"}
            phases = [len(selection)] if os.environ.get("MUTATE_FULL_PHASE") else [15, SAMPLE_CAP]
            total_duration = 0.0
            for phase, cap in enumerate(phases, 1):
                subset = sample_tests(selection, cap)
                rec.update(
                    selection=subset,
                    selection_sha256=digest_json(subset),
                    selection_mode="module-fallback"
                    if module_level
                    else ("complete" if len(subset) == len(selection) else "sampled"),
                )
                baseline_key = digest_json([self.campaign_id, subset])
                # The baseline always runs against clean snapshot bytes, with
                # exactly the mutant's selection and process/resource limits.
                path.write_bytes(original)
                with self.baseline_lock:
                    baseline = self.baselines.get(baseline_key)
                    if baseline is None:
                        baseline = self.run_pytest(subset, worker)
                        if baseline[0] == "pass":
                            self.baselines[baseline_key] = baseline
                rec["baseline"] = {
                    "status": baseline[0],
                    "duration": baseline[2],
                    "selection_sha256": digest_json(subset),
                }
                if baseline[0] != "pass":
                    return {
                        **rec,
                        "status": "INCONCLUSIVE_TIMEOUT"
                        if baseline[0] == "timeout"
                        else "INFRA_ERROR",
                        "error": "clean baseline did not pass",
                        "fail": self.first_failure(baseline[1]),
                    }
                path.write_text(mutated)
                status, out, duration = self.run_pytest(subset, worker)
                total_duration += duration
                rec.update(
                    phase=phase,
                    dur=round(total_duration, 2),
                    pytest_receipt=getattr(self.local, "pytest_receipt", None),
                )
                if status != "pass":
                    return {
                        **rec,
                        "status": {
                            "kill": "KILLED",
                            "timeout": "INCONCLUSIVE_TIMEOUT",
                            "infra": "INFRA_ERROR",
                        }[status],
                        "fail": self.first_failure(out),
                    }
                if len(selection) <= cap:
                    break
            return {**rec, "status": "SURVIVED"}

    def worker_loop(
        self, worker: int, queue: collections.deque[dict[str, Any]], queue_lock: threading.Lock
    ) -> None:
        while not self.stop.is_set():
            if self.deadline and time.monotonic() > self.deadline:
                return
            with queue_lock:
                if not queue:
                    return
                mutant = queue.popleft()
            try:
                record = self.execute(mutant, worker)
            except Exception as exc:
                record = {
                    "id": mutant["id"],
                    "campaign_id": self.campaign_id,
                    "mutant_digest": digest_json(mutant),
                    "run_id": self.run_id,
                    "status": "INFRA_ERROR",
                    "error": repr(exc)[:300],
                }
            with self.print_lock:
                self.counter[record["status"]] += 1
                print(
                    f"[w{worker}] {record['status']} {mutant['file']}:{mutant['line']}", flush=True
                )
                with self.results_path.open("a") as handle:
                    handle.write(json.dumps(record) + "\n")

    def run(self, mutants: list[dict[str, Any]]) -> None:
        queue = collections.deque(mutants)
        lock = threading.Lock()
        threads = [
            threading.Thread(target=self.worker_loop, args=(i, queue, lock))
            for i in range(self.workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        print(f"completed attempt statuses: {dict(self.counter)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--tiers", default="1,2,3")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--files")
    parser.add_argument("--kinds")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((MUTDIR / "manifest.json").read_text())
    tiers = {int(tier) for tier in args.tiers.split(",")}
    todo = [
        m
        for m in manifest
        if m["tier"] in tiers
        and (not args.files or any(name in m["file"] for name in args.files.split(",")))
        and (not args.kinds or m["kind"] in args.kinds.split(","))
    ]
    if args.limit:
        todo = todo[: args.limit]
    if args.dry_run:
        print(
            json.dumps(
                {
                    "mutants": [m["id"] for m in todo],
                    "workers": args.workers,
                    "full": args.full,
                    "source_writes": False,
                },
                indent=2,
            )
        )
        return
    if args.full:
        os.environ["MUTATE_FULL_PHASE"] = "1"
    runner = Runner(args.workers, args.max_seconds)
    try:
        runner.run([m for m in todo if m["id"] not in runner.done_ids])
    finally:
        runner.close()


if __name__ == "__main__":
    main()
