"""Mutation contracts run only in isolated tiny fixture projects, never app/ edits."""

import hashlib
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

MUTATION = Path(__file__).resolve().parents[1] / "mutation"
sys.path.insert(0, str(MUTATION))
import mutate_cover  # type: ignore[import-not-found]  # noqa: E402
import mutate_final_pass  # type: ignore[import-not-found]  # noqa: E402
import mutate_gen  # type: ignore[import-not-found]  # noqa: E402
import mutate_report  # type: ignore[import-not-found]  # noqa: E402
import mutate_run  # type: ignore[import-not-found]  # noqa: E402
from mutate_identity import (  # type: ignore[import-not-found]  # noqa: E402
    digest_json,
    input_identity,
    latest_compatible,
    sha_file,
)
from mutate_verify_extremes import current_attempts  # type: ignore[import-not-found]  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """The fixture projects need no PostgreSQL or application lifecycle."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """No global integration cleanup in these harness-only checks."""


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[dict[str, Any]]]:
    backend = tmp_path / "project" / "backend"
    (backend / "app").mkdir(parents=True)
    (backend / "tests").mkdir()
    (backend / "mutation").mkdir()
    (backend / "app/__init__.py").write_text("")
    mutants = []
    for name in ("alpha", "beta"):
        path = backend / f"app/{name}.py"
        path.write_text("def value():\n    return 1\n")
        other = "beta" if name == "alpha" else "alpha"
        (backend / f"tests/test_{name}.py").write_text(
            f"from app import {name}, {other}\n"
            f"def test_value():\n    assert {other}.value() == 1\n    assert {name}.value() == 1\n"
        )
        mutants.append(
            {
                "id": name,
                "file": f"app/{name}.py",
                "tier": 1,
                "scope": "function",
                "line": 2,
                "kind": "intconst",
                "detail": "+1",
                "stmt_line": 2,
                "stmt_end_line": 2,
                "stmt_col": 4,
                "orig_stmt": "return 1",
                "orig_span": "    return 1\n",
                "mut_stmt": "return 2",
                "source_sha256": sha_file(path),
            }
        )
    (backend / "mutation/manifest.json").write_text(json.dumps(mutants))
    shutil.copyfile(MUTATION / "pytest_receipt.py", backend / "mutation/pytest_receipt.py")
    (backend / ".coverage-mut").write_bytes(b"fixture coverage: parsed by fixture contexts")
    (backend / ".coverage-mut.provenance.json").write_text(
        json.dumps(
            {
                "inputs": input_identity(backend),
                "coverage_sha256": sha_file(backend / ".coverage-mut"),
                "baseline_exit_code": 0,
            }
        )
    )
    monkeypatch.setattr(mutate_run, "BACKEND", backend)
    monkeypatch.setattr(mutate_run, "MUTDIR", backend / "mutation")
    monkeypatch.setattr(mutate_run, "VENV_PY", Path(sys.executable))
    monkeypatch.setattr(
        mutate_run,
        "load_coverage_contexts",
        lambda: {
            f"app/{name}.py": {2: {f"tests/test_{name}.py::test_value"}}
            for name in ("alpha", "beta")
        },
    )
    return backend, mutants


def test_two_concurrent_mutants_have_clean_baselines_and_exclusive_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(2, None)
    originals = {m["file"]: (backend / m["file"]).read_bytes() for m in mutants}
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda pair: runner.execute(pair[1], pair[0]), enumerate(mutants))
            )
        assert [record["status"] for record in results] == ["KILLED", "KILLED"]
        assert all(record["baseline"]["status"] == "pass" for record in results)
        assert len({record["attempt_id"] for record in results}) == 2
        assert all((backend / path).read_bytes() == content for path, content in originals.items())
        assert all(record["selection_mode"] == "complete" for record in results)
        assert all(record["pytest_receipt"]["reports"] for record in results)
    finally:
        runner.close()


@pytest.mark.parametrize(
    "mutant_change", [{"source_sha256": "stale"}, {"orig_span": "wrong"}, {"mut_stmt": "return ("}]
)
def test_invalid_or_stale_mutant_never_spawns_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutant_change: dict[str, Any]
) -> None:
    _, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    monkeypatch.setattr(runner, "run_pytest", lambda *args: pytest.fail("pytest must not run"))
    try:
        record = runner.execute({**mutants[0], **mutant_change}, 0)
        assert record["status"] == "INVALID"
    finally:
        runner.close()


