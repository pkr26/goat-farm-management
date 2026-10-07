"""Adversarial protocol gates using labeled synthetic harness receipts."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import MethodType
from typing import Any

import pytest

MUTATION = Path(__file__).resolve().parents[2] / "mutation"
sys.path.insert(0, str(MUTATION))
# CI checks standalone mutation scripts independently from test modules.
boot = importlib.import_module("bootstrap_policy")
producer = importlib.import_module("mutate_run")
identity_module = importlib.import_module("mutate_identity")
verifier = importlib.import_module("verify_domain_receipts")
digest_json = identity_module.digest_json
input_identity = identity_module.input_identity
EvidenceError = verifier.EvidenceError
verify_definitive = verifier.verify_definitive


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Synthetic protocol gates own no application database lifecycle."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Protocol fixtures do not truncate application tables."""


SOURCE = b"value: float | None = None\n"
NATIVE = [f"tests/test_native.py::test_native[{index}]" for index in range(111)]


def receipt(nodes: list[str], failure: str | None = None) -> dict[str, Any]:
    touched = nodes[:1] if failure else nodes
    reports = []
    for node in touched:
        reports.extend(
            [
                {"nodeid": node, "when": "setup", "outcome": "passed", "assertion": False},
                {
                    "nodeid": node,
                    "when": "call",
                    "outcome": "failed" if failure else "passed",
                    "assertion": failure == "assertion",
                },
                {"nodeid": node, "when": "teardown", "outcome": "passed", "assertion": False},
            ]
        )
    return {
        "exit_code": 1 if failure else 0,
        "collected": len(nodes),
        "reports": reports,
        "collection_errors": [],
    }


def runner(
    tmp_path: Path, boot_status: str = "kill", native_status: str = "pass"
) -> tuple[Any, dict[str, Any], Any, list[dict[str, Any]]]:
    snapshot = tmp_path / "snapshot"
    backend = snapshot / producer.BACKEND.name
    (backend / "app").mkdir(parents=True)
    (backend / "app/demo.py").write_bytes(SOURCE)
    script = backend / boot.SCRIPT
    script.parent.mkdir(parents=True)
    script.write_text(
        "def test_backend_constructs_and_serves_the_public_health_app():\n    assert True\n"
    )
    (backend / "tests/test_native.py").write_text("# Synthetic protocol-inventory fixture only.\n")
    inputs = input_identity(backend)
    mutant = {
        "id": "import-time-scope",
        "file": "app/demo.py",
        "line": 1,
        "tier": 2,
        "kind": "binop",
        "detail": "BitOr -> BitAnd",
        "scope": "module",
        "source_sha256": hashlib.sha256(SOURCE).hexdigest(),
        "orig_span": SOURCE.decode(),
        "stmt_line": 1,
        "stmt_end_line": 1,
        "stmt_col": 0,
        "mut_stmt": "value: float & None = None",
    }
    identity = {
        "schema": 2,
        **inputs,
        "manifest_sha256": "a" * 64,
        "coverage_sha256": "b" * 64,
        "coverage_provenance_sha256": "c" * 64,
        "config": {
            "full": True,
            "domain_selection": "all-coverers-or-declared-domain-suite-v1",
            "test_database_admin_sha256": "d" * 64,
            "bootstrap": boot.configuration(inputs),
        },
    }
    instance = object.__new__(producer.Runner)
    instance.inputs = inputs
    instance.identity = identity
    instance.campaign_id = digest_json(identity)
    instance.run_id = "gate-fixture-not-a-campaign"
    instance.manifest = {mutant["id"]: mutant}
    instance._mutant_digests = {mutant["id"]: digest_json(mutant)}
    instance.snapshot = snapshot
    instance.local = threading.local()
    instance.bootstrap_first = True
    instance.full_phase = True
    instance.baselines = {}
    instance.baseline_receipts = {}
    instance.baseline_phase_identities = {}
    instance.baseline_lock = threading.Lock()
    instance.baseline_in_flight = {}
    calls: list[dict[str, Any]] = []

    def select(self: Any, _mutant: dict[str, Any]) -> tuple[list[str], bool]:
        return NATIVE, False

    def execute(
        self: Any, nodes: list[str], worker: int, *, confcutdir: str | None = None
    ) -> tuple[str, str, float]:
        state = (
            "clean" if (self.local.workspace / mutant["file"]).read_bytes() == SOURCE else "mutant"
        )
        status = "pass" if state == "clean" else boot_status if confcutdir else native_status
        result = receipt(
            nodes,
            "assertion" if status == "kill" else "application-error" if status == "infra" else None,
        )
        self.local.pytest_receipt = result
        self.local.phase_identity = {
            "inputs_before": input_identity(self.local.workspace),
            "inputs_after": input_identity(self.local.workspace),
            "confcutdir": confcutdir,
            "cleanup_error": None,
        }
        calls.append(
            {"state": state, "selection": list(nodes), "confcutdir": confcutdir, "worker": worker}
        )
        return status, "synthetic protocol gate outcome", 0.01

    instance.__dict__["select_tests"] = MethodType(select, instance)
    instance.__dict__["run_pytest"] = MethodType(execute, instance)
    context = verifier.Context(
        instance.campaign_id,
        instance.manifest,
        inputs,
        {
            k: identity[k]
            for k in ("manifest_sha256", "coverage_sha256", "coverage_provenance_sha256")
        },
        identity["config"]["test_database_admin_sha256"],
        lambda _m: NATIVE,
        source_bytes=lambda _f: SOURCE,
    )
    return instance, mutant, context, calls


