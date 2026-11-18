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
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

import asyncpg
import bootstrap_policy
from mutate_identity import (
    EXECUTION_MODE_POLICY,
    digest_json,
    execution_modes,
    input_identity,
    read_artifact,
    sha_bytes,
    sha_file,
)
from mutate_identity import (
    database_admin_sha256 as database_admin_sha256,
)
from mutate_identity import (
    latest_compatible as latest_compatible,
)
from mutate_identity import (
    read_results as read_results,
)
from mutate_selection import GenericSelection, SelectionTimeoutPolicy

BACKEND = Path(__file__).resolve().parent.parent
MUTDIR = BACKEND / "mutation"
VENV_PY = BACKEND / ".venv" / "bin" / "python"

SAMPLE_CAP = 60  # phase-1 sampled tests
MODULE_LEVEL_FILE_SAMPLES = 3  # whole test files for module-level mutants
ISOLATION_POLICY = "durable-snapshots-guarded-baseline-cache-v1"
DATABASE_CLEANUP_POLICY = "bounded-owned-database-drop-v2"


def database_cleanup_configuration() -> dict[str, Any]:
    return {
        "policy": DATABASE_CLEANUP_POLICY,
        "connect_timeout_seconds": 5,
        "drop_timeout_seconds": 300,
        "close_timeout_seconds": 5,
    }


class DatabaseCleanupError(RuntimeError):
    """A bounded cleanup failed; preserve its primary and secondary diagnostics."""

    def __init__(self, diagnostics: dict[str, Any]):
        self.diagnostics = diagnostics
        super().__init__(
            "attempt database cleanup failed: " + json.dumps(diagnostics, sort_keys=True)
        )


class SnapshotDrift(RuntimeError):
    """A captured execution input changed; the run cannot credit more verdicts."""


