"""Mutation contracts run only in isolated tiny fixture projects, never app/ edits."""

import hashlib
import json
import shutil
import subprocess
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

REAL_LOAD_COVERAGE_CONTEXTS = mutate_run.load_coverage_contexts


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Skip the application's global database lifecycle for tiny projects.

    The timeout-cleanup regression owns its separately named disposable DB.
    """


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
        lambda *args: {
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
        # Feed actual producer records and its report into the final CI gate.
        # Schema-only fixtures cannot establish producer/consumer compatibility.
        plan_path, report_path, results_path = (
            tmp_path / name for name in ("plan.json", "report.json", "results.jsonl")
        )
        plan_path.write_text(json.dumps({"mutants": [row["id"] for row in results]}))
        report_path.write_text(
            json.dumps(
                mutate_report.summarize(
                    {row["id"]: row for row in mutants}, results, campaign_id=runner.campaign_id
                )
            )
        )
        results_path.write_text("".join(json.dumps(row) + "\n" for row in results))
        gate = subprocess.run(
            [
                sys.executable,
                str(MUTATION.parents[1] / ".github/scripts/check_mutation_report.py"),
                "--plan",
                str(plan_path),
                "--report",
                str(report_path),
                "--results",
                str(results_path),
                "--format",
                "backend",
                "--minimum",
                "80",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert gate.returncode == 0, gate.stdout + gate.stderr
        assert json.loads(gate.stdout)["raw_killed"] == 2
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


@pytest.mark.parametrize("artifact", ["coverage", "provenance"])
def test_campaign_uses_captured_coverage_during_live_artifact_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    import coverage

    backend, _ = _project(tmp_path, monkeypatch)
    coverage_path = backend / ".coverage-mut"
    provenance_path = backend / ".coverage-mut.provenance.json"
    maps = []
    for name in ("alpha", "beta"):
        data = coverage.CoverageData(basename=str(tmp_path / f"coverage-{name}"))
        data.set_context(f"tests/test_{name}.py::test_value|run")
        data.add_lines({str(backend / "app/alpha.py"): {2}})
        data.write()
        maps.append((tmp_path / f"coverage-{name}").read_bytes())
    coverage_path.write_bytes(maps[0])
    provenance = json.loads(provenance_path.read_text())
    provenance.update(coverage_sha256=sha_file(coverage_path), source_root=str(backend))
    provenance_path.write_text(json.dumps(provenance))
    original_provenance = provenance_path.read_bytes()

    def replace_during_loading(*args: Any) -> dict[str, dict[int, set[str]]]:
        if artifact == "coverage":
            coverage_path.write_bytes(maps[1])
        else:
            provenance_path.write_text(
                json.dumps({**provenance, "source_root": str(backend.parent)})
            )
        # The artifacts are live replaced, then restored before scoring. A
        # before/after hash check alone cannot establish which map was parsed.
        try:
            contexts: dict[str, dict[int, set[str]]] = REAL_LOAD_COVERAGE_CONTEXTS(*args)
            return contexts
        finally:
            coverage_path.write_bytes(maps[0])
            provenance_path.write_bytes(original_provenance)

    monkeypatch.setattr(mutate_run, "load_coverage_contexts", replace_during_loading)
    runner = mutate_run.Runner(1, None)
    try:
        assert runner.identity["coverage_sha256"] == sha_file(coverage_path)
        assert runner.ctx["app/alpha.py"][2] == {"tests/test_alpha.py::test_value"}
    finally:
        runner.close()


def test_campaign_rejects_mutant_absent_from_captured_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, mutants = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    replacement = {**mutants[0], "mut_stmt": "return 3"}
    (backend / "mutation/manifest.json").write_text(json.dumps([replacement, mutants[1]]))
    monkeypatch.setattr(runner, "run_pytest", lambda *args: ("pass", "", 0.01))
    try:
        record = runner.execute(replacement, 0)
        assert record["status"] == "INVALID"
        assert "captured manifest" in record["error"]
        assert "baseline" not in record
    finally:
        runner.close()


def test_coverage_publication_hashes_its_baseline_instead_of_live_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    backend, _ = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(mutate_cover, "BACKEND", backend)
    baseline_bytes = b"coverage from this passing baseline"
    other_bytes = b"coverage concurrently published by a different baseline"

    def passing_baseline(*args: Any, **kwargs: Any) -> SimpleNamespace:
        (kwargs["cwd"] / ".coverage-mut").write_bytes(baseline_bytes)
        return SimpleNamespace(returncode=0)

    original_replace = Path.replace

    def replace_then_concurrently_publish(path: Path, target: Any) -> Path:
        result = original_replace(path, target)
        if Path(target) == backend / ".coverage-mut":
            Path(target).write_bytes(other_bytes)
        return result

    monkeypatch.setattr(mutate_cover.subprocess, "run", passing_baseline)
    monkeypatch.setattr(Path, "replace", replace_then_concurrently_publish)
    monkeypatch.setattr(sys, "argv", ["mutate_cover.py"])
    mutate_cover.main()
    provenance = json.loads((backend / ".coverage-mut.provenance.json").read_text())
    assert provenance["coverage_sha256"] == hashlib.sha256(baseline_bytes).hexdigest()
    with pytest.raises(ValueError, match="stale"):
        mutate_run.Runner(1, None)


def test_report_never_scores_manifest_different_from_the_one_it_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    backend, mutants = _project(tmp_path, monkeypatch)
    manifest_path = backend / "mutation/manifest.json"
    replacement = json.dumps([mutants[0]]).encode()
    identity = {
        **input_identity(backend),
        "manifest_sha256": hashlib.sha256(replacement).hexdigest(),
        "coverage_sha256": sha_file(backend / ".coverage-mut"),
        "coverage_provenance_sha256": sha_file(backend / ".coverage-mut.provenance.json"),
    }
    record = {
        "id": mutants[0]["id"],
        "status": "KILLED",
        "selection_mode": "complete",
        "mutant_digest": digest_json(mutants[0]),
        "provenance": identity,
        "campaign_id": digest_json(identity),
    }
    (backend / "mutation/results.jsonl").write_text(json.dumps(record) + "\n")

    def replace_after_manifest_parsing(path: Path) -> dict[str, Any]:
        manifest_path.write_bytes(replacement)
        inputs: dict[str, Any] = input_identity(path)
        return inputs

    monkeypatch.setattr(mutate_report, "BACKEND", backend)
    monkeypatch.setattr(mutate_report, "MUTDIR", backend / "mutation")
    monkeypatch.setattr(mutate_report, "input_identity", replace_after_manifest_parsing)
    monkeypatch.setattr(sys, "argv", ["mutate_report.py", "--dry-run"])
    mutate_report.main()
    summary = json.loads(capsys.readouterr().out)
    assert summary["score"] is None
    assert summary["executed"] == 0


@pytest.mark.parametrize(
    "relative",
    ["alembic/env.py", "scripts/export_openapi.py", "alembic.ini", "pins/uv.txt", ".env.example"],
)
def test_coverage_rejects_changed_migration_and_script_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    backend, _ = _project(tmp_path, monkeypatch)
    target = backend / relative
    target.parent.mkdir(exist_ok=True)
    target.write_text("changed execution dependency\n")
    with pytest.raises(ValueError, match="stale"):
        mutate_run.Runner(1, None)


@pytest.mark.parametrize(
    "relative",
    [
        "shared/openapi.json",
        "docker/edge-entrypoint.sh",
        ".github/workflows/ci.yml",
        "Dockerfile",
        "README.md",
        "docs/deployment.md",
        "frontend/pnpm-lock.yaml",
    ],
)
def test_coverage_rejects_changed_repository_contract_and_deployment_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    backend, _ = _project(tmp_path, monkeypatch)
    target = backend.parent / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed repository dependency\n")
    with pytest.raises(ValueError, match="stale"):
        mutate_run.Runner(1, None)


def test_killed_pytest_attempt_reaps_its_real_disposable_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import asyncpg

    backend, _ = _project(tmp_path, monkeypatch)
    marker = backend / "attempt-database.txt"
    (backend / "tests/test_hang.py").write_text(
        "import asyncio, os, time\n"
        "from pathlib import Path\n"
        "import asyncpg\n"
        "def test_hang():\n"
        "    name = os.environ['GOATFARM_TEST_DB']\n"
        "    async def create():\n"
        "        c = await asyncpg.connect('postgresql://localhost:5432/postgres')\n"
        "        await c.execute(f'CREATE DATABASE \"{name}\"')\n"
        "        await c.close()\n"
        "    asyncio.run(create())\n"
        "    Path('attempt-database.txt').write_text(name)\n"
        "    time.sleep(60)\n"
    )
    runner = object.__new__(mutate_run.Runner)
    runner.run_id = "a" * 32
    runner.phase_timeout = 5
    runner.local = __import__("threading").local()
    runner.local.workspace = backend
    status, _, _ = runner.run_pytest(["tests/test_hang.py"], 0)
    assert marker.is_file(), "child must create a real database before the timeout"
    database = marker.read_text()

    async def remains() -> bool:
        connection = await asyncpg.connect("postgresql://localhost:5432/postgres")
        try:
            return bool(
                await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)
            )
        finally:
            await connection.close()

    try:
        assert status == "timeout"
        assert not asyncio.run(remains())
    finally:
        mutate_run.drop_attempt_database(database)


def test_attempt_database_cleanup_rejects_unowned_names() -> None:
    with pytest.raises(ValueError, match="namespace"):
        mutate_run.drop_attempt_database("goatfarm")


def test_attempt_database_cleanup_bounds_connect_drop_and_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    class Connection:
        async def execute(self, sql: str, **kwargs: int) -> None:
            assert sql == (
                'DROP DATABASE IF EXISTS "herdly_mut_aaaaaaaaaa_bbbbbbbb_test" WITH (FORCE)'
            )
            calls.append(("drop", kwargs["timeout"]))

        async def close(self, **kwargs: int) -> None:
            calls.append(("close", kwargs["timeout"]))

    async def connect(dsn: str, **kwargs: int) -> Connection:
        assert dsn == "postgresql://localhost:5432/postgres"
        calls.append(("connect", kwargs["timeout"]))
        return Connection()

    monkeypatch.setattr(mutate_run.asyncpg, "connect", connect)
    mutate_run.drop_attempt_database("herdly_mut_aaaaaaaaaa_bbbbbbbb_test")
    assert calls == [("connect", 5), ("drop", 45), ("close", 5)]


def test_cleanup_failure_cannot_credit_a_real_assertion_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, mutants = _project(tmp_path, monkeypatch)
    calls = 0

    def fail_mutant_cleanup(database: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError("controlled cleanup failure after assertion failure")

    monkeypatch.setattr(mutate_run, "drop_attempt_database", fail_mutant_cleanup)
    runner = mutate_run.Runner(1, None)
    try:
        record = runner.execute(mutants[0], 0)
        assert record["baseline"]["status"] == "pass"
        assert any(report.get("assertion") for report in record["pytest_receipt"]["reports"]), (
            "the mutant's actual pytest assertion must fail before cleanup"
        )
        assert record["status"] == "INFRA_ERROR"
        assert calls == 2
        assert "cleanup failed" in record["fail"]
    finally:
        runner.close()


def test_failed_process_launch_does_not_reuse_previous_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, _ = _project(tmp_path, monkeypatch)
    runner = mutate_run.Runner(1, None)
    runner.local.workspace = backend
    runner.local.pytest_receipt = {"exit_code": 1, "collected": 42}

    def fail_launch(*args: Any, **kwargs: Any) -> None:
        raise OSError("controlled process launch failure")

    monkeypatch.setattr(mutate_run.subprocess, "Popen", fail_launch)
    try:
        status, _, _ = runner.run_pytest(["tests/test_alpha.py"], 0)
        assert status == "infra"
        assert runner.local.pytest_receipt is None
    finally:
        runner.close()


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


@pytest.mark.parametrize("driver", [mutate_run, mutate_report, mutate_final_pass])
def test_preview_requires_generated_inputs_without_writes_or_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, driver: Any
) -> None:
    backend, _mutants = _project(tmp_path, monkeypatch)
    (backend / "mutation/manifest.json").unlink()
    monkeypatch.setattr(driver, "MUTDIR", backend / "mutation")
    monkeypatch.setattr(
        mutate_run.Runner, "__init__", lambda *args, **kwargs: pytest.fail("must not spawn")
    )
    monkeypatch.setattr(sys, "argv", ["mutation-driver", "--dry-run"])
    before = {path: path.read_bytes() for path in backend.rglob("*") if path.is_file()}
    with pytest.raises(
        SystemExit, match=r"Missing manifest\.json; run python mutation/mutate_gen\.py"
    ):
        driver.main()
    assert before == {path: path.read_bytes() for path in backend.rglob("*") if path.is_file()}


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
    monkeypatch.delenv("MUTATE_FULL_PHASE")
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
    assert loaded.load_coverage_contexts(
        (backend / ".coverage-mut").read_bytes(), json.loads(provenance.read_text())
    ) == {"app/alpha.py": {2: {"tests/test_alpha.py::test_value"}}}
