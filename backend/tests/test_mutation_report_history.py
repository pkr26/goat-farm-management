"""Generic diagnostic reports fold metadata without retaining full receipts."""

import importlib
import json
import sys
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mutation"))
identity = importlib.import_module("mutate_identity")
report = importlib.import_module("mutate_report")


def test_summary_accepts_one_pass_history_and_keeps_only_latest_metadata(tmp_path: Path) -> None:
    manifest = {key: {"id": key} for key in ("one", "two", "three")}
    path = tmp_path / "results.jsonl"

    def row(mid: str, status: str, mode: str) -> dict[str, Any]:
        return {
            "id": mid,
            "status": status,
            "selection_mode": mode,
            "campaign_id": "fresh",
            "mutant_digest": identity.digest_json(manifest[mid]),
        }

    padding = {**row("one", "KILLED", "complete"), "pytest_output": "x" * 262144}
    with path.open("w") as stream:
        for _ in range(48):
            stream.write(json.dumps(padding) + "\n")
        for item in [
            row("one", "SURVIVED", "complete"),
            row("two", "INCONCLUSIVE_TIMEOUT", "complete"),
            row("three", "KILLED", "sampled"),
        ]:
            stream.write(json.dumps(item) + "\n")
    tracemalloc.start()
    try:
        summary = report.summarize(manifest, identity.iter_results(path), campaign_id="fresh")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024
    assert summary["statuses"] == {"SURVIVED": 1, "INCONCLUSIVE_TIMEOUT": 1, "KILLED": 1}
    assert summary["complete_measured"] == 1
    assert summary["sampled"] == 1
    assert summary["score"] == 0


@pytest.mark.parametrize("explicit", [False, True])
def test_main_streams_and_preserves_last_row_or_explicit_campaign_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    explicit: bool,
) -> None:
    mutant = {"id": "one"}
    raw = json.dumps([mutant]).encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    path = tmp_path / "results.jsonl"
    inputs: dict[str, Any] = {"sources": {"app/demo.py": "source"}, "tests": {}}
    provenance = {
        **inputs,
        "manifest_sha256": identity.sha_bytes(raw),
        "coverage_sha256": "coverage",
        "coverage_provenance_sha256": "coverage-provenance",
        "config": {"workers": 1},
    }
    other = {**provenance, "config": {"workers": 2}}

    def row(status: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": "one",
            "status": status,
            "selection_mode": "complete",
            "campaign_id": identity.digest_json(context),
            "provenance": context,
            "mutant_digest": identity.digest_json(mutant),
        }

    path.write_text(
        "".join(
            json.dumps(item) + "\n"
            for item in [
                row("KILLED", provenance),
                row("SURVIVED", other),
                row("INFRA_ERROR", provenance),
                {"id": "old", "status": "KILLED"},
            ]
        )
    )
    monkeypatch.setattr(report, "MUTDIR", tmp_path)
    monkeypatch.setattr(report, "input_identity", lambda _: inputs)
    monkeypatch.setattr(
        report,
        "sha_file",
        lambda p: "coverage" if p.name == ".coverage-mut" else "coverage-provenance",
    )
    original = Path.read_text

    def refuse_eager(self: Path, *args: Any, **kwargs: Any) -> str:
        assert self != path, "result histories must be streamed"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse_eager)
    argv = ["mutate_report.py", "--dry-run"]
    if explicit:
        argv += ["--campaign-id", identity.digest_json(other)]
    monkeypatch.setattr(sys, "argv", argv)
    report.main()
    summary = json.loads(capsys.readouterr().out)
    assert summary["campaign_id"] == identity.digest_json(other if explicit else provenance)
    assert summary["statuses"] == ({"SURVIVED": 1} if explicit else {"INFRA_ERROR": 1})
    assert summary["untrusted_or_other_campaign_attempts"] == (3 if explicit else 2)
    assert not (tmp_path / "report.md").exists()


@pytest.mark.parametrize("unproven", [None, {"schema": "changed"}])
def test_report_never_resurrects_a_prior_kill_after_an_unproven_current_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unproven: dict[str, Any] | None,
) -> None:
    mutant = {"id": "one"}
    raw = json.dumps([mutant]).encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    inputs: dict[str, Any] = {"sources": {}, "tests": {}}
    provenance = {
        **inputs,
        "manifest_sha256": identity.sha_bytes(raw),
        "coverage_sha256": "coverage",
        "coverage_provenance_sha256": "provenance",
    }
    claimed = {
        "id": "one",
        "status": "KILLED",
        "selection_mode": "complete",
        "campaign_id": identity.digest_json(provenance),
        "provenance": provenance,
        "mutant_digest": identity.digest_json(mutant),
    }
    later = {**claimed, "status": "INFRA_ERROR"}
    if unproven is None:
        later.pop("provenance")
    else:
        later["provenance"] = unproven
    (tmp_path / "results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in [claimed, later])
    )
    monkeypatch.setattr(report, "MUTDIR", tmp_path)
    monkeypatch.setattr(report, "input_identity", lambda _: inputs)
    monkeypatch.setattr(
        report, "sha_file", lambda p: "coverage" if p.name == ".coverage-mut" else "provenance"
    )
    monkeypatch.setattr(sys, "argv", ["mutate_report.py", "--dry-run"])
    report.main()
    summary = json.loads(capsys.readouterr().out)
    assert summary["statuses"] == {"UNVERIFIED": 1}
    assert summary["complete_measured"] == 0
    assert summary["score"] is None
    assert summary["untrusted_or_other_campaign_attempts"] == 1
