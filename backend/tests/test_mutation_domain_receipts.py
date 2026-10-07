"""Adversarial fixtures for expansion-aware domain receipt verification."""

import copy
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

MUTATION = Path(__file__).resolve().parents[1] / "mutation"
sys.path.insert(0, str(MUTATION))
# CI type-checks these standalone scripts separately from the tests.
mutate_domains = importlib.import_module("mutate_domains")
mutate_identity = importlib.import_module("mutate_identity")
mutate_run = importlib.import_module("mutate_run")
verifier = importlib.import_module("verify_domain_receipts")


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Pure receipt contracts have no application database lifecycle."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Pure receipt contracts do not truncate application tables."""


def _reports(node: str, *, call: str = "passed", setup: str = "passed") -> list[dict[str, Any]]:
    reports = [{"nodeid": node, "when": "setup", "outcome": setup, "assertion": False}]
    if setup != "skipped":
        reports.append(
            {
                "nodeid": node,
                "when": "call",
                "outcome": call,
                "assertion": call == "failed",
            }
        )
    reports.append({"nodeid": node, "when": "teardown", "outcome": "passed", "assertion": False})
    return reports


def _case() -> tuple[dict[str, Any], Any]:
    nodes = [
        "tests/test_farm.py::TestForecast::test_months[positive rate]",
        "tests/test_farm.py::TestForecast::test_months[zero::case]",
        "tests/test_extra.py::test_skipped",
    ]
    selection = [
        "tests/test_farm.py",
        "tests/test_farm.py::TestForecast",
        "tests/test_farm.py::TestForecast::test_months",
        nodes[1],
        "tests/test_extra.py",
    ]
    baseline_receipt = {
        "exit_code": 0,
        "collected": len(nodes),
        "collection_errors": [],
        "reports": [
            *_reports(nodes[0]),
            *_reports(nodes[1]),
            *_reports(nodes[2], setup="skipped"),
        ],
    }
    mutant_receipt = {
        "exit_code": 1,
        "collected": len(nodes),
        "collection_errors": [],
        "reports": _reports(nodes[0], call="failed"),
    }
    mutant = {
        "id": "farm",
        "domain": 28,
        "file": "app/simulation/engine.py",
        "line": 1,
        "kind": "compare",
        "detail": "Lt -> LtE",
        "tier": 1,
    }
    inputs = {
        "sources": {"app/simulation/engine.py": "a" * 64},
        "harness": {"mutate_run.py": "b" * 64},
    }
    artifacts = {
        "manifest_sha256": "c" * 64,
        "coverage_sha256": "d" * 64,
        "coverage_provenance_sha256": "f" * 64,
    }
    provenance = {
        "schema": 2,
        **inputs,
        **artifacts,
        "config": {
            "test_database_admin_sha256": "e" * 64,
            "full": True,
            "workers": 12,
            "phase_timeout": 7200,
            "sample_cap": 60,
            "domain_selection": "all-coverers-or-declared-domain-suite-v1",
        },
    }
    campaign = mutate_identity.digest_json(provenance)
    record = {
        **{key: mutant[key] for key in ("id", "file", "line", "kind", "detail", "tier")},
        "status": "KILLED",
        "status_policy": "assertions-only-timeouts-inconclusive",
        "campaign_id": campaign,
        "mutant_digest": mutate_identity.digest_json(mutant),
        "provenance": provenance,
        "selection": selection,
        "selection_sha256": mutate_identity.digest_json(selection),
        "selection_mode": "complete",
        "n_tests": len(selection),
        "baseline": {
            "status": "pass",
            "selection_sha256": mutate_identity.digest_json(selection),
            "pytest_receipt": baseline_receipt,
        },
        "pytest_receipt": mutant_receipt,
    }
    immutable_selection = tuple(selection)
    context = verifier.Context(
        campaign,
        {"farm": mutant},
        inputs,
        artifacts,
        "e" * 64,
        lambda mutant: list(immutable_selection),
    )
    return record, context


