"""Real tiny-project runner isolation and bounded-history regression checks."""

import json
import stat
import sys
import threading
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from tests.test_mutation_harness import _project

MUTATION = Path(__file__).resolve().parents[1] / "mutation"
sys.path.insert(0, str(MUTATION))
import mutate_run  # type: ignore[import-not-found]  # noqa: E402
import verify_generic_receipts as generic_consumer  # type: ignore[import-not-found]  # noqa: E402
from mutate_identity import digest_json  # type: ignore[import-not-found]  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """These fixtures use no application database lifecycle."""


@pytest.fixture(autouse=True)
def _clean_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mutate_run, "drop_attempt_database", lambda _name: None)


def test_execution_mode_fingerprint_is_source_bound_without_changing_byte_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _ = _project(tmp_path, monkeypatch)
    script = backend / "scripts/tool.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\ntrue\n")
    script.chmod(0o755)
    inputs = mutate_run.input_identity(backend)
    before = mutate_run.execution_modes(backend, inputs)
    assert before["support"]["scripts/tool.sh"] == 0o755
    assert before["locks"]["uv.lock"] is None
    script.chmod(0o644)
    assert mutate_run.input_identity(backend) == inputs
    assert digest_json(mutate_run.execution_modes(backend, inputs)) != digest_json(before)
    with pytest.raises(ValueError, match="escapes"):
        mutate_run.execution_modes(backend, {"sources": {"../outside.py": "unbound"}})


