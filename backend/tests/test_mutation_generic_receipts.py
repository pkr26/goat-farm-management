"""New full generic receipts require independently recomputed exact coverers."""

import copy
import importlib
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_mutation_harness import _project

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mutation"))
producer = importlib.import_module("mutate_run")
consumer = importlib.import_module("verify_generic_receipts")
shared = importlib.import_module("verify_domain_receipts")
identity = importlib.import_module("mutate_identity")


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """All processes operate on tiny fixture projects, without application DBs."""


@pytest.fixture(autouse=True)
def _clean_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(producer, "drop_attempt_database", lambda _: None)


def _case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str = "module"
) -> tuple[dict[str, Any], Any]:
    backend, mutants = _project(tmp_path, monkeypatch)
    mutants[0]["scope"] = scope
    (backend / "mutation/manifest.json").write_text(json.dumps([mutants[0]]))
    monkeypatch.setenv("MUTATE_FULL_PHASE", "1")
    monkeypatch.setattr(consumer, "BACKEND", backend)
    runner = producer.Runner(1, None, phase_timeout=90)
    try:
        record = runner.execute(mutants[0], 0)
        assert record["status"] == "KILLED"
        # Independent capture must never instantiate another runner or execute
        # tests while recomputing exact selections from the captured bytes.
        with monkeypatch.context() as no_runner:
            no_runner.setattr(
                producer,
                "Runner",
                lambda *args, **kwargs: pytest.fail("consumer must not construct Runner"),
            )
            context = consumer.capture_context(runner.campaign_id)
        return record, context
    finally:
        runner.close()


@pytest.mark.parametrize("scope", ["module", "function"])
def test_real_tiny_producer_receipts_are_independently_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    record, context = _case(tmp_path, monkeypatch, scope)
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps(record) + "\n")
    report, proof = consumer.verify([path], context)
    assert report["statuses"] == {"KILLED": 1}
    assert report["files"][0]["name"] == "app/alpha.py"
    assert proof["verified_assertion_kills"] == 1
    assert proof["all_target_verdicts_verified"] is True
    assert proof["raw_latest_rows"][0]["concrete_baseline_nodes"] == (2 if scope == "module" else 1)


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_selection",
        "reordered_selection",
        "sampled_policy",
        "old_generic_policy",
        "missing_cleanup_policy",
        "cleanup_bounds",
        "timeout_budget",
        "missing_timeout",
        "missing_baseline_case",
        "nonassertion_failure",
        "foreign_mutant_node",
        "mode_digest",
        "missing_mode_evidence",
    ],
)
def test_tampered_or_old_generic_evidence_cannot_prove_a_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    record, context = _case(tmp_path, monkeypatch)
    if tamper == "missing_selection":
        record["selection"].pop()
        record["n_tests"] -= 1
        record["selection_sha256"] = record["baseline"]["selection_sha256"] = identity.digest_json(
            record["selection"]
        )
    elif tamper == "reordered_selection":
        record["selection"].reverse()
        record["selection_sha256"] = record["baseline"]["selection_sha256"] = identity.digest_json(
            record["selection"]
        )
    elif tamper == "sampled_policy":
        record["selection_mode"] = "module-fallback"
    elif tamper == "old_generic_policy":
        record["provenance"]["config"].pop("generic_selection")
        # Even a self-consistent historical policy digest cannot masquerade as
        # the new complete generic selection contract.
        record["campaign_id"] = identity.digest_json(record["provenance"])
        context.campaign = record["campaign_id"]
    elif tamper in {"missing_cleanup_policy", "cleanup_bounds"}:
        if tamper == "missing_cleanup_policy":
            record["provenance"]["config"].pop("database_cleanup")
        else:
            record["provenance"]["config"]["database_cleanup"]["drop_timeout_seconds"] = 45
        record["campaign_id"] = identity.digest_json(record["provenance"])
        context.campaign = record["campaign_id"]
    elif tamper == "timeout_budget":
        record["timeout_seconds"] += 1
    elif tamper == "missing_timeout":
        record["baseline"].pop("timeout_seconds")
    elif tamper == "mode_digest":
        record["provenance"]["config"]["execution_modes_sha256"] = "0" * 64
        record["campaign_id"] = identity.digest_json(record["provenance"])
        context.campaign = record["campaign_id"]
    elif tamper == "missing_mode_evidence":
        record["baseline"].pop("execution_modes_after_sha256")
    elif tamper == "missing_baseline_case":
        record["baseline"]["pytest_receipt"]["reports"] = record["baseline"]["pytest_receipt"][
            "reports"
        ][:-3]
        record["baseline"]["pytest_receipt"]["collected"] -= 1
    elif tamper == "nonassertion_failure":
        for row in record["pytest_receipt"]["reports"]:
            if row["outcome"] == "failed":
                row["assertion"] = False
    elif tamper == "foreign_mutant_node":
        for row in record["pytest_receipt"]["reports"]:
            row["nodeid"] = "tests/test_foreign.py::test_unrecorded"
    path = tmp_path / "tampered.jsonl"
    path.write_text(json.dumps(record) + "\n")
    _report, proof = consumer.verify([path], context)
    assert proof["verified_assertion_kills"] == 0
    assert proof["all_target_verdicts_verified"] is False
    assert proof["error_count"] > 0