def test_overlapping_file_class_family_and_parameter_selectors_expand_without_losing_cases() -> (
    None
):
    record, context = _case()
    facts = verifier.verify_definitive(record, context)
    assert facts == {
        "concrete_baseline_nodes": 3,
        "concrete_touched_nodes": 1,
        "selector_count": 5,
    }


def test_survival_requires_every_baseline_case_including_skips() -> None:
    record, context = _case()
    record["status"] = "SURVIVED"
    record["pytest_receipt"] = copy.deepcopy(record["baseline"]["pytest_receipt"])
    assert verifier.verify_definitive(record, context)["concrete_touched_nodes"] == 3
    record["pytest_receipt"]["reports"] = record["pytest_receipt"]["reports"][:-2]
    with pytest.raises(verifier.EvidenceError, match="omits baseline"):
        verifier.verify_definitive(record, context)


@pytest.mark.parametrize(
    "tamper",
    [
        "uncovered_selector",
        "outside_baseline_node",
        "missing_baseline_node",
        "baseline_call_failure",
        "baseline_teardown_failure",
        "baseline_missing_teardown",
        "mutant_setup_failure",
        "mutant_teardown_failure",
        "mutant_nonassertion_failure",
        "mutant_duplicate_report",
        "mutant_foreign_node",
        "mutant_wrong_collection",
        "mutant_exit_disagreement",
        "ordered_selection_hash",
        "sampled_mode",
        "wrong_endpoint",
    ],
)
def test_contradictory_or_incomplete_evidence_cannot_prove_a_kill(tamper: str) -> None:
    record, context = _case()
    baseline = record["baseline"]["pytest_receipt"]
    mutant = record["pytest_receipt"]
    if tamper == "uncovered_selector":
        record["selection"].append("tests/test_farm.py::test_missing")
        record["n_tests"] += 1
        record["selection_sha256"] = record["baseline"]["selection_sha256"] = (
            mutate_identity.digest_json(record["selection"])
        )
    elif tamper == "outside_baseline_node":
        baseline["reports"] += _reports("tests/test_unselected.py::test_unselected")
        baseline["collected"] += 1
    elif tamper == "missing_baseline_node":
        baseline["reports"] = baseline["reports"][:-2]
    elif tamper == "baseline_call_failure":
        baseline["reports"][1].update(outcome="failed", assertion=True)
    elif tamper == "baseline_teardown_failure":
        baseline["reports"][2]["outcome"] = "failed"
    elif tamper == "baseline_missing_teardown":
        baseline["reports"].pop()
    elif tamper == "mutant_setup_failure":
        mutant["reports"][0]["outcome"] = "failed"
    elif tamper == "mutant_teardown_failure":
        mutant["reports"][2]["outcome"] = "failed"
    elif tamper == "mutant_nonassertion_failure":
        mutant["reports"][1]["assertion"] = False
    elif tamper == "mutant_duplicate_report":
        mutant["reports"].append(copy.deepcopy(mutant["reports"][0]))
    elif tamper == "mutant_foreign_node":
        for row in mutant["reports"]:
            row["nodeid"] = "tests/test_foreign.py::test_not_in_baseline"
    elif tamper == "mutant_wrong_collection":
        mutant["collected"] += 1
    elif tamper == "mutant_exit_disagreement":
        mutant["exit_code"] = 0
    elif tamper == "ordered_selection_hash":
        record["selection"].reverse()
    elif tamper == "sampled_mode":
        record["selection_mode"] = "module-fallback"
    elif tamper == "wrong_endpoint":
        record["provenance"]["config"]["test_database_admin_sha256"] = "0" * 64
    with pytest.raises(verifier.EvidenceError):
        verifier.verify_definitive(record, context)