def test_bootstrap_assertion_kill_keeps_all_111_native_clean_cases_but_zero_native_mutant_cases(
    tmp_path: Path,
) -> None:
    instance, mutant, context, calls = runner(tmp_path)
    record = instance.execute(mutant, 0)
    assert record["status"] == "KILLED"
    assert [c["state"] for c in calls] == ["clean", "clean", "mutant"]
    assert [c["confcutdir"] for c in calls] == [boot.CONFCUTDIR, None, boot.CONFCUTDIR]
    assert (
        record["selection"] == NATIVE and record["baseline"]["pytest_receipt"]["collected"] == 111
    )
    assert record["bootstrap"]["clean"]["pytest_receipt"]["collected"] == 1
    assert record["bootstrap"]["mutant"]["pytest_receipt"]["collected"] == 1
    assert record["pytest_receipt"] is None and record["native_mutant_attempted"] is False
    assert record["native_mutant"] == {
        "status": "not-attempted",
        "reason": "bootstrap-assertion-kill",
    }
    verified = verify_definitive(record, context)
    assert verified["concrete_native_clean_nodes"] == 111
    assert verified["concrete_native_mutant_nodes"] == 0
    assert verified["concrete_bootstrap_mutant_nodes"] == 1
    assert (
        record["edited_source_sha256"]
        == hashlib.sha256(b"value: float & None = None\n").hexdigest()
    )


@pytest.mark.parametrize("native_status", ["pass", "kill", "infra", "timeout"])
def test_bootstrap_pass_continues_to_the_exact_native_selection(
    tmp_path: Path, native_status: str
) -> None:
    instance, mutant, context, calls = runner(tmp_path, "pass", native_status)
    record = instance.execute(mutant, 0)
    assert len(calls) == 4 and calls[-1]["selection"] == NATIVE and calls[-1]["confcutdir"] is None
    assert record["native_mutant_attempted"] is True
    assert (
        record["status"]
        == {
            "pass": "SURVIVED",
            "kill": "KILLED",
            "infra": "INFRA_ERROR",
            "timeout": "INCONCLUSIVE_TIMEOUT",
        }[native_status]
    )
    if native_status in {"pass", "kill"}:
        verify_definitive(record, context)


@pytest.mark.parametrize("boot_status", ["infra", "timeout"])
def test_non_assertion_or_timeout_bootstrap_failure_never_becomes_a_kill(
    tmp_path: Path, boot_status: str
) -> None:
    instance, mutant, _context, calls = runner(tmp_path, boot_status)
    record = instance.execute(mutant, 0)
    assert len(calls) == 3 and record["native_mutant_attempted"] is False
    assert (
        record["status"] == {"infra": "INFRA_ERROR", "timeout": "INCONCLUSIVE_TIMEOUT"}[boot_status]
    )