def input_differences(expected: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    changed: list[dict[str, Any]] = []
    total = 0
    for group in sorted(expected.keys() | observed.keys()):
        before, after = expected.get(group, {}), observed.get(group, {})
        for path in sorted(before.keys() | after.keys()):
            if before.get(path) != after.get(path):
                total += 1
                if len(changed) < 20:
                    changed.append(
                        {
                            "group": group,
                            "path": path,
                            "expected": before.get(path),
                            "observed": after.get(path),
                        }
                    )
    return {"changed_input_count": total, "changed_inputs": changed}


def runtime_root() -> Path:
    """Keep long campaigns out of operating-system temporary cleanup paths."""
    root = (
        Path(os.environ.get("MUTATION_WORK_ROOT", MUTDIR / "campaigns/runtime"))
        .expanduser()
        .resolve()
    )
    repository = BACKEND.parent.resolve()
    if root.is_relative_to(repository) and not any(
        root.is_relative_to(allowed.resolve())
        for allowed in (MUTDIR / "campaigns", repository / "audit_reports", repository / "scratch")
    ):
        raise ValueError(
            "MUTATION_WORK_ROOT inside the repository must use an excluded "
            "campaign/audit/scratch directory"
        )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


class BaselineProofCache(MutableMapping[str, dict[str, Any] | None]):
    """Persist exact structured proofs; keep only their paths/digests in memory."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(mode=0o700)
        self._entries: dict[str, tuple[Path, str]] = {}

    def __getitem__(self, key: str) -> dict[str, Any] | None:
        path, expected = self._entries[key]
        try:
            data = path.read_bytes()
            if sha_bytes(data) != expected:
                raise SnapshotDrift(f"cached baseline proof changed: {path}")
            value = json.loads(data)
            if value is not None and not isinstance(value, dict):
                raise SnapshotDrift(f"cached baseline proof is not a structured object: {path}")
            return value
        except (OSError, ValueError) as exc:
            raise SnapshotDrift(f"cached baseline proof cannot be read: {path}") from exc

    def __setitem__(self, key: str, value: dict[str, Any] | None) -> None:
        path = self.directory / f"{sha_bytes(key.encode())}.json"
        temporary = path.with_suffix(".tmp")
        try:
            data = json.dumps(value).encode()
            temporary.write_bytes(data)
            temporary.replace(path)
        except (OSError, ValueError, TypeError) as exc:
            raise SnapshotDrift(f"cannot persist exact baseline proof: {path}") from exc
        self._entries[key] = (path, sha_bytes(data))

    def __delitem__(self, key: str) -> None:
        path, _ = self._entries.pop(key)
        path.unlink(missing_ok=True)

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        paths = [entry[0] for entry in self._entries.values()]
        self._entries.clear()  # Proofs become unreachable even if cleanup fails.
        for path in paths:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)


def compatible_statuses(
    path: Path, campaign_id: str, manifest: dict[str, dict[str, Any]], identity: dict[str, Any]
) -> dict[str, str]:
    """Fold resume metadata one receipt at a time, never retaining full histories."""
    if not path.exists():
        return {}
    fingerprints = {mid: digest_json(mutant) for mid, mutant in manifest.items()}
    latest: dict[str, str] = {}
    with path.open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            mid = row.get("id")
            if (
                isinstance(mid, str)
                and mid in fingerprints
                and row.get("campaign_id") == campaign_id
                and row.get("mutant_digest") == fingerprints.get(mid)
                and isinstance(row.get("status"), str)
            ):
                # A later error still supersedes an earlier terminal verdict.
                # Terminal claims additionally need the captured provenance;
                # an ID/digest/status-only row cannot establish a fresh kill.
                latest[mid] = row["status"] if row.get("provenance") == identity else "UNVERIFIED"
    return latest


def compatible_terminal_ids(
    path: Path, campaign_id: str, manifest: dict[str, dict[str, Any]], identity: dict[str, Any]
) -> set[str]:
    return {
        mid
        for mid, status in compatible_statuses(path, campaign_id, manifest, identity).items()
        if status in {"KILLED", "SURVIVED", "INVALID"}
    }


def drop_attempt_database(database: str) -> None:
    """Reap only this runner's unique database, including after SIGKILL.

    Killed pytest processes cannot run their session-fixture teardown. Never
    let an inconclusive attempt leak its database into the next campaign.
    """
    if re.fullmatch(r"herdly_mut_[a-f0-9]{10}_[a-f0-9]{8}_test", database) is None:
        raise ValueError("refusing to clean a database outside the attempt namespace")

    async def drop() -> None:
        configuration = database_cleanup_configuration()
        started = time.monotonic()
        stage_started = started
        stage = "connect"
        connection = None
        failure: Exception | None = None
        diagnostics: dict[str, Any] = {"database": database, **configuration}
        try:
            connection = await asyncpg.connect(
                os.environ.get("MUTATION_TEST_ADMIN_URL", "postgresql://localhost:5432/postgres"),
                timeout=configuration["connect_timeout_seconds"],
            )
            stage = "drop"
            stage_started = time.monotonic()
            # DROP waits for a new synchronous checkpoint. Parallel full-suite
            # DDL produced a 200-second checkpoint plus its following barrier;
            # allow that measured wait without making cleanup unbounded.
            await connection.execute(
                f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)',
                timeout=configuration["drop_timeout_seconds"],
            )
        except Exception as exc:
            failure = exc
            diagnostics.update(
                stage=stage,
                stage_elapsed_seconds=time.monotonic() - stage_started,
                stage_timeout_seconds=configuration[f"{stage}_timeout_seconds"],
                error_type=type(exc).__name__,
            )
        finally:
            if connection is not None:
                close_started = time.monotonic()
                try:
                    await connection.close(timeout=configuration["close_timeout_seconds"])
                except Exception as exc:
                    close_diagnostics = {
                        "stage": "close",
                        "stage_elapsed_seconds": time.monotonic() - close_started,
                        "stage_timeout_seconds": configuration["close_timeout_seconds"],
                        "error_type": type(exc).__name__,
                    }
                    if failure is None:
                        failure = exc
                        diagnostics.update(close_diagnostics)
                    else:
                        diagnostics["secondary_close_error"] = close_diagnostics
        if failure is not None:
            diagnostics["elapsed_seconds"] = time.monotonic() - started
            raise DatabaseCleanupError(diagnostics) from failure

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
    def __init__(
        self, workers: int, max_seconds: float | None, *, phase_timeout: float | None = None
    ):
        if workers < 1:
            raise ValueError("workers must be positive")
        self.workers = workers
        self.phase_timeout = phase_timeout
        self.deadline = time.monotonic() + max_seconds if max_seconds else None
        self.run_id = uuid.uuid4().hex
        self.inputs = input_identity(BACKEND)
        self._execution_modes = execution_modes(BACKEND, self.inputs)
        self._execution_modes_sha256 = digest_json(self._execution_modes)
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
        self.coverage_provenance = coverage_provenance
        self.timeout_policy = SelectionTimeoutPolicy(
            coverage_provenance, explicit_seconds=phase_timeout
        )
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
                "isolation_policy": ISOLATION_POLICY,
                "database_cleanup": database_cleanup_configuration(),
                "generic_selection": GenericSelection.configuration()
                if self.full_phase and type(self).select_tests is Runner.select_tests
                else None,
                "timeout_policy": self.timeout_policy.configuration(),
                "pytest_addopts": "cleared",
                "execution_mode_policy": EXECUTION_MODE_POLICY,
                "execution_modes_sha256": self._execution_modes_sha256,
            },
        }
        if (
            coverage_provenance.get("inputs") != self.inputs
            or coverage_provenance.get("coverage_sha256") != self.identity["coverage_sha256"]
            or coverage_provenance.get("baseline_exit_code") != 0
            or coverage_provenance.get("test_database_admin_sha256")
            != self.identity["config"]["test_database_admin_sha256"]
            or coverage_provenance.get("execution_mode_policy") != EXECUTION_MODE_POLICY
            or coverage_provenance.get("execution_modes_sha256") != self._execution_modes_sha256
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
        self.generic_selection = GenericSelection(self.ctx, coverage_provenance, backend=BACKEND)
        self.pop = file_popularity(self.ctx)
        self.tpf = tests_per_file(self.ctx)
        self.stop = threading.Event()
        self.counter: collections.Counter[str] = collections.Counter()
        self.print_lock = threading.Lock()
        self.results_path = MUTDIR / "results.jsonl"
        self.done_ids = compatible_terminal_ids(
            self.results_path, self.campaign_id, self.manifest, self.identity
        )
        self._snapshot_lock = threading.RLock()
        self._snapshot_error: str | None = None
        self._snapshot_tmp = tempfile.TemporaryDirectory(
            prefix=f"run-{self.run_id}-", dir=runtime_root()
        )
        run_directory = Path(self._snapshot_tmp.name)
        self.snapshot = run_directory / "snapshot/repo"
        self._attempt_root = run_directory / "attempts"
        self._attempt_root.mkdir()
        self._manifest_sha256 = self.identity["manifest_sha256"]
        try:
            shutil.copytree(BACKEND.parent, self.snapshot, ignore=snapshot_ignore)
            (self.snapshot / BACKEND.name / "mutation/manifest.json").write_bytes(manifest_bytes)
            # Reject a mixed snapshot taken while editors changed source/tests.
            if input_identity(self.snapshot / BACKEND.name) != self.inputs:
                raise RuntimeError(
                    "source/tests changed while snapshotting; retry a stable checkout"
                )
            if execution_modes(self.snapshot / BACKEND.name, self.inputs) != self._execution_modes:
                raise RuntimeError("source execution modes changed while snapshotting")
        except BaseException:
            self._snapshot_tmp.cleanup()
            raise
        self.local = threading.local()
        self.baselines: dict[str, tuple[str, str, float]] = {}
        self.baseline_receipts: MutableMapping[str, dict[str, Any] | None] = BaselineProofCache(
            run_directory / "baseline-receipts"
        )
        self.baseline_phase_identities: MutableMapping[str, dict[str, Any] | None] = (
            BaselineProofCache(run_directory / "baseline-identities")
        )
        self.baseline_lock = threading.Lock()
        self.baseline_in_flight: dict[str, threading.Event] = {}

    def close(self) -> None:
        self._snapshot_tmp.cleanup()

    def _invalidate_snapshot(self, reason: str, details: dict[str, Any] | None = None) -> None:
        if not hasattr(self, "_snapshot_lock"):
            self._snapshot_lock = threading.RLock()
        with self._snapshot_lock:
            if not getattr(self, "_snapshot_error", None):
                self._snapshot_error = reason
                self._snapshot_drift = {
                    "reason": reason,
                    "detected_epoch": time.time(),
                    **(details or {}),
                }
            if hasattr(self, "baseline_lock"):
                with self.baseline_lock:
                    self.baselines.clear()
                    self.baseline_receipts.clear()
                    getattr(self, "baseline_phase_identities", {}).clear()
            if hasattr(self, "stop"):
                self.stop.set()

    def _check_parent_snapshot(self) -> None:
        if not hasattr(self, "_snapshot_lock"):
            self._snapshot_lock = threading.RLock()
        with self._snapshot_lock:
            if getattr(self, "_snapshot_error", None):
                raise SnapshotDrift(self._snapshot_error)
            try:
                observed = input_identity(self.snapshot / BACKEND.name)
                intact = observed == self.inputs
                expected_modes = getattr(self, "_execution_modes", None)
                observed_modes = (
                    execution_modes(self.snapshot / BACKEND.name, self.inputs)
                    if expected_modes is not None
                    else None
                )
                intact = intact and observed_modes == expected_modes
                manifest_hash = None
                if hasattr(self, "_manifest_sha256"):
                    manifest_hash = sha_file(
                        self.snapshot / BACKEND.name / "mutation/manifest.json"
                    )
                    intact = intact and manifest_hash == self._manifest_sha256
            except OSError as exc:
                self._invalidate_snapshot(
                    f"parent snapshot cannot be read: {type(exc).__name__}: {exc}"
                )
                raise SnapshotDrift(self._snapshot_error) from exc
            if not intact:
                self._invalidate_snapshot(
                    "captured parent snapshot inputs or manifest changed; "
                    "discard baseline caches and start a fresh run",
                    {
                        "snapshot": str(self.snapshot),
                        "observed_manifest_sha256": manifest_hash,
                        **input_differences(self.inputs, observed),
                        "execution_mode_differences": input_differences(
                            expected_modes or {}, observed_modes or {}
                        ),
                    },
                )
                raise SnapshotDrift(self._snapshot_error)

    def _expected_workspace_inputs(self) -> dict[str, Any]:
        expected = copy.deepcopy(self.inputs)
        edit = getattr(self.local, "active_mutation", None)
        if edit:
            relative, fingerprint = edit
            group = "sources" if relative.startswith("app/") else "support"
            expected[group][relative] = fingerprint
        return expected

    def _check_clean_workspace(self) -> None:
        try:
            observed = input_identity(self.local.workspace)
            intact = observed == self.inputs
            expected_modes = getattr(self, "_execution_modes", None)
            observed_modes = (
                execution_modes(self.local.workspace, self.inputs)
                if expected_modes is not None
                else None
            )
            intact = intact and observed_modes == expected_modes
        except OSError as exc:
            self._invalidate_snapshot(
                f"clean private workspace cannot be read: {type(exc).__name__}: {exc}"
            )
            raise SnapshotDrift(self._snapshot_error) from exc
        if not intact:
            self._invalidate_snapshot(
                "private baseline workspace differs from captured clean inputs",
                {
                    "workspace": str(self.local.workspace),
                    **input_differences(self.inputs, observed),
                    "execution_mode_differences": input_differences(
                        expected_modes or {}, observed_modes or {}
                    ),
                },
            )
            raise SnapshotDrift(self._snapshot_error)

    def select_tests(self, m: dict[str, Any]) -> tuple[list[str], bool]:
        if self.full_phase:
            return self.generic_selection.select(m), False
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
        diagnostics: dict[str, Any] = {"category": None, "exit_code": None}
        self.local.phase_identity["diagnostics"] = diagnostics
        self.local.phase_diagnostics = diagnostics
        expected = (
            self._expected_workspace_inputs()
            if hasattr(self, "inputs")
            else self.local.phase_identity["inputs_before"]
        )
        expected_modes = getattr(self, "_execution_modes", None)
        modes_before = execution_modes(self.local.workspace, expected)
        self.local.phase_identity["execution_modes_before_sha256"] = digest_json(modes_before)
        if self.local.phase_identity["inputs_before"] != expected or (
            expected_modes is not None and modes_before != expected_modes
        ):
            diagnostics["category"] = "workspace_input_drift_before_process"
            self._invalidate_snapshot(
                "private execution workspace differs from its captured "
                "clean or single-mutant inputs",
                {
                    **input_differences(expected, self.local.phase_identity["inputs_before"]),
                    "execution_mode_differences": input_differences(
                        expected_modes or {}, modes_before
                    ),
                },
            )
            self.local.phase_identity["inputs_after"] = self.local.phase_identity["inputs_before"]
            return "infra", self._snapshot_error or "private input drift", 0.0
        timeout = (
            self.phase_timeout
            if self.phase_timeout is not None
            else self.timeout_policy.seconds(node_ids)
        )
        self.local.phase_identity["timeout_seconds"] = timeout
        env = os.environ.copy()
        # These flags configure this controller, not nested pytest harnesses.
        # Keep the bound parent policy while child tests start independently.
        env.pop("MUTATE_BOOTSTRAP_FIRST", None)
        env.pop("MUTATE_FULL_PHASE", None)
        env.pop("PYTEST_ADDOPTS", None)
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
            diagnostics.update(
                category="process_launch_error",
                error_type=type(exc).__name__,
                detail=str(exc)[:500],
            )
            self.local.phase_identity["inputs_after"] = input_identity(self.local.workspace)
            return "infra", repr(exc), time.monotonic() - t0
        try:
            out, _ = proc.communicate(timeout=timeout)
            try:
                receipt = json.loads(receipt_path.read_text())
            except OSError as exc:
                receipt = None
                diagnostics.update(category="missing_pytest_receipt", error_type=type(exc).__name__)
            except ValueError as exc:
                receipt = None
                diagnostics.update(
                    category="malformed_pytest_receipt", error_type=type(exc).__name__
                )
            self.local.pytest_receipt = receipt
            diagnostics["exit_code"] = proc.returncode
            try:
                if receipt is not None and (
                    not isinstance(receipt, dict)
                    or not isinstance(receipt.get("reports"), list)
                    or not isinstance(receipt.get("collection_errors"), list)
                    or type(receipt.get("collected")) is not int
                    or type(receipt.get("exit_code")) is not int
                ):
                    status = "infra"
                    diagnostics["category"] = "malformed_pytest_receipt"
                else:
                    status = classify_pytest(proc.returncode, receipt)
            except (KeyError, TypeError, AttributeError):
                status = "infra"
                diagnostics["category"] = "malformed_pytest_receipt"
            if status == "infra" and diagnostics["category"] is None:
                diagnostics["category"] = "pytest_infrastructure_outcome"
            if isinstance(receipt, dict):
                collection_errors = receipt.get("collection_errors")
                reports = receipt.get("reports")
                diagnostics.update(
                    collected=receipt.get("collected"),
                    collection_errors=collection_errors[:10]
                    if isinstance(collection_errors, list)
                    else [],
                    failed_phases=[
                        {key: report.get(key) for key in ("nodeid", "when", "outcome", "assertion")}
                        for report in reports
                        if isinstance(report, dict) and report.get("outcome") == "failed"
                    ][:10]
                    if isinstance(reports, list)
                    else [],
                )
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            try:
                out, _ = proc.communicate()
            except (OSError, UnicodeError) as exc:
                out = repr(exc)
                diagnostics["timeout_output_error_type"] = type(exc).__name__
                try:
                    proc.wait(timeout=45)
                except (OSError, subprocess.TimeoutExpired) as reap_error:
                    diagnostics["reaping_error_type"] = type(reap_error).__name__
            status = "timeout"
            self.local.pytest_receipt = None
            diagnostics.update(
                category="phase_timeout", timeout_seconds=timeout, exit_code=proc.returncode
            )
        except (OSError, UnicodeError) as exc:
            # A decoding/pipe failure must not leave an untracked pytest process
            # or bypass disposable-database cleanup below.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            diagnostics.update(
                category="process_communication_error",
                error_type=type(exc).__name__,
                detail=str(exc)[:500],
            )
            try:
                proc.wait(timeout=45)
            except (OSError, subprocess.TimeoutExpired) as reap_error:
                diagnostics["reaping_error_type"] = type(reap_error).__name__
            status = "infra"
            out = repr(exc)
            self.local.pytest_receipt = None
            diagnostics["exit_code"] = proc.returncode
        try:
            drop_attempt_database(env["GOATFARM_TEST_DB"])
        except Exception as exc:
            # A measurement with failed resource cleanup needs operator review,
            # even if the assertion receipt itself was otherwise conclusive.
            status = "infra"
            diagnostics.update(
                category="database_cleanup_error", cleanup_error_type=type(exc).__name__
            )
            if isinstance(exc, DatabaseCleanupError):
                diagnostics["database_cleanup"] = exc.diagnostics
            self.local.phase_identity["cleanup_error"] = type(exc).__name__
            out = (out or "") + (
                f"\nAttempt database cleanup failed for {env['GOATFARM_TEST_DB']}: "
                f"{type(exc).__name__}: {exc}"
            )
        self.local.phase_identity["inputs_after"] = input_identity(self.local.workspace)
        modes_after = execution_modes(self.local.workspace, expected)
        self.local.phase_identity["execution_modes_after_sha256"] = digest_json(modes_after)
        if self.local.phase_identity["inputs_after"] != expected or (
            expected_modes is not None and modes_after != expected_modes
        ):
            diagnostics["category"] = "workspace_input_drift_after_process"
            self._invalidate_snapshot(
                "private execution inputs changed during pytest",
                {
                    **input_differences(expected, self.local.phase_identity["inputs_after"]),
                    "execution_mode_differences": input_differences(
                        expected_modes or {}, modes_after
                    ),
                },
            )
            status = "infra"
        if status in {"infra", "timeout"}:
            diagnostics["output_tail"] = (out or "")[-4000:]
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
        self.local.active_mutation = None
        self.local.failed_baseline_evidence = None
        while True:
            self._check_parent_snapshot()
            self._check_clean_workspace()
            with self.baseline_lock:
                cached = self.baselines.get(key)
                if cached is not None:
                    break
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
                # A failed control is never cached. Clear prior phase state so
                # even a phase that cannot produce evidence cannot inherit a
                # previous mutant's receipt from this worker thread.
                self.local.pytest_receipt = None
                self.local.phase_identity = None
                self.local.phase_diagnostics = None
                result = (
                    self.run_pytest(selection, worker)
                    if confcutdir is None
                    else self.run_pytest(selection, worker, confcutdir=confcutdir)
                )
                if result[0] == "pass":
                    self._check_parent_snapshot()
                    self._check_clean_workspace()
                    with self.baseline_lock:
                        if getattr(self, "_snapshot_error", None):
                            raise SnapshotDrift(self._snapshot_error)
                        self.baseline_receipts[key] = copy.deepcopy(
                            getattr(self.local, "pytest_receipt", None)
                        )
                        if not hasattr(self, "baseline_phase_identities"):
                            self.baseline_phase_identities = {}
                        phase_identity = getattr(self.local, "phase_identity", None)
                        self.baseline_phase_identities[key] = (
                            copy.deepcopy(phase_identity)
                            if getattr(self, "bootstrap_first", False)
                            else {
                                key: (phase_identity or {}).get(key)
                                for key in (
                                    "timeout_seconds",
                                    "execution_modes_before_sha256",
                                    "execution_modes_after_sha256",
                                )
                            }
                        )
                        # Publish a reusable pass only after both exact proofs
                        # have been persisted; cache writes can fail too.
                        self.baselines[key] = (result[0], result[1][-4000:], result[2])
                else:
                    self.local.failed_baseline_evidence = {
                        "key": key,
                        "status": result[0],
                        "receipt": copy.deepcopy(getattr(self.local, "pytest_receipt", None)),
                        "identity": copy.deepcopy(getattr(self.local, "phase_identity", None)),
                    }
                return result
            finally:
                with self.baseline_lock:
                    self.baseline_in_flight.pop(key, None)
                    pending.set()
        self._check_parent_snapshot()
        self._check_clean_workspace()
        return cached

    def _baseline_evidence(
        self, key: str, status: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if status == "pass":
            return self.baseline_receipts.get(key), self.baseline_phase_identities.get(key)
        evidence = getattr(self.local, "failed_baseline_evidence", None)
        if evidence is None or evidence["key"] != key or evidence["status"] != status:
            return None, None
        # Another worker may already have published a pass for this same key;
        # it cannot replace this failed owner's diagnostic evidence.
        return copy.deepcopy(evidence["receipt"]), copy.deepcopy(evidence["identity"])

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
            receipt, phase_identity = self._baseline_evidence(key, result[0])
            return capsule(
                result,
                nodes,
                boundary,
                receipt,
                phase_identity,
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
        self.local.active_mutation = (m["file"], sha_bytes(mutated.encode()))
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
        self.local.active_mutation = None
        self.local.phase_diagnostics = None
        try:
            self._check_parent_snapshot()
            record = self._execute(m, worker)
            self._check_parent_snapshot()
        except SnapshotDrift as exc:
            self._invalidate_snapshot(str(exc))
            record = self.error_record(m, exc, category="snapshot_input_drift")
            record["infrastructure"]["drift"] = copy.deepcopy(getattr(self, "_snapshot_drift", {}))
        diagnostics = getattr(self.local, "phase_diagnostics", None)
        if diagnostics and record.get("status") in {"INFRA_ERROR", "INCONCLUSIVE_TIMEOUT"}:
            if record.get("infrastructure"):
                record["infrastructure"]["phase"] = copy.deepcopy(diagnostics)
            else:
                record["infrastructure"] = copy.deepcopy(diagnostics)
        return record

    def error_record(self, m: dict[str, Any], error: Exception, *, category: str) -> dict[str, Any]:
        return {
            **{key: m.get(key) for key in ("id", "file", "line", "kind", "detail", "tier")},
            "campaign_id": self.campaign_id,
            "mutant_digest": digest_json(m),
            "provenance": self.identity,
            "run_id": self.run_id,
            "attempt_id": uuid.uuid4().hex,
            "status_policy": "assertions-only-timeouts-inconclusive",
            "status": "INFRA_ERROR",
            "error": str(error)[:1000],
            "infrastructure": {"category": category, "error_type": type(error).__name__},
        }

    def _execute(self, m: dict[str, Any], worker: int) -> dict[str, Any]:
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
        with tempfile.TemporaryDirectory(
            prefix="mutant-", dir=getattr(self, "_attempt_root", None) or runtime_root()
        ) as temporary:
            workspace = Path(temporary) / "repo"
            try:
                shutil.copytree(self.snapshot, workspace)
            except OSError as exc:
                self._invalidate_snapshot(
                    "private snapshot copy failed; execution inputs cannot be established",
                    {"copy_error_type": type(exc).__name__, "errno": exc.errno},
                )
                raise SnapshotDrift(self._snapshot_error) from exc
            self.local.workspace = workspace / BACKEND.name
            self._check_clean_workspace()
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
                baseline_receipt, baseline_identity = self._baseline_evidence(
                    baseline_key, baseline[0]
                )
                baseline_identity = baseline_identity or {}
                rec["baseline"] = {
                    "status": baseline[0],
                    "duration": baseline[2],
                    "selection_sha256": digest_json(subset),
                    "pytest_receipt": baseline_receipt,
                    **{
                        key: baseline_identity.get(key)
                        for key in (
                            "timeout_seconds",
                            "execution_modes_before_sha256",
                            "execution_modes_after_sha256",
                        )
                    },
                }
                if baseline[0] != "pass":
                    rec["baseline"]["phase_identity"] = baseline_identity
                    return {
                        **rec,
                        "status": "INCONCLUSIVE_TIMEOUT"
                        if baseline[0] == "timeout"
                        else "INFRA_ERROR",
                        "error": "clean baseline did not pass",
                        "fail": self.first_failure(baseline[1]),
                    }
                path.write_text(mutated)
                self.local.active_mutation = (m["file"], sha_bytes(mutated.encode()))
                status, out, duration = self.run_pytest(subset, worker)
                total_duration += duration
                rec.update(
                    phase=phase,
                    dur=round(total_duration, 2),
                    pytest_receipt=getattr(self.local, "pytest_receipt", None),
                    **{
                        key: (getattr(self.local, "phase_identity", None) or {}).get(key)
                        for key in (
                            "timeout_seconds",
                            "execution_modes_before_sha256",
                            "execution_modes_after_sha256",
                        )
                    },
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
                record = self.error_record(mutant, exc, category="runner_exception")
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
        if getattr(self, "_snapshot_error", None):
            raise SnapshotDrift(self._snapshot_error)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--timeout",
        type=float,
        help="equal fixed seconds for each clean/mutated phase; "
        "default uses captured concrete selection size",
    )
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
    runner = Runner(args.workers, args.max_seconds, phase_timeout=args.timeout)
    try:
        todo = select_targets(list(runner.manifest.values()))
        runner.run([m for m in todo if m["id"] not in runner.done_ids])
    finally:
        runner.close()


if __name__ == "__main__":
    main()