def test_streaming_latest_statuses_retain_current_semantics_and_ignore_other_campaigns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, context = _case()
    earlier = copy.deepcopy(record)
    earlier["status"] = "SURVIVED"
    earlier["pytest_receipt"] = copy.deepcopy(earlier["baseline"]["pytest_receipt"])
    old = {
        "id": "historical",
        "status": "KILLED",
        "campaign_id": "old",
        "provenance": {},
    }
    path = tmp_path / "results.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in (old, earlier, record)))
    report, verification = verifier.stream_verify(path, context)
    assert report["attempted"] == 1
    assert report["statuses"] == {"KILLED": 1}
    assert report["incompatible_historical_attempts"] == 1
    assert verification["verified_assertion_kills"] == 1
    assert verification["error_count"] == 0
    (tmp_path / "manifest.json").write_text(json.dumps(list(context.manifest.values())))
    fingerprints = {
        "manifest.json": context.artifacts["manifest_sha256"],
        ".coverage-mut": context.artifacts["coverage_sha256"],
        ".coverage-mut.provenance.json": context.artifacts["coverage_provenance_sha256"],
    }
    monkeypatch.setattr(mutate_domains, "MUTDIR", tmp_path)
    monkeypatch.setattr(mutate_domains, "input_identity", lambda _: context.inputs)
    monkeypatch.setattr(mutate_domains, "sha_file", lambda path: fingerprints[path.name])
    mutate_domains.summarize()
    assert json.loads((tmp_path / "domain-report.json").read_text()) == report


@pytest.mark.parametrize(
    "tamper", ["manifest", "source_inputs", "coverage", "provenance_digest", "metadata"]
)
def test_streaming_verifier_rejects_wrong_current_fingerprints(tmp_path: Path, tamper: str) -> None:
    record, context = _case()
    if tamper == "manifest":
        record["mutant_digest"] = "0" * 64
    elif tamper == "source_inputs":
        record["provenance"]["sources"] = {}
    elif tamper == "coverage":
        record["provenance"]["coverage_sha256"] = "0" * 64
    elif tamper == "provenance_digest":
        record["provenance"]["schema"] = 99
    elif tamper == "metadata":
        record["line"] = 999
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps(record) + "\n")
    _, verification = verifier.stream_verify(path, context)
    assert verification["error_count"] == 1
    assert verification["verified_assertion_kills"] == 0


@pytest.mark.parametrize(
    "status", ["INFRA_ERROR", "INCONCLUSIVE_TIMEOUT", "INVALID", "NOT_COVERED"]
)
def test_unmeasured_statuses_never_improve_verified_kills(tmp_path: Path, status: str) -> None:
    record, context = _case()
    record["status"] = status
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps(record) + "\n")
    report, verification = verifier.stream_verify(path, context)
    assert report["statuses"] == {status: 1}
    assert verification["verified_assertion_kills"] == 0
    assert verification["unmeasured_latest_statuses"] == {status: 1}


def test_provenance_interning_never_trusts_a_changed_duplicate(tmp_path: Path) -> None:
    record, context = _case()
    changed = copy.deepcopy(record)
    changed["provenance"]["config"]["phase_timeout"] = 1
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps(record) + "\n" + json.dumps(changed) + "\n")
    _, verification = verifier.stream_verify(path, context)
    assert verification["error_count"] == 1
    assert verification["all_target_verdicts_verified"] is False
    assert verification["verified_assertion_kills"] == 0


def test_later_unproven_worker_infrastructure_error_supersedes_a_previous_kill(
    tmp_path: Path,
) -> None:
    record, context = _case()
    later = {
        "id": record["id"],
        "campaign_id": context.campaign,
        "mutant_digest": record["mutant_digest"],
        "status": "INFRA_ERROR",
        "run_id": "worker",
        "error": "worker exception",
    }
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps(record) + "\n" + json.dumps(later) + "\n")
    report, verification = verifier.stream_verify(path, context)
    assert report["statuses"] == {"INFRA_ERROR": 1}
    assert verification["verified_assertion_kills"] == 0
    assert verification["unmeasured_latest_statuses"] == {"INFRA_ERROR": 1}
    assert verification["missing_provenance_latest_ids"] == [record["id"]]