@pytest.mark.parametrize(
    "attack",
    [
        "missing_boot_clean",
        "missing_boot_mutant",
        "false_boot_kill",
        "collection_error",
        "setup_failure",
        "teardown_failure",
        "non_assertion_call",
        "extra_boot_node",
        "wrong_native_selection",
        "reordered_native_selection",
        "wrong_boot_selection",
        "wrong_boundary",
        "wrong_script_hash",
        "stale_source_before",
        "changed_source_after",
        "stale_tests",
        "wrong_edited_source",
        "native_collection_invented",
        "native_marker_forged",
        "native_control_missing_node",
        "wrong_campaign",
        "wrong_mutant_digest",
        "cleanup_failed",
    ],
)
def test_consumer_rejects_forged_or_incomplete_phase_evidence(tmp_path: Path, attack: str) -> None:
    instance, mutant, context, _calls = runner(tmp_path)
    original = instance.execute(mutant, 0)
    record = copy.deepcopy(original)
    phase = record["bootstrap"]["mutant"]
    if attack == "missing_boot_clean":
        record["bootstrap"]["clean"] = None
    elif attack == "missing_boot_mutant":
        record["bootstrap"]["mutant"] = None
    elif attack == "false_boot_kill":
        phase["pytest_receipt"] = receipt(boot.SELECTION)
        phase["exit_code"] = 0
    elif attack == "collection_error":
        phase["pytest_receipt"]["collection_errors"] = ["test_import"]
        phase["pytest_receipt"]["exit_code"] = phase["exit_code"] = 2
    elif attack == "setup_failure":
        phase["pytest_receipt"]["reports"][0]["outcome"] = "failed"
    elif attack == "teardown_failure":
        phase["pytest_receipt"]["reports"][-1]["outcome"] = "failed"
    elif attack == "non_assertion_call":
        phase["pytest_receipt"]["reports"][1]["assertion"] = False
    elif attack == "extra_boot_node":
        phase["pytest_receipt"] = receipt(
            [*boot.SELECTION, "tests/bootstrap/extra.py::test_extra"], "assertion"
        )
    elif attack == "wrong_native_selection":
        record["selection"] = NATIVE[:-1]
        record["n_tests"] -= 1
        record["selection_sha256"] = digest_json(record["selection"])
    elif attack == "reordered_native_selection":
        record["selection"] = list(reversed(NATIVE))
        record["selection_sha256"] = digest_json(record["selection"])
    elif attack == "wrong_boot_selection":
        phase["selection"] = ["tests/bootstrap/test_wrong.py"]
        phase["selection_sha256"] = digest_json(phase["selection"])
    elif attack == "wrong_boundary":
        phase["confcutdir"] = None
    elif attack == "wrong_script_hash":
        record["bootstrap"]["configuration"]["script_sha256"] = "f" * 64
    elif attack == "stale_source_before":
        phase["inputs_before"]["sources"][mutant["file"]] = mutant["source_sha256"]
    elif attack == "changed_source_after":
        phase["inputs_after"]["sources"][mutant["file"]] = "f" * 64
    elif attack == "stale_tests":
        record["baseline"]["inputs_after"]["tests"][boot.SCRIPT] = "f" * 64
    elif attack == "wrong_edited_source":
        record["edited_source_sha256"] = "f" * 64
    elif attack == "native_collection_invented":
        record["pytest_receipt"] = receipt(NATIVE)
        record["native_mutant_attempted"] = True
    elif attack == "native_marker_forged":
        record["native_mutant"]["collected"] = 111
    elif attack == "native_control_missing_node":
        record["baseline"]["pytest_receipt"] = receipt(NATIVE[:-1])
    elif attack == "wrong_campaign":
        record["campaign_id"] = "f" * 64
    elif attack == "wrong_mutant_digest":
        record["mutant_digest"] = "f" * 64
    elif attack == "cleanup_failed":
        phase["cleanup_error"] = "CleanupError"
    with pytest.raises(EvidenceError):
        verify_definitive(record, context)
    assert json.dumps(original, sort_keys=True) != json.dumps(record, sort_keys=True)


def test_consumer_independently_reconstructs_source_instead_of_trusting_producer_sha(
    tmp_path: Path,
) -> None:
    instance, mutant, context, _calls = runner(tmp_path)
    record = instance.execute(mutant, 0)
    context.source_bytes = lambda _f: b"value: float | None = 0\n"
    with pytest.raises(EvidenceError, match="stale clean source"):
        verify_definitive(record, context)