def test_latest_unproven_or_timeout_receipt_supersedes_a_verified_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, context = _case(tmp_path, monkeypatch)
    original, recovery = tmp_path / "original.jsonl", tmp_path / "recovery.jsonl"
    original.write_text(json.dumps(record) + "\n")
    unproven = {key: record[key] for key in ("id", "campaign_id", "mutant_digest")}
    unproven["status"] = "INFRA_ERROR"
    recovery.write_text(json.dumps(unproven) + "\n")
    report, proof = consumer.verify([original, recovery], context)
    assert report["statuses"] == {"INFRA_ERROR": 1}
    assert proof["verified_assertion_kills"] == 0
    assert proof["missing_provenance_latest_ids"] == [record["id"]]
    unproven = {**record, "status": "INCONCLUSIVE_TIMEOUT"}
    recovery.write_text(json.dumps(unproven) + "\n")
    report, proof = consumer.verify([original, recovery], context)
    assert report["statuses"] == {"INCONCLUSIVE_TIMEOUT": 1}
    assert proof["verified_complete_latest_verdicts"] == 0


def test_survival_requires_every_clean_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, context = _case(tmp_path, monkeypatch)
    record["status"] = "SURVIVED"
    record["pytest_receipt"] = copy.deepcopy(record["baseline"]["pytest_receipt"])
    assert shared.verify_definitive(record, context)["concrete_touched_nodes"] == 2
    record["pytest_receipt"]["reports"] = record["pytest_receipt"]["reports"][:-3]
    with pytest.raises(consumer.EvidenceError, match="omits baseline"):
        shared.verify_definitive(record, context)


def test_generic_verification_rejects_mode_only_change_after_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, context = _case(tmp_path, monkeypatch)
    path = consumer.BACKEND / "app/alpha.py"
    path.chmod(stat.S_IMODE(path.stat().st_mode) ^ stat.S_IXUSR)
    assert identity.input_identity(consumer.BACKEND) == context.inputs
    evidence = tmp_path / "results.jsonl"
    evidence.write_text(json.dumps(record) + "\n")
    with pytest.raises(consumer.EvidenceError, match="inputs/artifacts changed"):
        consumer.verify([evidence], context)


def test_generic_capture_uses_captured_coverage_and_provenance_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _ = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(consumer, "BACKEND", backend)
    data_path = backend / ".coverage-mut"
    provenance_path = backend / ".coverage-mut.provenance.json"
    captured_data, captured_provenance = data_path.read_bytes(), provenance_path.read_bytes()
    original = producer.load_coverage_contexts

    def replace_and_restore(data: bytes, provenance: dict[str, Any]) -> Any:
        assert data == captured_data
        assert provenance == json.loads(captured_provenance)
        data_path.write_bytes(b"unrelated replacement")
        provenance_path.write_text("{}")
        try:
            return original(data, provenance)
        finally:
            data_path.write_bytes(captured_data)
            provenance_path.write_bytes(captured_provenance)

    monkeypatch.setattr(producer, "load_coverage_contexts", replace_and_restore)
    context = consumer.capture_context("a" * 64)
    assert context.artifacts["coverage_sha256"] == identity.sha_bytes(captured_data)
    assert context.artifacts["coverage_provenance_sha256"] == identity.sha_bytes(
        captured_provenance
    )


def test_generic_capture_rejects_changed_inputs_and_old_maps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _ = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(consumer, "BACKEND", backend)
    original = producer.load_coverage_contexts

    def change_test(data: bytes, provenance: dict[str, Any]) -> Any:
        (backend / "tests/test_alpha.py").write_text("def test_changed():\n    assert True\n")
        return original(data, provenance)

    with monkeypatch.context() as changed:
        changed.setattr(producer, "load_coverage_contexts", change_test)
        with pytest.raises(consumer.EvidenceError, match="inputs/artifacts changed"):
            consumer.capture_context("a" * 64)
    # A self-consistent old coverage publication still lacks the inventory
    # necessary to independently establish complete module selections.
    provenance_path = backend / ".coverage-mut.provenance.json"
    metadata = json.loads(provenance_path.read_text())
    metadata["inputs"] = identity.input_identity(backend)
    metadata.pop("collection_inventory")
    provenance_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="inventory missing"):
        consumer.capture_context("a" * 64)
