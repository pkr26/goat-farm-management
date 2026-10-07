"""Business-campaign manifests preserve compilable, isolated mutation targets."""

import ast
import copy
import importlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

MUTATION = Path(__file__).resolve().parents[1] / "mutation"
sys.path.insert(0, str(MUTATION))
# CI type-checks these standalone scripts separately from the tests.
mutate_cover = importlib.import_module("mutate_cover")
mutate_domains = importlib.import_module("mutate_domains")
mutate_gen = importlib.import_module("mutate_gen")
mutate_run = importlib.import_module("mutate_run")


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Pure harness contracts have no application database lifecycle."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Pure harness contracts do not truncate application tables."""


def test_all_38_campaigns_have_real_declared_test_suites() -> None:
    assert len(mutate_domains.NAMES) == 38
    assert set(mutate_domains.TESTS) == set(range(1, 39))
    for domain in range(1, 39):
        paths = mutate_domains.suite(domain)
        assert paths
        assert all((mutate_domains.BACKEND / path).is_file() for path in paths)


def test_snapshot_keeps_harness_and_semantic_sources_without_recursive_attempt_logs() -> None:
    names = [
        "mutate_run.py",
        "domain_specs.json",
        "domain_ownership.json",
        "manifest.json",
        "results.jsonl",
        "domain-baseline.log",
        "campaigns",
    ]
    assert mutate_run.snapshot_ignore("/repo/backend/mutation", names) == {
        "manifest.json",
        "results.jsonl",
        "domain-baseline.log",
        "campaigns",
    }


def test_migration_targets_are_confined_to_private_revision_files(tmp_path: Path) -> None:
    assert mutate_run.allowed_mutation_target(tmp_path / "app/services/animals.py", tmp_path)
    assert mutate_run.allowed_mutation_target(tmp_path / "alembic/versions/example.py", tmp_path)
    assert not mutate_run.allowed_mutation_target(tmp_path / "alembic/env.py", tmp_path)
    assert not mutate_run.allowed_mutation_target(tmp_path / "tests/test_example.py", tmp_path)
    assert not mutate_run.allowed_mutation_target(tmp_path / "app/../../outside.py", tmp_path)


def test_semantic_multiline_edit_preserves_enclosing_async_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        "async def work():\n"
        "    for item in range(2):\n"
        "        if item == 1:\n"
        "            return item\n"
        "    return -1\n"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app/example.py").write_text(source)
    monkeypatch.setattr(mutate_domains, "BACKEND", tmp_path)
    mutant = mutate_domains.semantic_mutant(
        {
            "domain": 17,
            "file": "app/example.py",
            "original": "if item == 1:",
            "replacement": "if item == 0:",
            "detail": "incorrect terminal item",
            "tests": [],
        }
    )
    runner = object.__new__(mutate_run.Runner)
    runner.local = threading.local()
    runner.local.workspace = tmp_path
    mutated = runner.apply_mutant(mutant)
    compile(mutated, "app/example.py", "exec")
    assert "async def work()" in mutated
    assert "if item == 0:" in mutated
    assert (tmp_path / "app/example.py").read_text() == source
    assert mutant["scope"] == "function"


def test_semantic_edit_rejects_ambiguous_snippet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/example.py").write_text("x = 1\ny = 1\n")
    monkeypatch.setattr(mutate_domains, "BACKEND", tmp_path)
    with pytest.raises(ValueError, match="not unique"):
        mutate_domains.semantic_mutant(
            {
                "domain": 1,
                "file": "app/example.py",
                "original": "= 1",
                "replacement": "= 2",
                "detail": "ambiguous assignment",
                "tests": [],
            }
        )


def test_campaign_classification_owns_a_nested_function_by_its_innermost_scope() -> None:
    tree = ast.parse("def outer():\n    def inner():\n        return 1\n    return inner()\n")
    assert mutate_domains.symbol(tree, 3) == "inner"
    assert mutate_domains.classify("app/services/health_rounds.py", "complete", 1) == 10
    assert mutate_domains.classify("app/services/finance.py", "lifetime_pnl", 1) == 16
    assert mutate_domains.classify("app/services/finance.py", "renew_insurance", 1) == 15