def test_legacy_record_cannot_enter_a_bootstrap_policy_campaign(tmp_path: Path) -> None:
    instance, mutant, context, _calls = runner(tmp_path)
    record = instance.execute(mutant, 0)
    record["status_policy"] = "assertions-only-timeouts-inconclusive"
    with pytest.raises(EvidenceError, match="legacy"):
        verify_definitive(record, context)


@pytest.mark.parametrize(
    "phase_name", ["boot_clean", "native_clean", "boot_mutant", "native_mutant"]
)
@pytest.mark.parametrize(
    "attack", ["missing_receipt", "setup_failure", "non_assertion", "stale_inputs"]
)
def test_producer_does_not_publish_a_kill_from_invalid_phase_evidence(
    tmp_path: Path, phase_name: str, attack: str
) -> None:
    instance, mutant, _context, calls = runner(
        tmp_path, "pass" if phase_name == "native_mutant" else "kill", "kill"
    )
    original_run: Callable[..., tuple[str, str, float]] = instance.run_pytest

    def corrupt(
        self: Any,
        nodes: list[str],
        worker: int,
        *,
        confcutdir: str | None = None,
    ) -> tuple[str, str, float]:
        result = original_run(nodes, worker, confcutdir=confcutdir)
        current = calls[-1]
        label = ("boot" if confcutdir else "native") + "_" + current["state"]
        if label != phase_name:
            return result
        if attack == "missing_receipt":
            self.local.pytest_receipt = None
        elif attack == "setup_failure":
            self.local.pytest_receipt["reports"][0]["outcome"] = "failed"
        elif attack == "non_assertion":
            self.local.pytest_receipt = receipt(nodes, "application-error")
        else:
            self.local.phase_identity["inputs_after"]["sources"][mutant["file"]] = "f" * 64
        return result

    instance.__dict__["run_pytest"] = MethodType(corrupt, instance)
    record = instance.execute(mutant, 0)
    assert record["status"] == "INFRA_ERROR"
    if phase_name.endswith("clean"):
        assert all(call["state"] == "clean" for call in calls)
        assert "edited_source_sha256" not in record
    assert (instance.snapshot / producer.BACKEND.name / mutant["file"]).read_bytes() == SOURCE


@pytest.mark.parametrize("control", ["boot", "native"])
@pytest.mark.parametrize("status", ["kill", "infra", "timeout"])
def test_failed_clean_control_cannot_be_reused_as_a_passing_control(
    tmp_path: Path, control: str, status: str
) -> None:
    instance, mutant, _context, calls = runner(tmp_path)
    original_run: Callable[..., tuple[str, str, float]] = instance.run_pytest

    def fail_control(
        self: Any,
        nodes: list[str],
        worker: int,
        *,
        confcutdir: str | None = None,
    ) -> tuple[str, str, float]:
        result = original_run(nodes, worker, confcutdir=confcutdir)
        if calls[-1]["state"] == "clean" and ("boot" if confcutdir else "native") == control:
            self.local.pytest_receipt = receipt(
                nodes, "assertion" if status == "kill" else "application-error"
            )
            return status, "synthetic failed clean control", result[2]
        return result

    instance.__dict__["run_pytest"] = MethodType(fail_control, instance)
    first = instance.execute(mutant, 0)
    second = instance.execute(mutant, 0)
    expected = "INCONCLUSIVE_TIMEOUT" if status == "timeout" else "INFRA_ERROR"
    assert first["status"] == second["status"] == expected
    assert all(call["state"] == "clean" for call in calls)
    assert "edited_source_sha256" not in first and "edited_source_sha256" not in second
    failing_calls = [
        call for call in calls if ("boot" if call["confcutdir"] else "native") == control
    ]
    assert len(failing_calls) == 2


