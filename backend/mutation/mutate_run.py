"""Isolated mutation runner with clean-selection baselines and attempt receipts.

Every worker mutates a private immutable checkout snapshot and uses a unique
throwaway database. Only structured assertion failures against a passing exact
selection baseline count as kills. Timeouts and infrastructure failures remain
inconclusive; incompatible historical receipts cannot be resumed or scored.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import copy
import json
import os
import re
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

import asyncpg
import bootstrap_policy
from mutate_identity import (
    database_admin_sha256 as database_admin_sha256,
)
from mutate_identity import (
    digest_json,
    input_identity,
    latest_compatible,
    read_artifact,
    read_results,
    sha_bytes,
    sha_file,
)

BACKEND = Path(__file__).resolve().parent.parent
MUTDIR = BACKEND / "mutation"
VENV_PY = BACKEND / ".venv" / "bin" / "python"

SAMPLE_CAP = 60  # phase-1 sampled tests
MODULE_LEVEL_FILE_SAMPLES = 3  # whole test files for module-level mutants


def drop_attempt_database(database: str) -> None:
    """Reap only this runner's unique database, including after SIGKILL.

    Killed pytest processes cannot run their session-fixture teardown. Never
    let an inconclusive attempt leak its database into the next campaign.
    """
    if re.fullmatch(r"herdly_mut_[a-f0-9]{10}_[a-f0-9]{8}_test", database) is None:
        raise ValueError("refusing to clean a database outside the attempt namespace")

    async def drop() -> None:
        connection = await asyncpg.connect(
            os.environ.get("MUTATION_TEST_ADMIN_URL", "postgresql://localhost:5432/postgres"),
            timeout=5,
        )
        try:
            # PostgreSQL can wait for an in-progress checkpoint even with every
            # database lock granted. Allow that bounded I/O wait under parallel
            # test load; cleanup failure still makes the attempt inconclusive.
            await connection.execute(
                f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)', timeout=45
            )
        finally:
            await connection.close(timeout=5)

    asyncio.run(drop())


def load_coverage_contexts(
    coverage_bytes: bytes, provenance: dict[str, Any]
) -> dict[str, dict[int, set[str]]]:
    """Parse exactly the captured bytes whose digest identifies this campaign."""
    import coverage

    source_root = Path(provenance.get("source_root", str(BACKEND))).resolve()
    by_file: dict[str, dict[int, set[str]]] = {}
    with tempfile.TemporaryDirectory(prefix="herdly-mutation-coverage-") as temporary:
        data_file = Path(temporary) / ".coverage"
        data_file.write_bytes(coverage_bytes)
        cov = coverage.Coverage(data_file=str(data_file))
        cov.load()
        data = cov.get_data()
        for f in data.measured_files():
            rel = Path(f).resolve().relative_to(source_root).as_posix()
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
        "scratch",
    }
    campaign_outputs = {
        "manifest.json",
        "results.jsonl",
        "verify_extremes.jsonl",
        "report.md",
        "measurement-current.json",
        "domain-plan.json",
        "domain-report.json",
        "campaigns",
    }
    return {
        name
        for name in names
        if name in excluded
        or name.startswith(".coverage")
        or (name.startswith(".env") and not name.endswith(".example"))
        or name.endswith((".pyc", ".dump", ".dump.gpg"))
        or (
            Path(directory).name == "mutation"
            and (name in campaign_outputs or name.endswith(".log") or name.startswith("ci-"))
        )
    }


def allowed_mutation_target(path: Path, workspace: Path) -> bool:
    """Only application code and private migration revisions are executable targets."""
    resolved = path.resolve()
    return path.suffix == ".py" and any(
        resolved.is_relative_to(root.resolve())
        for root in (workspace / "app", workspace / "alembic/versions")
    )


class Runner:
    def __init__(self, workers: int, max_seconds: float | None, *, phase_timeout: float = 300):
        if workers < 1:
            raise ValueError("workers must be positive")
        self.workers = workers
        self.phase_timeout = phase_timeout
        self.deadline = time.monotonic() + max_seconds if max_seconds else None
        self.run_id = uuid.uuid4().hex
        self.inputs = input_identity(BACKEND)
        # Read each artifact once. Rechecking live hashes after parsing does
        # not catch an artifact replaced and restored during context loading.
        manifest_bytes = read_artifact(
            MUTDIR / "manifest.json",
            instruction="run python mutation/mutate_gen.py first",
        )
        try:
            coverage_bytes = (BACKEND / ".coverage-mut").read_bytes()
            provenance_bytes = (BACKEND / ".coverage-mut.provenance.json").read_bytes()
            coverage_provenance = json.loads(provenance_bytes)
        except (OSError, ValueError) as exc:
            raise ValueError(
                "coverage has no clean-baseline provenance; run mutation/mutate_cover.py"
            ) from exc
        manifest = json.loads(manifest_bytes)
        self.manifest = {m["id"]: m for m in manifest}
        if len(self.manifest) != len(manifest):
            raise ValueError("manifest has duplicate mutant IDs; regenerate the manifest")
        self._mutant_digests = {mid: digest_json(m) for mid, m in self.manifest.items()}
        self.full_phase = bool(os.environ.get("MUTATE_FULL_PHASE"))
        self.identity = {
            "schema": 2,
            **self.inputs,
            "manifest_sha256": sha_bytes(manifest_bytes),
            "coverage_sha256": sha_bytes(coverage_bytes),
            "coverage_provenance_sha256": sha_bytes(provenance_bytes),
            "config": {
                "test_database_admin_sha256": database_admin_sha256(),
                "workers": workers,
                "phase_timeout": phase_timeout,
                "full": self.full_phase,
                "sample_cap": SAMPLE_CAP,
            },
        }
        if (
            coverage_provenance.get("inputs") != self.inputs
            or coverage_provenance.get("coverage_sha256") != self.identity["coverage_sha256"]
            or coverage_provenance.get("baseline_exit_code") != 0
            or coverage_provenance.get("test_database_admin_sha256")
            != self.identity["config"]["test_database_admin_sha256"]
        ):
            raise ValueError(
                "coverage source/tests/locks or test database are stale; "
                "run mutation/mutate_cover.py"
            )
        self.bootstrap_first = os.environ.get("MUTATE_BOOTSTRAP_FIRST") == "1"
        if self.bootstrap_first:
            if not self.full_phase:
                raise ValueError("bootstrap-first requires complete native selection")
            self.identity["config"]["bootstrap"] = bootstrap_policy.configuration(self.inputs)
        self.campaign_id = digest_json(self.identity)
        self.ctx = load_coverage_contexts(coverage_bytes, coverage_provenance)
        self.pop = file_popularity(self.ctx)
        self.tpf = tests_per_file(self.ctx)
        self.stop = threading.Event()
        self.counter: collections.Counter[str] = collections.Counter()
        self.print_lock = threading.Lock()
        self.results_path = MUTDIR / "results.jsonl"
        self.done_ids = {
            mid
            for mid, rec in latest_compatible(
                read_results(self.results_path), self.campaign_id, self.manifest
            ).items()
            if rec.get("status") in {"KILLED", "SURVIVED", "INVALID"}
        }
        self._snapshot_tmp = tempfile.TemporaryDirectory(prefix="herdly-mutation-snapshot-")
        self.snapshot = Path(self._snapshot_tmp.name) / "repo"
        try:
            shutil.copytree(BACKEND.parent, self.snapshot, ignore=snapshot_ignore)
            (self.snapshot / BACKEND.name / "mutation/manifest.json").write_bytes(manifest_bytes)
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
        self.baseline_receipts: dict[str, dict[str, Any] | None] = {}
        self.baseline_phase_identities: dict[str, dict[str, Any] | None] = {}
        self.baseline_lock = threading.Lock()
        self.baseline_in_flight: dict[str, threading.Event] = {}

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

    def run_pytest(
        self, node_ids: list[str], worker: int, *, confcutdir: str | None = None
    ) -> tuple[str, str, float]:
        self.local.pytest_receipt = None
        self.local.phase_identity = {
            "inputs_before": input_identity(self.local.workspace),
            "inputs_after": None,
            "confcutdir": confcutdir,
            "cleanup_error": None,
        }
        env = os.environ.copy()
        # These flags configure this controller, not nested pytest harnesses.
        # Keep the bound parent policy while child tests start independently.
        env.pop("MUTATE_BOOTSTRAP_FIRST", None)
        env.pop("MUTATE_FULL_PHASE", None)
        env["GOATFARM_TEST_DB"] = f"herdly_mut_{self.run_id[:10]}_{uuid.uuid4().hex[:8]}_test"
        env.pop("COVERAGE_FILE", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(self.local.workspace)
        env["PATH"] = str(VENV_PY.parent) + os.pathsep + env.get("PATH", "")
        receipt_path = self.local.workspace / f"receipt-{uuid.uuid4().hex}.json"
        env["MUTATION_RECEIPT_PATH"] = str(receipt_path)
        selection_path = receipt_path.with_suffix(".selection.json")
        selection_path.write_text(json.dumps(node_ids))
        env["MUTATION_SELECTION_PATH"] = str(selection_path)
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
            *([f"--confcutdir={confcutdir}"] if confcutdir is not None else []),
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
            try:
                status = classify_pytest(proc.returncode, receipt)
            except (KeyError, TypeError, AttributeError):
                status = "infra"
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            status = "timeout"
            self.local.pytest_receipt = None
        try:
            drop_attempt_database(env["GOATFARM_TEST_DB"])
        except Exception as exc:
            # A measurement with failed resource cleanup needs operator review,
            # even if the assertion receipt itself was otherwise conclusive.
            status = "infra"
            self.local.phase_identity["cleanup_error"] = type(exc).__name__
            out = (out or "") + (
                f"\nAttempt database cleanup failed for {env['GOATFARM_TEST_DB']}: "
                f"{type(exc).__name__}"
            )
        self.local.phase_identity["inputs_after"] = input_identity(self.local.workspace)
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
        cleanup_failure = next(
            (
                line.strip()[:220]
                for line in out.splitlines()
                if line.strip().startswith("Attempt database cleanup failed")
            ),
            None,
        )
        if cleanup_failure:
            return cleanup_failure
        return next(
            (
                line.strip()[:220]
                for line in out.splitlines()
                if line.strip().startswith(("FAILED", "ERROR"))
            ),
            out[-220:],
        )

    def clean_baseline(
        self,
        key: str,
        selection: list[str],
        worker: int,
        *,
        confcutdir: str | None = None,
    ) -> tuple[str, str, float]:
        """Coalesce identical selections while independent baselines run concurrently."""
        while True:
            with self.baseline_lock:
                cached = self.baselines.get(key)
                if cached is not None:
                    return cached
                pending = self.baseline_in_flight.get(key)
                if pending is None:
                    pending = threading.Event()
                    self.baseline_in_flight[key] = pending
                    owner = True
                else:
                    owner = False
            if not owner:
                pending.wait()
                continue
            try:
                result = (
                    self.run_pytest(selection, worker)
                    if confcutdir is None
                    else self.run_pytest(selection, worker, confcutdir=confcutdir)
                )
                if result[0] == "pass":
                    with self.baseline_lock:
                        self.baselines[key] = result
                        self.baseline_receipts[key] = copy.deepcopy(
                            getattr(self.local, "pytest_receipt", None)
                        )
                        if getattr(self, "bootstrap_first", False):
                            self.baseline_phase_identities[key] = copy.deepcopy(
                                getattr(self.local, "phase_identity", None)
                            )
                return result
            finally:
                with self.baseline_lock:
                    self.baseline_in_flight.pop(key, None)
                    pending.set()

    def execute_bootstrap_first(
        self,
        rec: dict[str, Any],
        m: dict[str, Any],
        selection: list[str],
        worker: int,
        path: Path,
        original: bytes,
        mutated: str,
    ) -> dict[str, Any]:
        """A real isolated call may stop the mutant phase; native clean controls always run."""
        from verify_domain_receipts import Context, EvidenceError

        config = self.identity["config"]["bootstrap"]
        rec.update(
            status_policy=bootstrap_policy.POLICY,
            selection=selection,
            selection_sha256=digest_json(selection),
            selection_mode="complete",
            bootstrap={"configuration": copy.deepcopy(config)},
            native_mutant_attempted=False,
            pytest_receipt=None,
        )

        def capsule(
            result: tuple[str, str, float],
            nodes: list[str],
            boundary: str | None,
            receipt: dict[str, Any] | None,
            phase_identity: dict[str, Any] | None,
        ) -> dict[str, Any]:
            return {
                "status": result[0],
                "duration": result[2],
                "selection": nodes,
                "selection_sha256": digest_json(nodes),
                "confcutdir": boundary,
                "pytest_receipt": receipt,
                "exit_code": receipt.get("exit_code") if isinstance(receipt, dict) else None,
                **(
                    phase_identity
                    or {
                        "inputs_before": None,
                        "inputs_after": None,
                        "cleanup_error": None,
                    }
                ),
            }

        def clean(label: str, nodes: list[str], boundary: str | None) -> tuple[dict[str, Any], str]:
            key = digest_json([self.campaign_id, label, boundary, nodes])
            path.write_bytes(original)
            result = self.clean_baseline(key, nodes, worker, confcutdir=boundary)
            return capsule(
                result,
                nodes,
                boundary,
                self.baseline_receipts.get(key),
                self.baseline_phase_identities.get(key),
            ), result[1]

        boot_clean, output = clean(
            "bootstrap", bootstrap_policy.SELECTION, bootstrap_policy.CONFCUTDIR
        )
        rec["bootstrap"]["clean"] = boot_clean
        if boot_clean["status"] != "pass":
            return {
                **rec,
                "status": "INCONCLUSIVE_TIMEOUT"
                if boot_clean["status"] == "timeout"
                else "INFRA_ERROR",
                "error": "clean bootstrap control did not pass",
                "fail": self.first_failure(output),
            }
        native_clean, output = clean("native", selection, None)
        rec["baseline"] = native_clean
        if native_clean["status"] != "pass":
            return {
                **rec,
                "status": "INCONCLUSIVE_TIMEOUT"
                if native_clean["status"] == "timeout"
                else "INFRA_ERROR",
                "error": "complete clean native control did not pass",
                "fail": self.first_failure(output),
            }
        # Passing labels alone are insufficient: reject malformed or stale clean
        # controls before changing source bytes or running any mutant process.
        try:
            clean_boot_nodes, _ = bootstrap_policy.verify_phase(
                boot_clean,
                bootstrap_policy.SELECTION,
                bootstrap_policy.CONFCUTDIR,
                self.inputs,
                True,
            )
            if clean_boot_nodes != set(bootstrap_policy.SELECTION):
                raise EvidenceError("bootstrap control must contain exactly one call")
            bootstrap_policy.verify_phase(native_clean, selection, None, self.inputs, True)
        except (EvidenceError, ValueError, TypeError, KeyError) as exc:
            return {**rec, "status": "INFRA_ERROR", "error": f"invalid clean controls: {exc}"}
        path.write_text(mutated)
        rec["edited_source_sha256"] = sha_file(path)
        result = self.run_pytest(
            bootstrap_policy.SELECTION, worker, confcutdir=bootstrap_policy.CONFCUTDIR
        )
        boot_mutant = capsule(
            result,
            bootstrap_policy.SELECTION,
            bootstrap_policy.CONFCUTDIR,
            getattr(self.local, "pytest_receipt", None),
            getattr(self.local, "phase_identity", None),
        )
        rec["bootstrap"]["mutant"] = boot_mutant
        if result[0] == "kill":
            rec.update(
                status="KILLED",
                decisive_phase="bootstrap",
                native_mutant={
                    "status": "not-attempted",
                    "reason": "bootstrap-assertion-kill",
                },
            )
        elif result[0] != "pass":
            return {
                **rec,
                "status": "INCONCLUSIVE_TIMEOUT" if result[0] == "timeout" else "INFRA_ERROR",
                "error": "bootstrap mutant phase did not pass or assertion-fail",
                "fail": self.first_failure(result[1]),
            }
        else:
            result = self.run_pytest(selection, worker)
            native_mutant = capsule(
                result,
                selection,
                None,
                getattr(self.local, "pytest_receipt", None),
                getattr(self.local, "phase_identity", None),
            )
            rec.update(
                native_mutant=native_mutant,
                native_mutant_attempted=True,
                decisive_phase="native",
                pytest_receipt=native_mutant["pytest_receipt"],
                status={
                    "pass": "SURVIVED",
                    "kill": "KILLED",
                    "infra": "INFRA_ERROR",
                    "timeout": "INCONCLUSIVE_TIMEOUT",
                }[result[0]],
            )
        rec["dur"] = round(
            boot_mutant["duration"] + (rec.get("native_mutant", {}).get("duration") or 0),
            2,
        )
        if rec["status"] in {"KILLED", "SURVIVED"}:
            context = Context(
                self.campaign_id,
                self.manifest,
                self.inputs,
                {
                    key: self.identity[key]
                    for key in (
                        "manifest_sha256",
                        "coverage_sha256",
                        "coverage_provenance_sha256",
                    )
                },
                self.identity["config"]["test_database_admin_sha256"],
                lambda mutant: self.select_tests(mutant)[0],
                source_bytes=lambda relative: (
                    original
                    if relative == m["file"]
                    else (self.snapshot / BACKEND.name / relative).read_bytes()
                ),
            )
            try:
                bootstrap_policy.verify(rec, context)
            except (EvidenceError, ValueError, TypeError, KeyError) as exc:
                rec.update(status="INFRA_ERROR", error=f"invalid phase evidence: {exc}")
        return rec

    def execute(self, m: dict[str, Any], worker: int) -> dict[str, Any]:
        m = copy.deepcopy(m)
        rec = {key: m[key] for key in ("id", "file", "line", "kind", "detail", "tier")}
        rec.update(
            campaign_id=self.campaign_id,
            run_id=self.run_id,
            attempt_id=uuid.uuid4().hex,
            mutant_digest=digest_json(m),
            provenance=self.identity,
            status_policy="assertions-only-timeouts-inconclusive",
        )
        if self._mutant_digests.get(m["id"]) != rec["mutant_digest"]:
            return {
                **rec,
                "status": "INVALID",
                "error": "mutation does not match the captured manifest",
            }
        with tempfile.TemporaryDirectory(prefix="herdly-mutant-") as temporary:
            workspace = Path(temporary) / "repo"
            shutil.copytree(self.snapshot, workspace)
            self.local.workspace = workspace / BACKEND.name
            path = self.local.workspace / m["file"]
            if not allowed_mutation_target(path, self.local.workspace):
                return {
                    **rec,
                    "status": "INVALID",
                    "error": "mutation target must stay inside app/ or private alembic/versions/",
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
            if getattr(self, "bootstrap_first", False):
                try:
                    return self.execute_bootstrap_first(
                        rec, m, selection, worker, path, original, mutated
                    )
                finally:
                    path.write_bytes(original)
            phases = [len(selection)] if self.full_phase else [15, SAMPLE_CAP]
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
                baseline = self.clean_baseline(baseline_key, subset, worker)
                rec["baseline"] = {
                    "status": baseline[0],
                    "duration": baseline[2],
                    "selection_sha256": digest_json(subset),
                    "pytest_receipt": self.baseline_receipts.get(baseline_key),
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
        self,
        worker: int,
        queue: collections.deque[dict[str, Any]],
        queue_lock: threading.Lock,
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
                    f"[w{worker}] {record['status']} {mutant['file']}:{mutant['line']}",
                    flush=True,
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
    tiers = {int(tier) for tier in args.tiers.split(",")}

    def select_targets(manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
        todo = [
            m
            for m in manifest
            if m["tier"] in tiers
            and (not args.files or any(name in m["file"] for name in args.files.split(",")))
            and (not args.kinds or m["kind"] in args.kinds.split(","))
        ]
        return todo[: args.limit] if args.limit else todo

    if args.dry_run:
        todo = select_targets(
            json.loads(
                read_artifact(
                    MUTDIR / "manifest.json",
                    instruction="run python mutation/mutate_gen.py first",
                )
            )
        )
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
        todo = select_targets(list(runner.manifest.values()))
        runner.run([m for m in todo if m["id"] not in runner.done_ids])
    finally:
        runner.close()


if __name__ == "__main__":
    main()