def test_registration_oracles_follow_outer_scope_without_claiming_other_mutations() -> None:
    tree = ast.parse(
        "async def create_animal():\n"
        "    async def mutate():\n"
        "        return 1\n"
        "    return await mutate()\n"
        "async def record_weight():\n"
        "    async def mutate():\n"
        "        return 2\n"
        "    return await mutate()\n"
    )
    registration = mutate_domains.contract_oracles("app/api/animals.py", tree, 3)
    assert "tests/test_mutation38_livestock_purchase_move_audit.py" in registration
    assert "tests/test_mutation38_livestock_registration_idempotency.py" in registration
    weight_oracles = mutate_domains.contract_oracles("app/api/animals.py", tree, 7)
    assert "tests/test_mutation38_livestock_purchase_move_audit.py" not in weight_oracles
    assert "tests/test_mutation38_livestock_registration_idempotency.py" not in weight_oracles
    assert mutate_domains.contract_oracles("app/api/other.py", tree, 3) == []


def test_profile_cap_oracles_follow_named_assignments_without_fixed_source_lines() -> None:
    tree = ast.parse(
        "unrelated = 1\nPROFILE_HISTORY_MAX_LIMIT = 100\nPROFILE_HISTORY_DEFAULT_LIMIT: int = 25\n"
    )
    expected = [
        "tests/test_animal_profile_pagination.py::test_profile_histories_have_exact_totals_independent_pages_and_constant_queries"
    ]
    assert mutate_domains.contract_oracles("app/api/animals.py", tree, 2) == expected
    assert mutate_domains.contract_oracles("app/api/animals.py", tree, 3) == expected
    assert mutate_domains.contract_oracles("app/api/animals.py", tree, 1) == []


def test_independent_baselines_are_concurrent_and_same_selection_is_coalesced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = object.__new__(mutate_run.Runner)
    runner.local = threading.local()
    runner.baseline_lock = threading.Lock()
    runner.baseline_in_flight = {}
    runner.baselines = {}
    runner.baseline_receipts = {}
    started = threading.Barrier(2)
    calls: list[str] = []

    def run(selection: list[str], worker: int) -> tuple[str, str, float]:
        calls.append(selection[0])
        started.wait(timeout=5)
        runner.local.pytest_receipt = {"selection": selection, "worker": worker}
        return "pass", selection[0], 0.0

    monkeypatch.setattr(runner, "run_pytest", run)
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [
            pool.submit(runner.clean_baseline, "a", ["test_a"], 0),
            pool.submit(runner.clean_baseline, "a", ["test_a"], 1),
            pool.submit(runner.clean_baseline, "b", ["test_b"], 2),
        ]
        results = [job.result(timeout=10) for job in jobs]
    assert [result[1] for result in results] == ["test_a", "test_a", "test_b"]
    assert sorted(calls) == ["test_a", "test_b"]
    receipt_a = runner.baseline_receipts["a"]
    receipt_b = runner.baseline_receipts["b"]
    assert receipt_a is not None and receipt_a["selection"] == ["test_a"]
    assert receipt_b is not None and receipt_b["selection"] == ["test_b"]


@pytest.mark.parametrize("fail_second", [False, True])
def test_parallel_coverage_publishes_only_after_every_group_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_second: bool
) -> None:
    backend = tmp_path / "project/backend"
    for directory in ("app", "tests", "mutation"):
        (backend / directory).mkdir(parents=True)
    (backend / "app/__init__.py").write_text("")
    (backend / "app/example.py").write_text(
        "def value_0():\n    return 42\n\ndef value_1():\n    return 42\n"
    )
    (backend / "pyproject.toml").write_text("[tool.coverage.report]\nfail_under = 100\n")
    # The isolated tiny project must include the collector used by the real
    # sharded coverage command, just as it includes app and test modules.
    (backend / "mutation/coverage_inventory.py").write_bytes(
        (MUTATION / "coverage_inventory.py").read_bytes()
    )
    for index in range(2):
        expected = 41 if fail_second and index == 1 else 42
        (backend / f"tests/test_group_{index}.py").write_text(
            f"from app.example import value_{index}\n"
            f"def test_value():\n    assert value_{index}() == {expected}\n"
        )
    monkeypatch.setattr(mutate_cover, "BACKEND", backend)
    monkeypatch.setattr(mutate_cover, "VENV_PY", Path(sys.executable))
    monkeypatch.setattr(sys, "argv", ["mutate_cover.py", "--workers", "2"])
    if fail_second:
        (backend / ".coverage-mut").write_bytes(b"previous passing evidence")
        with pytest.raises(SystemExit, match="clean coverage baseline failed"):
            mutate_cover.main()
        assert (backend / ".coverage-mut").read_bytes() == b"previous passing evidence"
        assert not (backend / ".coverage-mut.provenance.json").exists()
    else:
        mutate_cover.main()
        import coverage

        data = coverage.CoverageData(basename=str(backend / ".coverage-mut"))
        data.read()
        assert any("test_group_0" in context for context in data.measured_contexts())
        assert any("test_group_1" in context for context in data.measured_contexts())
        provenance = json.loads((backend / ".coverage-mut.provenance.json").read_text())
        assert provenance["test_database_admin_sha256"] == mutate_run.database_admin_sha256()