def test_parent_and_attempts_ignore_operating_system_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inherited_campaign_root = tmp_path / "outer-campaign/runtime"
    monkeypatch.setenv("MUTATION_WORK_ROOT", str(inherited_campaign_root))
    backend, mutants = _project(tmp_path, monkeypatch)
    operating_system_tmp = tmp_path / "os-temp"
    operating_system_tmp.mkdir()
    monkeypatch.setattr(mutate_run.tempfile, "tempdir", str(operating_system_tmp))
    runner = mutate_run.Runner(1, None)
    spawned: list[Path] = []
    original = mutate_run.subprocess.Popen

    def spawn(*args: Any, **kwargs: Any) -> Any:
        spawned.append(Path(kwargs["cwd"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(mutate_run.subprocess, "Popen", spawn)
    try:
        assert runner.snapshot.is_relative_to(backend / "mutation/campaigns/runtime")
        assert not inherited_campaign_root.exists()
        result = runner.execute(mutants[0], 0)
        assert result["status"] == "KILLED"
        assert len(spawned) == 2
        assert all(path.is_relative_to(runner._attempt_root) for path in spawned)
        assert not any(operating_system_tmp.iterdir())
    finally:
        run_directory = Path(runner._snapshot_tmp.name)
        runner.close()
    assert not run_directory.exists()


def _controlled_baseline_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, list[dict[str, Any]]]:
    backend, mutants = _project(tmp_path, monkeypatch)
    test = backend / "tests/test_alpha.py"
    test.write_text(
        "import os\n"
        + test.read_text().replace(
            "def test_value():\n",
            "def test_value():\n    assert os.environ.get('RELIABILITY_CONTROL_FAIL') != '1'\n",
        )
    )
    provenance_path = backend / ".coverage-mut.provenance.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["inputs"] = mutate_run.input_identity(backend)
    provenance["execution_modes_sha256"] = digest_json(
        mutate_run.execution_modes(backend, provenance["inputs"])
    )
    provenance_path.write_text(json.dumps(provenance))
    return backend, mutants


def test_failed_clean_baseline_retains_its_receipt_without_running_or_scoring_mutant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, mutants = _controlled_baseline_project(tmp_path, monkeypatch)
    monkeypatch.setenv("MUTATE_FULL_PHASE", "1")
    monkeypatch.setenv("RELIABILITY_CONTROL_FAIL", "1")
    monkeypatch.setattr(generic_consumer, "BACKEND", backend)
    runner = mutate_run.Runner(1, None)
    phases: list[str] = []
    run_pytest = runner.run_pytest

    def run(*args: Any, **kwargs: Any) -> tuple[str, str, float]:
        phases.append((runner.local.workspace / "app/alpha.py").read_text())
        return cast(tuple[str, str, float], run_pytest(*args, **kwargs))

    monkeypatch.setattr(runner, "run_pytest", run)
    try:
        result = runner.execute(mutants[0], 0)
        assert result["status"] == "INFRA_ERROR"
        baseline = result["baseline"]
        assert baseline["status"] == "kill"
        failed = [
            report
            for report in baseline["pytest_receipt"]["reports"]
            if report["outcome"] == "failed"
        ]
        assert len(failed) == 1
        assert failed[0]["nodeid"] == "tests/test_alpha.py::test_value"
        assert failed[0]["when"] == "call" and failed[0]["assertion"] is True
        phase = baseline["phase_identity"]
        assert phase["inputs_before"] == phase["inputs_after"] == runner.inputs
        assert phase["timeout_seconds"] == baseline["timeout_seconds"] == 300
        assert phase["execution_modes_before_sha256"] == baseline["execution_modes_before_sha256"]
        assert phase["execution_modes_after_sha256"] == baseline["execution_modes_after_sha256"]
        assert phases == ["def value():\n    return 1\n"]
        assert not runner.baselines and not runner.baseline_receipts
        assert not runner.baseline_phase_identities
        runner.local.pytest_receipt["reports"].clear()
        runner.local.phase_identity["inputs_before"].clear()
        assert baseline["pytest_receipt"]["reports"]
        assert phase["inputs_before"] == runner.inputs
        context = generic_consumer.capture_context(runner.campaign_id)
        history = tmp_path / "failed-baseline.jsonl"
        history.write_text(json.dumps(result) + "\n")
        report, proof = generic_consumer.verify([history], context)
        assert report["statuses"] == {"INFRA_ERROR": 1}
        assert proof["verified_complete_latest_verdicts"] == 0
        assert proof["verified_assertion_kills"] == 0
        history.write_text(json.dumps({**result, "status": "KILLED"}) + "\n")
        _report, proof = generic_consumer.verify([history], context)
        assert proof["verified_assertion_kills"] == 0 and proof["error_count"] > 0
    finally:
        runner.close()


def test_failed_baseline_evidence_survives_a_concurrent_pass_of_the_same_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _backend, mutants = _controlled_baseline_project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(2, None)
    failed_ready = threading.Event()
    passing_ready = threading.Event()
    run_pytest = runner.run_pytest
    clean_baseline = runner.clean_baseline
    popen = mutate_run.subprocess.Popen

    def run(selection: list[str], worker: int) -> tuple[str, str, float]:
        runner.local.control_worker = worker
        return cast(tuple[str, str, float], run_pytest(selection, worker))

    def spawn(*args: Any, **kwargs: Any) -> Any:
        kwargs["env"]["RELIABILITY_CONTROL_FAIL"] = "1" if runner.local.control_worker == 0 else "0"
        return popen(*args, **kwargs)

    def baseline(key: str, selection: list[str], worker: int) -> tuple[str, str, float]:
        result = clean_baseline(key, selection, worker)
        if worker == 0:
            assert result[0] == "kill"
            failed_ready.set()
            assert passing_ready.wait(10), "second worker did not publish its passing control"
        elif result[0] == "pass":
            passing_ready.set()
        return cast(tuple[str, str, float], result)

    monkeypatch.setattr(runner, "run_pytest", run)
    monkeypatch.setattr(runner, "clean_baseline", baseline)
    monkeypatch.setattr(mutate_run.subprocess, "Popen", spawn)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(runner.execute, mutants[0], 0)
            assert failed_ready.wait(10), "first worker did not finish its failed control"
            second = pool.submit(runner.execute, mutants[0], 1)
            failed, passed = first.result(timeout=15), second.result(timeout=15)
        assert failed["status"] == "INFRA_ERROR" and failed["baseline"]["status"] == "kill"
        assert passed["status"] == "KILLED" and passed["baseline"]["status"] == "pass"
        assert failed["selection_sha256"] == passed["selection_sha256"]
        assert failed["baseline"]["pytest_receipt"]["exit_code"] == 1
        assert passed["baseline"]["pytest_receipt"]["exit_code"] == 0
        assert len(runner.baselines) == len(runner.baseline_receipts) == 1
        assert len(runner.baseline_phase_identities) == 1
        assert failed["baseline"]["phase_identity"]["inputs_before"] == runner.inputs
    finally:
        runner.close()


def test_missing_failed_baseline_evidence_cannot_inherit_the_previous_mutant_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _backend, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    try:
        assert runner.execute(mutants[0], 0)["status"] == "KILLED"
        assert runner.local.pytest_receipt["exit_code"] == 1
        monkeypatch.setattr(runner, "run_pytest", lambda *args: ("infra", "no phase evidence", 0.0))
        result = runner.execute(mutants[1], 0)
        assert result["status"] == "INFRA_ERROR"
        assert result["baseline"]["pytest_receipt"] is None
        assert result["baseline"]["phase_identity"] == {}
        assert result["baseline"]["timeout_seconds"] is None
        assert len(runner.baselines) == len(runner.baseline_receipts) == 1
    finally:
        runner.close()


def test_ambient_pytest_filter_cannot_shrink_the_recorded_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _backend, mutants = _project(tmp_path, monkeypatch)
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k this_test_does_not_exist")
    runner = mutate_run.Runner(1, None)
    try:
        result = runner.execute(mutants[0], 0)
        assert result["status"] == "KILLED"
        assert result["baseline"]["pytest_receipt"]["collected"] == 1
        assert runner.identity["config"]["pytest_addopts"] == "cleared"
    finally:
        runner.close()


def test_deleted_parent_input_invalidates_cached_baseline_even_after_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _backend, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    try:
        assert runner.execute(mutants[0], 0)["status"] == "KILLED"
        assert runner.baselines
        path = runner.snapshot / "backend/app/__init__.py"
        original = path.read_bytes()
        path.unlink()
        monkeypatch.setattr(
            runner,
            "run_pytest",
            lambda *args, **kwargs: pytest.fail(
                "a poisoned baseline cache must never execute another process"
            ),
        )
        failed = runner.execute(mutants[1], 0)
        assert failed["status"] == "INFRA_ERROR"
        assert failed["infrastructure"]["category"] == "snapshot_input_drift"
        assert not runner.baselines and not runner.baseline_receipts
        assert runner.stop.is_set()
        path.write_bytes(original)
        assert runner.execute(mutants[1], 0)["status"] == "INFRA_ERROR"
    finally:
        runner.close()


def test_mode_only_parent_drift_permanently_invalidates_cached_baselines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _backend, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    try:
        assert runner.execute(mutants[0], 0)["status"] == "KILLED"
        path = runner.snapshot / "backend/app/alpha.py"
        original_mode = stat.S_IMODE(path.stat().st_mode)
        original_bytes = path.read_bytes()
        path.chmod(original_mode ^ stat.S_IXUSR)
        monkeypatch.setattr(
            runner,
            "run_pytest",
            lambda *args: pytest.fail("mode drift invalidated the old passing baseline"),
        )
        result = runner.execute(mutants[0], 0)
        assert result["status"] == "INFRA_ERROR" and path.read_bytes() == original_bytes
        drift = result["infrastructure"]["drift"]
        assert drift["changed_input_count"] == 0
        assert drift["execution_mode_differences"]["changed_input_count"] == 1
        assert drift["execution_mode_differences"]["changed_inputs"][0]["path"] == "app/alpha.py"
        assert not runner.baselines and runner.stop.is_set()
        path.chmod(original_mode)
        assert runner.execute(mutants[0], 0)["status"] == "INFRA_ERROR"
    finally:
        runner.close()


def test_mode_only_source_change_after_coverage_requires_new_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _ = _project(tmp_path, monkeypatch)
    path = backend / "app/alpha.py"
    path.chmod(stat.S_IMODE(path.stat().st_mode) ^ stat.S_IXUSR)
    with pytest.raises(ValueError, match="stale"):
        mutate_run.Runner(1, None)


@pytest.mark.parametrize("drift_kind", ["missing", "mode"])
def test_parent_drift_after_assertion_failure_cannot_credit_a_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift_kind: str
) -> None:
    _backend, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    original = runner.run_pytest

    def run(*args: Any, **kwargs: Any) -> tuple[str, str, float]:
        result = original(*args, **kwargs)
        if result[0] == "kill":
            path = runner.snapshot / "backend/app/__init__.py"
            if drift_kind == "missing":
                path.unlink()
            else:
                path.chmod(stat.S_IMODE(path.stat().st_mode) ^ stat.S_IXUSR)
        return cast(tuple[str, str, float], result)

    monkeypatch.setattr(runner, "run_pytest", run)
    try:
        result = runner.execute(mutants[0], 0)
        assert result["status"] == "INFRA_ERROR"
        assert result["infrastructure"]["category"] == "snapshot_input_drift"
        assert not runner.baselines
    finally:
        runner.close()


@pytest.mark.parametrize("drift_kind", ["missing", "mode"])
def test_private_copy_must_match_clean_inputs_before_any_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift_kind: str
) -> None:
    _backend, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    original = mutate_run.shutil.copytree

    def copy(*args: Any, **kwargs: Any) -> Path:
        result = original(*args, **kwargs)
        if Path(args[1]).name == "repo":
            path = Path(args[1]) / "backend/tests/test_alpha.py"
            if drift_kind == "missing":
                path.unlink()
            else:
                path.chmod(stat.S_IMODE(path.stat().st_mode) ^ stat.S_IXUSR)
        return Path(result)

    monkeypatch.setattr(mutate_run.shutil, "copytree", copy)
    monkeypatch.setattr(
        runner,
        "run_pytest",
        lambda *args, **kwargs: pytest.fail("a partial private copy is not a baseline"),
    )
    try:
        assert runner.execute(mutants[0], 0)["status"] == "INFRA_ERROR"
    finally:
        runner.close()