def test_coverage_cannot_resume_after_test_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _ = _project(tmp_path, monkeypatch)
    (backend / "tests/test_alpha.py").write_text("def test_changed():\n    assert True\n")
    with pytest.raises(ValueError, match="stale"):
        mutate_run.Runner(1, None)


@pytest.mark.parametrize(
    ("code", "reports", "expected"),
    [
        (0, [{"when": "call", "outcome": "passed"}], "pass"),
        (1, [{"when": "call", "outcome": "failed", "assertion": True}], "kill"),
        (1, [{"when": "setup", "outcome": "failed", "assertion": True}], "infra"),
        (1, [{"when": "call", "outcome": "failed", "assertion": False}], "infra"),
        (2, [], "infra"),
        (3, [], "infra"),
        (4, [], "infra"),
        (5, [], "infra"),
    ],
)
def test_structured_pytest_exit_classification(
    code: int, reports: list[dict[str, Any]], expected: str
) -> None:
    receipt = {"exit_code": code, "collected": 1, "collection_errors": [], "reports": reports}
    assert mutate_run.classify_pytest(code, receipt) == expected
    assert mutate_run.classify_pytest(code, None) == "infra"


@pytest.mark.parametrize("failure", ["infra", "timeout"])
def test_bad_baseline_cannot_be_credited_as_mutant_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    _, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    monkeypatch.setattr(
        runner, "run_pytest", lambda *args: (failure, "fixture infrastructure", 0.01)
    )
    try:
        record = runner.execute(mutants[0], 0)
        assert record["status"] == (
            "INCONCLUSIVE_TIMEOUT" if failure == "timeout" else "INFRA_ERROR"
        )
        assert record["baseline"]["status"] != "pass"
    finally:
        runner.close()


def test_timeout_after_passing_baseline_remains_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    calls = iter([("pass", "", 0.01), ("timeout", "", 0.01)])
    monkeypatch.setattr(runner, "run_pytest", lambda *args: next(calls))
    try:
        assert runner.execute(mutants[0], 0)["status"] == "INCONCLUSIVE_TIMEOUT"
    finally:
        runner.close()