def test_elif_comparison_changes_one_branch_without_detaching_its_chain() -> None:
    source = (
        "def choose(anchor, value):\n"
        "    if anchor:\n        return 'anchor'\n"
        "    elif value > 0:\n        return 'positive'\n"
        "    else:\n        return 'fallback'\n"
    )
    tree = ast.parse(source)
    parents = mutate_gen.build_parent_map(tree)
    comparison = next(n for n in ast.walk(tree) if isinstance(n, ast.Compare))
    statement = mutate_gen.enclosing_statement(comparison, parents)
    assert isinstance(statement, ast.If)
    assert statement.lineno == 2
    replacement = copy.deepcopy(statement)
    changed = next(n for n in ast.walk(replacement) if isinstance(n, ast.Compare))
    changed.ops[0] = ast.GtE()
    edited = mutate_gen.splice(source, statement, ast.unparse(replacement))
    namespace: dict[str, object] = {}
    exec(edited, namespace)
    choose = namespace["choose"]
    assert callable(choose)
    assert choose(True, 0) == "anchor"
    assert choose(False, 0) == "positive"
    assert choose(False, -1) == "fallback"


def test_real_nested_if_in_else_remains_an_independent_statement() -> None:
    tree = ast.parse("if anchor:\n    pass\nelse:\n    if value > 0:\n        pass\n")
    comparison = next(n for n in ast.walk(tree) if isinstance(n, ast.Compare))
    statement = mutate_gen.enclosing_statement(comparison, mutate_gen.build_parent_map(tree))
    assert isinstance(statement, ast.If)
    assert statement.lineno == 4


def test_identical_constants_in_one_statement_are_distinct_mutation_sites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "mutation").mkdir()
    source = "def values():\n    return [0, 0, 0]\n"
    path = tmp_path / "app/example.py"
    path.write_text(source)
    monkeypatch.setattr(mutate_gen, "BACKEND", tmp_path)
    monkeypatch.setattr(mutate_gen, "target_files", lambda: [path])
    mutate_gen.generate()
    mutants = json.loads((tmp_path / "mutation/manifest.json").read_text())
    assert len(mutants) == 3
    assert len({m["id"] for m in mutants}) == 3
    outcomes = []
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    statement = function.body[0]
    for mutant in mutants:
        namespace: dict[str, object] = {}
        exec(mutate_gen.splice(source, statement, mutant["mut_stmt"]), namespace)
        values = namespace["values"]
        assert callable(values)
        outcomes.append(values())
    assert outcomes == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def test_domain_selection_keeps_every_coverer_and_runs_explicit_oracle_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_z_progress.py").write_text("")
    oracle = "tests/test_z_progress.py::test_complete"
    coverers = {f"tests/test_existing.py::test_case_{i}" for i in range(65)}
    runner = object.__new__(mutate_domains.DomainRunner)
    runner.ctx = {"app/example.py": {12: coverers}}
    monkeypatch.setattr(mutate_domains, "BACKEND", tmp_path)
    selected, fallback = runner.select_tests(
        {
            "file": "app/example.py",
            "line": 12,
            "scope": "function",
            "domain": 28,
            "oracles": [oracle],
        }
    )
    assert selected[0] == oracle
    assert set(selected) == coverers | {oracle}
    assert len(selected) == 66
    assert not fallback


def test_default_parameter_mutant_applies_its_decorator_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "mutation").mkdir()
    source = (
        "decorations = 0\n"
        "def register(fn):\n    global decorations\n    decorations += 1\n    return fn\n"
        "@register\ndef value(n=0):\n    return n\n"
    )
    path = tmp_path / "app/example.py"
    path.write_text(source)
    monkeypatch.setattr(mutate_gen, "BACKEND", tmp_path)
    monkeypatch.setattr(mutate_gen, "target_files", lambda: [path])
    mutate_gen.generate()
    mutants = json.loads((tmp_path / "mutation/manifest.json").read_text())
    mutant = next(m for m in mutants if m["line"] == 7 and m["kind"] == "intconst")
    assert mutant["stmt_line"] == 6
    assert mutant["orig_span"].startswith("@register\n")
    function = ast.parse(source).body[-1]
    assert isinstance(function, ast.FunctionDef)
    namespace: dict[str, object] = {}
    exec(mutate_gen.splice(source, function, mutant["mut_stmt"]), namespace)
    value = namespace["value"]
    assert callable(value)
    assert namespace["decorations"] == 1
    assert value() == 1