def test_resume_history_is_streamed_and_requires_provenance(
    tmp_path: Path,
) -> None:
    identity = {"captured": "inputs"}
    campaign = digest_json(identity)
    manifest = {mid: {"id": mid} for mid in ("a", "b")}
    path = tmp_path / "results.jsonl"
    row = {
        "id": "a",
        "campaign_id": campaign,
        "mutant_digest": digest_json(manifest["a"]),
        "provenance": identity,
        "status": "KILLED",
        "tail": "x" * 262144,
    }
    with path.open("w") as output:
        for _ in range(128):
            output.write(json.dumps(row) + "\n")
        output.write(json.dumps({**row, "status": "INFRA_ERROR", "tail": ""}) + "\n")
        output.write(
            json.dumps(
                {
                    **row,
                    "id": "b",
                    "mutant_digest": digest_json(manifest["b"]),
                    "status": "SURVIVED",
                    "tail": "",
                }
            )
            + "\n"
        )
    tracemalloc.start()
    try:
        assert mutate_run.compatible_terminal_ids(path, campaign, manifest, identity) == {"b"}
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert path.stat().st_size > 32 * 1024 * 1024
    assert peak < 4 * 1024 * 1024
    with path.open("a") as output:
        output.write(
            json.dumps(
                {
                    "id": "b",
                    "campaign_id": campaign,
                    "mutant_digest": digest_json(manifest["b"]),
                    "status": "KILLED",
                }
            )
            + "\n"
        )
    assert mutate_run.compatible_terminal_ids(path, campaign, manifest, identity) == set()