@pytest.mark.parametrize("boundary", [None, boot.CONFCUTDIR])
def test_real_pytest_command_binds_the_collection_boundary_and_private_child_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str | None
) -> None:
    instance, _mutant, _context, _calls = runner(tmp_path)
    instance.local.workspace = instance.snapshot / producer.BACKEND.name
    instance.phase_timeout = 30
    selection = boot.SELECTION if boundary else NATIVE
    spawned: list[dict[str, Any]] = []
    dropped: list[str] = []

    class Process:
        returncode = 0

        def __init__(self, command: list[str], **options: Any) -> None:
            spawned.append({"command": command, **options})
            Path(options["env"]["MUTATION_RECEIPT_PATH"]).write_text(json.dumps(receipt(selection)))

        def communicate(self, timeout: float) -> tuple[str, None]:
            assert timeout == 30
            return "synthetic command-boundary probe", None

    monkeypatch.setattr(producer.subprocess, "Popen", Process)
    monkeypatch.setattr(producer, "drop_attempt_database", dropped.append)
    status, _output, _duration = producer.Runner.run_pytest(
        instance, selection, 0, confcutdir=boundary
    )
    assert status == "pass" and len(spawned) == 1 and len(dropped) == 1
    launch = spawned[0]
    assert launch["cwd"] == instance.local.workspace
    assert launch["env"]["PYTHONPATH"] == str(instance.local.workspace)
    assert json.loads(Path(launch["env"]["MUTATION_SELECTION_PATH"]).read_text()) == selection
    boundaries = [
        argument for argument in launch["command"] if argument.startswith("--confcutdir=")
    ]
    assert boundaries == ([] if boundary is None else [f"--confcutdir={boundary}"])
    assert instance.local.phase_identity["inputs_before"] == instance.inputs
    assert instance.local.phase_identity["inputs_after"] == instance.inputs
    assert instance.local.phase_identity["confcutdir"] == boundary
    assert instance.local.phase_identity["cleanup_error"] is None
    assert dropped == [launch["env"]["GOATFARM_TEST_DB"]]


def test_recursive_coverage_discovery_retains_nested_bootstrap_modules_and_pytest_file_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = importlib.import_module("mutate_cover")
    project = tmp_path / "backend"
    (project / "mutation").mkdir(parents=True)
    (project / "mutation/coverage_inventory.py").write_bytes(
        (MUTATION / "coverage_inventory.py").read_bytes()
    )
    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'python_files = ["test_*.py", "*_contract.py"]\n'
        'norecursedirs = ["ignored"]\n'
    )
    selected = [
        "tests/test_top.py",
        "tests/bootstrap/test_application_bootstrap.py",
        "tests/bootstrap_harness/test_protocol.py",
        "tests/nested/startup_contract.py",
    ]
    for index, name in enumerate(selected):
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"def test_fact_{index}():\n    assert True\n")
    for name in ["tests/ignored/test_excluded.py", "tests/nested/helper.py"]:
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('raise RuntimeError("pytest policy excludes this file")\n')
    monkeypatch.setattr(collector, "VENV_PY", Path(sys.executable))
    environment = os.environ | {"PYTHONPATH": str(project)}
    modules, inventory = collector.discover_test_modules(project, environment)
    assert [module.relative_to(project).as_posix() for module in modules] == sorted(selected)
    assert inventory["exit_code"] == 0 and len(inventory["items"]) == 4
    assert {item["file"] for item in inventory["items"]} == set(selected)


@pytest.mark.parametrize(
    "attack", ["missing_inventory", "duplicate_node", "outside_tests", "collection_error"]
)
def test_coverage_discovery_cannot_publish_incomplete_or_forged_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    collector = importlib.import_module("mutate_cover")
    project = tmp_path / "backend"
    target = project / "tests/test_ok.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_ok():\n    assert True\n")
    outside = project / "outside.py"
    outside.write_text("def test_outside():\n    assert True\n")

    def failed_collect(_command: list[str], **options: Any) -> Any:
        items = [{"nodeid": "tests/test_ok.py::test_ok", "file": "tests/test_ok.py"}]
        if attack == "duplicate_node":
            items *= 2
        elif attack == "outside_tests":
            items[0]["file"] = "outside.py"
        if attack != "missing_inventory":
            Path(options["env"]["MUTATION_COVERAGE_INVENTORY_PATH"]).write_text(
                json.dumps({"exit_code": 2 if attack == "collection_error" else 0, "items": items})
            )
        return type(
            "CollectionProbe", (), {"returncode": 2 if attack == "collection_error" else 0}
        )()

    monkeypatch.setattr(collector.subprocess, "run", failed_collect)
    with pytest.raises(SystemExit, match="no coverage published"):
        collector.discover_test_modules(project, {})
