"""Domain resume/report consumers must not materialize multi-GB receipts."""

import copy
import importlib
import json
import sys
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mutation"))
domains = importlib.import_module("mutate_domains")
identity = importlib.import_module("mutate_identity")


def _mutants() -> dict[str, dict[str, Any]]:
    return {key: {"id": key, "domain": 1, "kind": "compare"} for key in ("one", "two", "three")}


def _row(
    mid: str, status: str, provenance: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, Any]:
    return {
        "id": mid,
        "status": status,
        "campaign_id": identity.digest_json(provenance),
        "provenance": provenance,
        "mutant_digest": identity.digest_json(manifest[mid]),
    }


def test_domain_resume_is_bounded_and_does_not_trust_terminal_metadata_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _mutants()
    provenance: dict[str, Any] = {"schema": 2, "config": {"full": True}}
    expected = copy.deepcopy(provenance)
    expected["config"]["domain_selection"] = "all-coverers-or-declared-domain-suite-v1"
    path = tmp_path / "results.jsonl"
    padding = (
        json.dumps({"id": "old", "status": "KILLED", "campaign_id": "old", "log": "x" * 262144})
        + "\n"
    )
    rows = [
        _row("one", "KILLED", expected, manifest),
        _row("one", "INFRA_ERROR", expected, manifest),
        _row("two", "SURVIVED", expected, manifest),
        _row("three", "KILLED", expected, manifest),
    ]
    forged = _row("three", "KILLED", expected, manifest)
    forged.pop("provenance")
    with path.open("w") as stream:
        for _ in range(48):
            stream.write(padding)
        for row in [*rows, forged]:
            stream.write(json.dumps(row) + "\n")

    def prepared(self: Any, *_args: Any, **_kwargs: Any) -> None:
        self.identity = copy.deepcopy(provenance)
        self.manifest = manifest
        self.results_path = path

    monkeypatch.setattr(domains.Runner, "__init__", prepared)
    tracemalloc.start()
    try:
        runner = domains.DomainRunner(1, None)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert runner.done_ids == {"two"}
    assert peak < 4 * 1024 * 1024


def _report_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, list[dict[str, Any]]]:
    manifest = _mutants()
    inputs: dict[str, Any] = {"sources": {"app/demo.py": "source"}, "tests": {}}
    fingerprints = {
        "manifest.json": "manifest",
        ".coverage-mut": "coverage",
        ".coverage-mut.provenance.json": "provenance",
    }
    first = {
        "schema": 2,
        **inputs,
        "config": {"workers": 1},
        "manifest_sha256": "manifest",
        "coverage_sha256": "coverage",
        "coverage_provenance_sha256": "provenance",
    }
    second = {**first, "config": {"workers": 2}}
    rows = [
        _row("one", "KILLED", first, manifest),
        _row("one", "SURVIVED", second, manifest),
        _row("one", "INFRA_ERROR", first, manifest),
        {"id": "old", "status": "KILLED", "campaign_id": "old"},
    ]
    path = tmp_path / "results.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (tmp_path / "manifest.json").write_text(json.dumps(list(manifest.values())))
    monkeypatch.setattr(domains, "MUTDIR", tmp_path)
    monkeypatch.setattr(domains, "input_identity", lambda _: inputs)
    monkeypatch.setattr(domains, "sha_file", lambda p: fingerprints[p.name])
    return path, rows


def test_streaming_report_preserves_interleaved_campaign_and_latest_status_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, rows = _report_fixture(tmp_path, monkeypatch)
    read_text = Path.read_text

    def refuse_eager_results(self: Path, *args: Any, **kwargs: Any) -> str:
        assert self != path, "the result history must be streamed"
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse_eager_results)
    domains.summarize()
    report = json.loads((tmp_path / "domain-report.json").read_text())
    assert report["campaign_id"] == rows[1]["campaign_id"]
    assert report["attempted"] == 1
    assert report["statuses"] == {"SURVIVED": 1}
    assert report["incompatible_historical_attempts"] == 1
    assert report["campaigns"][0]["missing"] == 2


def test_report_refuses_a_history_that_changes_between_its_two_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = _report_fixture(tmp_path, monkeypatch)
    original = domains.iter_results
    passes = 0

    def appending(source: Path) -> Any:
        nonlocal passes
        passes += 1
        yield from original(source)
        if passes == 1:
            with source.open("a") as stream:
                stream.write("\n")

    monkeypatch.setattr(domains, "iter_results", appending)
    with pytest.raises(ValueError, match="receipts changed during reporting"):
        domains.summarize()
    assert path.exists()
    assert not (tmp_path / "domain-report.json").exists()


@pytest.mark.parametrize("provenance", [None, {"schema": "changed"}])
def test_domain_report_does_not_resurrect_a_kill_before_an_unproven_later_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: dict[str, Any] | None
) -> None:
    path, rows = _report_fixture(tmp_path, monkeypatch)
    rows[1]["status"] = "KILLED"
    later = {**rows[1], "status": "INFRA_ERROR"}
    if provenance is None:
        later.pop("provenance")
    else:
        later["provenance"] = provenance
    path.write_text("".join(json.dumps(row) + "\n" for row in [*rows, later]))
    domains.summarize()
    summary = json.loads((tmp_path / "domain-report.json").read_text())
    assert summary["statuses"] == {"UNVERIFIED": 1}
    assert summary["incompatible_historical_attempts"] == 2


@pytest.mark.parametrize("timeout", [None, "15"])
def test_domain_cli_status_retry_uses_compact_verified_latest_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout: str | None
) -> None:
    manifest = _mutants()
    provenance: dict[str, Any] = {"schema": 2, "config": {"domain_selection": "test"}}
    path = tmp_path / "results.jsonl"
    path.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                _row("one", "INFRA_ERROR", provenance, manifest),
                _row("two", "SURVIVED", provenance, manifest),
            ]
        )
    )
    called: dict[str, Any] = {}

    class Prepared:
        def __init__(self, _workers: int, _maximum: None, *, phase_timeout: float | None):
            self.results_path, self.identity, self.manifest = path, provenance, manifest
            self.campaign_id = identity.digest_json(provenance)
            self.done_ids: set[str] = set()
            called["timeout"] = phase_timeout

        def run(self, todo: list[dict[str, Any]]) -> None:
            called["ids"] = [row["id"] for row in todo]

        def close(self) -> None:
            called["closed"] = True

    monkeypatch.setattr(domains, "DomainRunner", Prepared)
    monkeypatch.setenv("MUTATE_FULL_PHASE", "1")
    argv = ["mutate_domains.py", "run", "--status", "INFRA_ERROR", "--domains", "1"]
    if timeout is not None:
        argv += ["--timeout", timeout]
    monkeypatch.setattr(sys, "argv", argv)
    domains.main()
    assert called == {"timeout": 15.0 if timeout else None, "ids": ["one"], "closed": True}


def test_iter_results_preserves_the_valid_record_api(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text(
        'broken\n[]\n{"id":4,"status":"KILLED"}\n{"id":"a","status":"INFRA_ERROR"}\n{"id":"b","status":"SURVIVED"}'
    )
    expected = [{"id": "a", "status": "INFRA_ERROR"}, {"id": "b", "status": "SURVIVED"}]
    assert list(identity.iter_results(path)) == expected
    assert identity.read_results(path) == expected