def test_large_baseline_proof_cache_is_disk_backed_and_materializes_one_receipt(
    tmp_path: Path,
) -> None:
    tracemalloc.start()
    try:
        proofs = mutate_run.BaselineProofCache(tmp_path / "proofs")
        for index in range(128):
            proofs[str(index)] = {"reports": [{"nodeid": f"case-{index}-" + "x" * 262144}]}
        assert len(proofs) == 128
        assert proofs["7"]["reports"][0]["nodeid"].startswith("case-7-")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert sum(path.stat().st_size for path in proofs.directory.iterdir()) > 32 * 1024 * 1024
    assert peak < 4 * 1024 * 1024
    proofs.clear()
    assert not proofs and not any(proofs.directory.iterdir())


def test_changed_cached_proof_cannot_reuse_a_passing_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _backend, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    try:
        assert runner.execute(mutants[0], 0)["status"] == "KILLED"
        assert isinstance(runner.baseline_receipts, mutate_run.BaselineProofCache)
        path = next(runner.baseline_receipts.directory.glob("*.json"))
        path.write_text("{}")
        monkeypatch.setattr(
            runner, "run_pytest", lambda *args: pytest.fail("changed cached proof is untrusted")
        )
        result = runner.execute(mutants[0], 0)
        assert result["status"] == "INFRA_ERROR"
        assert "cached baseline proof changed" in result["error"]
        assert runner.stop.is_set() and not runner.baselines
    finally:
        runner.close()