def test_latest_compatible_resume_retries_errors_and_ignores_old_id_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    identity = runner.campaign_id
    runner.close()
    records = [
        {
            "id": "alpha",
            "campaign_id": identity,
            "mutant_digest": digest_json(mutants[0]),
            "status": "KILLED",
        },
        {
            "id": "alpha",
            "campaign_id": identity,
            "mutant_digest": digest_json(mutants[0]),
            "status": "INFRA_ERROR",
        },
        {
            "id": "beta",
            "campaign_id": "old-campaign",
            "mutant_digest": digest_json(mutants[1]),
            "status": "KILLED",
        },
    ]
    (backend / "mutation/results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    fresh = mutate_run.Runner(1, None)
    try:
        assert fresh.done_ids == set()
        folded = latest_compatible(records, identity, {m["id"]: m for m in mutants})
        assert folded["alpha"]["status"] == "INFRA_ERROR"
    finally:
        fresh.close()


def test_report_folds_latest_and_excludes_timeout_invalid_and_sampled_scores() -> None:
    mutants = {str(i): {"id": str(i)} for i in range(4)}
    records = []
    for mid, status, mode in [
        ("0", "KILLED", "complete"),
        ("0", "SURVIVED", "complete"),
        ("1", "INCONCLUSIVE_TIMEOUT", "complete"),
        ("2", "INVALID", "complete"),
        ("3", "KILLED", "sampled"),
    ]:
        records.append(
            {
                "id": mid,
                "campaign_id": "fresh",
                "mutant_digest": digest_json(mutants[mid]),
                "status": status,
                "selection_mode": mode,
            }
        )
    summary = mutate_report.summarize(mutants, records, campaign_id="fresh")
    assert summary["executed"] == 4
    assert summary["complete_measured"] == 1
    assert summary["score"] == 0


def test_final_pass_dry_run_writes_nothing_and_spawns_no_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _mutants = _project(tmp_path, monkeypatch)
    results = backend / "mutation/results.jsonl"
    results.write_text(json.dumps({"id": "alpha", "status": "SURVIVED"}) + "\n")
    before = {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in backend.rglob("*") if p.is_file()
    }
    monkeypatch.setattr(mutate_final_pass, "MUTDIR", backend / "mutation")
    monkeypatch.setattr(
        mutate_run.Runner, "__init__", lambda *args, **kwargs: pytest.fail("must not spawn")
    )
    monkeypatch.setattr(sys, "argv", ["mutate_final_pass.py", "--dry-run"])
    mutate_final_pass.main()
    assert before == {
        p: hashlib.sha256(p.read_bytes()).hexdigest() for p in backend.rglob("*") if p.is_file()
    }


def test_extremes_folds_only_current_targets_and_preserves_timeout_status() -> None:
    records = [
        {"id": "a", "run_id": "old", "campaign_id": "fresh", "status": "TIMEOUT"},
        {"id": "b", "run_id": "current", "campaign_id": "fresh", "status": "KILLED"},
        {"id": "a", "run_id": "current", "campaign_id": "fresh", "status": "INCONCLUSIVE_TIMEOUT"},
    ]
    current = current_attempts(records, run_id="current", target_ids={"a"}, campaign_id="fresh")
    assert list(current) == ["a"]
    assert current["a"]["status"] == "INCONCLUSIVE_TIMEOUT"


def test_generator_validates_returns_awaits_and_yields_in_full_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = tmp_path / "backend"
    (backend / "app").mkdir(parents=True)
    (backend / "mutation").mkdir()
    path = backend / "app/example.py"
    path.write_text(
        "def answer():\n    return 1 + 2\n\n"
        "async def async_answer():\n    await work(7)\n    return 3 + 4\n\n"
        "def sequence():\n    yield 5 + 6\n"
    )
    monkeypatch.setattr(mutate_gen, "BACKEND", backend)
    monkeypatch.setattr(mutate_gen, "target_files", lambda: [path])
    mutate_gen.generate()
    manifest = json.loads((backend / "mutation/manifest.json").read_text())
    assert any(m["orig_stmt"].startswith("return") for m in manifest)
    assert any(m["orig_stmt"].startswith("yield") for m in manifest)
    assert any(m["orig_stmt"].startswith("await") for m in manifest)
    assert all(m["source_sha256"] == sha_file(path) for m in manifest)
    assert all(m["orig_span"] for m in manifest)


def test_full_mode_runs_every_coverer_without_hidden_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, mutants = _project(tmp_path, monkeypatch)
    monkeypatch.setenv("MUTATE_FULL_PHASE", "1")
    runner = mutate_run.Runner(1, None)
    selection = [f"tests/test_alpha.py::case_{i}" for i in range(1001)]
    called: list[list[str]] = []
    monkeypatch.setattr(runner, "select_tests", lambda *args: (selection, False))

    def passed(nodes: list[str], worker: int) -> tuple[str, str, float]:
        called.append(nodes)
        return "pass", "", 0.01

    monkeypatch.setattr(runner, "run_pytest", passed)
    try:
        record = runner.execute(mutants[0], 0)
        assert record["selection_mode"] == "complete"
        assert record["selection"] == selection
        assert called == [selection, selection]
    finally:
        runner.close()


def test_failed_coverage_baseline_preserves_existing_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    backend, _ = _project(tmp_path, monkeypatch)
    artifacts = [backend / ".coverage-mut", backend / ".coverage-mut.provenance.json"]
    before = [path.read_bytes() for path in artifacts]
    monkeypatch.setattr(mutate_cover, "BACKEND", backend)
    monkeypatch.setattr(
        mutate_cover.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1)
    )
    monkeypatch.setattr(sys, "argv", ["mutate_cover.py"])
    with pytest.raises(SystemExit, match="no coverage published"):
        mutate_cover.main()
    assert [path.read_bytes() for path in artifacts] == before


def test_isolated_coverage_paths_rebase_to_current_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coverage

    backend, _ = _project(tmp_path, monkeypatch)
    (backend / ".coverage-mut").unlink()
    data = coverage.CoverageData(basename=str(backend / ".coverage-mut"))
    data.set_context("tests/test_alpha.py::test_value|run")
    data.add_lines({"/fixture/deleted-snapshot/backend/app/alpha.py": {2}})
    data.write()
    provenance = backend / ".coverage-mut.provenance.json"
    provenance.write_text(json.dumps({"source_root": "/fixture/deleted-snapshot/backend"}))
    # Call the original implementation instead of _project's context shim.
    module = __import__("importlib.util", fromlist=["spec_from_file_location"])
    spec = module.spec_from_file_location("real_mutate_run", MUTATION / "mutate_run.py")
    loaded = module.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    monkeypatch.setattr(loaded, "BACKEND", backend)
    assert loaded.load_coverage_contexts() == {
        "app/alpha.py": {2: {"tests/test_alpha.py::test_value"}}
    }