def test_semantic_default_parameter_edit_retains_its_single_decorator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    source = (
        "decorations = 0\n"
        "def register(fn):\n    global decorations\n    decorations += 1\n    return fn\n"
        "@register\ndef value(n=0):\n    return n\n"
    )
    path = tmp_path / "app/example.py"
    path.write_text(source)
    monkeypatch.setattr(mutate_domains, "BACKEND", tmp_path)
    mutant = mutate_domains.semantic_mutant(
        {
            "domain": 1,
            "file": "app/example.py",
            "original": "def value(n=0):",
            "replacement": "def value(n=1):",
            "detail": "default parameter boundary",
        }
    )
    assert mutant["stmt_line"] == 6
    assert mutant["orig_span"].startswith("@register\n")
    function = ast.parse(source).body[-1]
    assert isinstance(function, ast.FunctionDef)
    namespace: dict[str, object] = {}
    exec(mutate_gen.splice(source, function, mutant["mut_stmt"]), namespace)
    value = namespace["value"]
    assert callable(value)
    assert namespace["decorations"] == 1
    assert value() == 1


def test_coverage_contexts_accept_a_directory_alias_for_the_same_measured_source(
    tmp_path: Path,
) -> None:
    import coverage

    root = tmp_path / "actual"
    (root / "app").mkdir(parents=True)
    measured = root / "app/example.py"
    measured.write_text("answer = 42\n")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    path = tmp_path / ".coverage"
    data = coverage.CoverageData(basename=str(path))
    data.set_context("tests/test_example.py::test_answer|run")
    data.add_lines({str(measured): {1}})
    data.write()
    contexts = mutate_run.load_coverage_contexts(path.read_bytes(), {"source_root": str(alias)})
    assert contexts == {"app/example.py": {1: {"tests/test_example.py::test_answer"}}}


def test_lazy_type_aliases_have_no_executable_mutation_sites() -> None:
    tree = ast.parse("type Amount = int | float | None\ndef value(n=0):\n    return n\n")
    sites = mutate_gen.collect_sites(tree, mutate_gen.excluded_node_ids(tree))
    assert len(sites) == 1
    assert sites[0].kind == "intconst"


@pytest.mark.parametrize(
    "oracle",
    [
        "tests/test_progress.py::test_complete",
        "tests/test_progress.py::test_complete[second]",
    ],
)
def test_receipt_plugin_runs_overlapping_explicit_oracles_first_without_losing_coverers(
    tmp_path: Path,
    oracle: str,
) -> None:
    import os
    import shutil
    import subprocess

    (tmp_path / "tests").mkdir()
    (tmp_path / "mutation").mkdir()
    shutil.copyfile(MUTATION / "pytest_receipt.py", tmp_path / "mutation/pytest_receipt.py")
    (tmp_path / "tests/test_progress.py").write_text(
        "import pytest\n"
        "def test_a_direct_call():\n    assert True\n"
        "@pytest.mark.parametrize('label', ['first', 'second'])\n"
        "def test_complete(label):\n    assert label in ('first', 'second')\n"
    )
    (tmp_path / "tests/test_existing.py").write_text("def test_existing():\n    assert True\n")
    selection = [
        oracle,
        "tests/test_existing.py",
        "tests/test_progress.py",
        "tests/test_progress.py",
    ]
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(selection))
    receipt_path = tmp_path / "receipt.json"
    env = {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "MUTATION_SELECTION_PATH": str(selection_path),
        "MUTATION_RECEIPT_PATH": str(receipt_path),
    }
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "mutation.pytest_receipt", *selection],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads(receipt_path.read_text())
    calls = [report["nodeid"] for report in receipt["reports"] if report["when"] == "call"]
    expected = {
        "tests/test_existing.py::test_existing",
        "tests/test_progress.py::test_a_direct_call",
        "tests/test_progress.py::test_complete[first]",
        "tests/test_progress.py::test_complete[second]",
    }
    assert set(calls) == expected
    assert len(calls) == len(expected)
    if "[" in oracle:
        assert calls[0] == oracle
    else:
        assert calls[:2] == [oracle + "[first]", oracle + "[second]"]