def test_failed_private_snapshot_copy_stops_the_queue_without_running_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _backend, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)

    def fail_copy(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "controlled full filesystem")

    monkeypatch.setattr(mutate_run.shutil, "copytree", fail_copy)
    monkeypatch.setattr(
        runner, "run_pytest", lambda *args: pytest.fail("no complete private workspace")
    )
    try:
        result = runner.execute(mutants[0], 0)
        assert result["status"] == "INFRA_ERROR" and runner.stop.is_set()
        assert result["infrastructure"]["drift"]["errno"] == 28
    finally:
        runner.close()


@pytest.mark.parametrize("after_timeout", [False, True])
def test_communication_failure_reaps_process_and_disposable_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_timeout: bool
) -> None:
    _backend, mutants = _project(tmp_path, monkeypatch)
    killed: list[tuple[int, int]] = []
    dropped: list[str] = []
    waited: list[float] = []

    class Process:
        pid = 123456
        returncode = -9
        communicated = False

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def communicate(self, timeout: float | None = None) -> tuple[str, None]:
            if after_timeout and not self.communicated:
                self.communicated = True
                raise mutate_run.subprocess.TimeoutExpired("controlled", timeout)
            raise UnicodeDecodeError("utf8", b"\xff", 0, 1, "controlled invalid output")

        def wait(self, timeout: float) -> None:
            waited.append(timeout)

    monkeypatch.setattr(mutate_run.subprocess, "Popen", Process)
    monkeypatch.setattr(mutate_run.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(mutate_run, "drop_attempt_database", dropped.append)
    runner = mutate_run.Runner(1, None)
    try:
        result = runner.execute(mutants[0], 0)
        assert result["status"] == ("INCONCLUSIVE_TIMEOUT" if after_timeout else "INFRA_ERROR")
        assert result["infrastructure"]["category"] == (
            "phase_timeout" if after_timeout else "process_communication_error"
        )
        assert len(killed) == len(dropped) == len(waited) == 1
        assert dropped[0].startswith(f"herdly_mut_{runner.run_id[:10]}_")
    finally:
        runner.close()


@pytest.mark.parametrize(
    "payload", [None, {"exit_code": 2, "collected": 0, "reports": None, "collection_errors": None}]
)
def test_infrastructure_receipt_failure_retains_process_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any] | None
) -> None:
    _backend, mutants = _project(tmp_path, monkeypatch)

    class Process:
        returncode = 2

        def __init__(self, _command: Any, **options: Any) -> None:
            if payload is not None:
                Path(options["env"]["MUTATION_RECEIPT_PATH"]).write_text(json.dumps(payload))

        def communicate(self, timeout: float) -> tuple[str, None]:
            return "collection failed: exact missing dependency diagnostic", None

    monkeypatch.setattr(mutate_run.subprocess, "Popen", Process)
    runner = mutate_run.Runner(1, None)
    try:
        result = runner.execute(mutants[0], 0)
        assert result["status"] == "INFRA_ERROR"
        diagnostics = result["infrastructure"]
        assert diagnostics["exit_code"] == 2
        assert diagnostics["category"] in {"missing_pytest_receipt", "malformed_pytest_receipt"}
        assert "exact missing dependency" in diagnostics["output_tail"]
    finally:
        runner.close()