def test_self_consistent_sampled_selection_cannot_fake_complete_domain_coverage() -> None:
    record, context = _case()
    record["selection"].pop()
    record["n_tests"] -= 1
    record["selection_sha256"] = record["baseline"]["selection_sha256"] = (
        mutate_identity.digest_json(record["selection"])
    )
    record["baseline"]["pytest_receipt"]["reports"] = record["baseline"]["pytest_receipt"][
        "reports"
    ][:-2]
    record["baseline"]["pytest_receipt"]["collected"] -= 1
    record["pytest_receipt"]["collected"] -= 1
    with pytest.raises(verifier.EvidenceError, match="independently recomputed"):
        verifier.verify_definitive(record, context)


def _captured_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, dict[str, Any], bytes]:
    record, _ = _case()
    backend = tmp_path / "backend"
    (backend / "mutation").mkdir(parents=True)
    (backend / "app").mkdir()
    (backend / "tests").mkdir()
    (backend / "app/engine.py").write_text("original = 1\n")
    (backend / "tests/test_farm.py").write_text("def test_valid():\n    assert True\n")
    (backend / "mutation/manifest.json").write_text(
        json.dumps(
            [
                {
                    **{
                        key: record[key] for key in ("id", "file", "line", "kind", "detail", "tier")
                    },
                    "domain": 28,
                }
            ]
        )
    )
    captured = b"captured original coverage bytes"
    metadata = {
        "inputs": mutate_identity.input_identity(backend),
        "coverage_sha256": mutate_identity.sha_bytes(captured),
        "baseline_exit_code": 0,
        "test_database_admin_sha256": mutate_identity.database_admin_sha256(),
    }
    (backend / ".coverage-mut").write_bytes(captured)
    (backend / ".coverage-mut.provenance.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(verifier, "BACKEND", backend)
    return backend, metadata, captured


def test_selection_loader_parses_exact_captured_bytes_despite_replace_and_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, metadata, captured = _captured_project(tmp_path, monkeypatch)

    def load(data: bytes, provenance: dict[str, Any]) -> dict[str, Any]:
        assert data == captured
        assert provenance == metadata
        original = (backend / ".coverage-mut.provenance.json").read_bytes()
        (backend / ".coverage-mut").write_bytes(b"replaced while parsing")
        (backend / ".coverage-mut.provenance.json").write_text("{}")
        try:
            return {"app/simulation/engine.py": {1: {"tests/test_farm.py::test_valid"}}}
        finally:
            (backend / ".coverage-mut").write_bytes(captured)
            (backend / ".coverage-mut.provenance.json").write_bytes(original)

    monkeypatch.setattr(mutate_run, "load_coverage_contexts", load)
    context = verifier.capture_context("a" * 64)
    assert context.artifacts["coverage_sha256"] == mutate_identity.sha_bytes(captured)
    assert context.expected_selection(context.manifest["farm"]) == [
        "tests/test_farm.py::test_valid"
    ]


def test_selection_loader_rejects_test_inputs_changed_during_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _, _ = _captured_project(tmp_path, monkeypatch)

    def load(data: bytes, provenance: dict[str, Any]) -> dict[str, Any]:
        (backend / "tests/test_farm.py").write_text("def test_changed():\n    assert True\n")
        return {"app/simulation/engine.py": {1: {"tests/test_farm.py::test_valid"}}}

    monkeypatch.setattr(mutate_run, "load_coverage_contexts", load)
    with pytest.raises(verifier.EvidenceError, match="inputs/artifacts changed"):
        verifier.capture_context("a" * 64)
