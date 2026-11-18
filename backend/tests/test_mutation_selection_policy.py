"""Actual constant-reader kills, complete collection, and equal timeout budgets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

MUTATION = Path(__file__).resolve().parents[1] / "mutation"
sys.path.insert(0, str(MUTATION))
import mutate_cover  # type: ignore[import-not-found]  # noqa: E402
import mutate_gen  # type: ignore[import-not-found]  # noqa: E402
from mutate_identity import (  # type: ignore[import-not-found]  # noqa: E402
    EXECUTION_MODE_POLICY,
    digest_json,
    execution_modes,
    input_identity,
)
from mutate_selection import (  # type: ignore[import-not-found]  # noqa: E402
    ConcreteInventory,
    GenericSelection,
    SelectionTimeoutPolicy,
    collected_nodes,
)


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """These isolated selection fixtures do not use the application database."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """No integration tables are opened by these harness contracts."""


def provenance(files: dict[str, str]) -> dict[str, Any]:
    return {
        "inputs": {
            "tests": {
                file: hashlib.sha256(source.encode()).hexdigest() for file, source in files.items()
            }
        },
        "collection_inventory": {
            "exit_code": 0,
            "items": [{"nodeid": file + "::test_value", "file": file} for file in files],
        },
    }


def test_import_time_class_value_runs_its_reader_beyond_the_old_three_file_sample(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend"
    for name in ("app", "tests", "mutation"):
        (backend / name).mkdir(parents=True)
    (backend / "app/__init__.py").write_text("")
    source = "class Limits:\n    maximum = 10\n\ndef ready():\n    return True\n"
    (backend / "app/settings.py").write_text(source)
    (backend / "app/bridge.py").write_text("from app.settings import ready\n")
    files = {
        **{
            f"tests/test_{letter}.py": (
                "from app.bridge import ready\ndef test_value():\n    assert ready() is True\n"
            )
            for letter in ("a", "b", "c")
        },
        "tests/test_z_reader.py": (
            "from app.settings import Limits\ndef test_value():\n    assert Limits.maximum == 10\n"
        ),
    }
    for file, text in files.items():
        (backend / file).write_text(text)
    shutil.copyfile(MUTATION / "pytest_receipt.py", backend / "mutation/pytest_receipt.py")
    # Runtime coverage of ready() cannot identify a test that only reads the
    # class value constructed during import before per-test contexts exist.
    measured = {file + "::test_value" for file in list(files)[:3]}
    policy = GenericSelection(
        {"app/settings.py": {5: measured}}, provenance(files), backend=backend
    )
    selected = policy.select({"file": "app/settings.py", "line": 2, "scope": "module"})
    assert selected[0] == "tests/test_z_reader.py::test_value"
    assert set(selected) == set(collected_nodes(provenance(files)))

    def run(nodes: list[str], label: str) -> tuple[int, dict[str, Any]]:
        receipt = backend / f"{label}-receipt.json"
        selection = backend / f"{label}-selection.json"
        selection.write_text(json.dumps(nodes))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-x",
                "-p",
                "mutation.pytest_receipt",
                *nodes,
            ],
            cwd=backend,
            env=os.environ
            | {
                "PYTHONPATH": str(backend),
                "PYTHONDONTWRITEBYTECODE": "1",
                "MUTATION_RECEIPT_PATH": str(receipt),
                "MUTATION_SELECTION_PATH": str(selection),
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert receipt.exists(), result.stdout + result.stderr
        return result.returncode, json.loads(receipt.read_text())

    baseline_code, baseline = run(selected, "clean")
    assert baseline_code == 0 and baseline["collected"] == 4
    (backend / "app/settings.py").write_text(source.replace("maximum = 10", "maximum = 11"))
    sampled_code, _ = run(sorted(measured), "old-sample")
    mutant_code, mutant = run(selected, "complete")
    assert sampled_code == 0
    failures = [row for row in mutant["reports"] if row["outcome"] == "failed"]
    assert mutant_code == 1 and mutant["collected"] == baseline["collected"]
    assert failures == [
        {
            "nodeid": "tests/test_z_reader.py::test_value",
            "when": "call",
            "outcome": "failed",
            "assertion": True,
        }
    ]


def test_empty_module_context_is_complete_but_unexecuted_function_remains_uncovered(
    tmp_path: Path,
) -> None:
    files = {"tests/test_values.py": "def test_value():\n    assert True\n"}
    (tmp_path / "tests").mkdir()
    (tmp_path / "app").mkdir()
    source = "def unused():\n    return 1\n"
    (tmp_path / "app/auth.py").write_text(source)
    (tmp_path / "tests/test_values.py").write_text(files["tests/test_values.py"])
    data = provenance(files)
    data["inputs"]["sources"] = {"app/auth.py": hashlib.sha256(source.encode()).hexdigest()}
    policy = GenericSelection({}, data, backend=tmp_path)
    assert policy.select({"file": "app/schemas/team.py", "line": 1, "scope": "module"}) == [
        "tests/test_values.py::test_value"
    ]
    assert (
        policy.select(
            {
                "file": "app/auth.py",
                "line": 2,
                "scope": "function",
                "stmt_line": 2,
                "stmt_end_line": 2,
            }
        )
        == []
    )


def test_importer_priority_refuses_test_bytes_changed_after_coverage(tmp_path: Path) -> None:
    files = {"tests/test_values.py": "def test_value():\n    assert True\n"}
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_values.py").write_text("def test_other():\n    assert True\n")
    policy = GenericSelection({}, provenance(files), backend=tmp_path)
    with pytest.raises(ValueError, match="source changed after coverage"):
        policy.select({"file": "app/settings.py", "line": 1, "scope": "module"})


def test_timeout_counts_all_concrete_parameter_cases_and_deduplicates_overlap() -> None:
    file = "tests/test_many.py"
    inventory = {
        "collection_inventory": {
            "exit_code": 0,
            "items": [
                {"nodeid": f"{file}::TestMany::test_value[{index}]", "file": file}
                for index in range(500)
            ],
        }
    }
    policy = SelectionTimeoutPolicy(inventory)
    expected = 120.0 + 2.0 * 500
    assert policy.seconds([file]) == expected
    overlapping = [file, file + "::TestMany", file + "::TestMany::test_value[7]"]
    assert policy.seconds(overlapping) == expected
    assert policy.seconds(list(ConcreteInventory(inventory).nodes)) == expected
    assert policy.seconds([file + "::TestMany::test_value[7]"]) == 300
    # Repeated clean/mutant calls consult the same captured case inventory;
    # observed mutant speed never changes the next phase's budget.
    assert policy.seconds([file]) == expected


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan"), True])
def test_timeout_rejects_unbounded_or_nonpositive_explicit_limits(seconds: float) -> None:
    with pytest.raises(ValueError, match="finite positive"):
        SelectionTimeoutPolicy({}, explicit_seconds=seconds)


def test_explicit_timeout_remains_an_absolute_equal_budget() -> None:
    policy = SelectionTimeoutPolicy({}, explicit_seconds=7200)
    assert policy.seconds(["tests/test_many.py"]) == 7200
    assert policy.configuration()["explicit_seconds"] == 7200


def test_missing_stale_and_duplicate_inventory_never_shrinks_complete_selection() -> None:
    with pytest.raises(ValueError, match="inventory missing"):
        SelectionTimeoutPolicy({}).seconds(["tests/test_value.py"])
    data = provenance({"tests/test_values.py": "def test_value(): pass\n"})
    with pytest.raises(ValueError, match="absent from complete captured inventory"):
        SelectionTimeoutPolicy(data).seconds(["tests/test_deleted.py"])
    data["collection_inventory"]["items"] *= 2
    with pytest.raises(ValueError, match="duplicate concrete"):
        collected_nodes(data)


def test_generator_keeps_class_fields_and_definition_defaults_at_import_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = tmp_path / "backend"
    (backend / "app").mkdir(parents=True)
    (backend / "mutation").mkdir()
    path = backend / "app/schema.py"
    path.write_text(
        "class Limits:\n    maximum = 10\n    def value(self, default=12):\n        return 11\n"
    )
    monkeypatch.setattr(mutate_gen, "BACKEND", backend)
    monkeypatch.setattr(mutate_gen, "target_files", lambda: [path])
    mutate_gen.generate()
    rows = json.loads((backend / "mutation/manifest.json").read_text())
    assert {row["scope"] for row in rows if row["line"] in (2, 3)} == {"module"}
    assert {row["scope"] for row in rows if row["line"] == 4} == {"function"}


def test_single_worker_coverage_publishes_every_case_despite_ambient_keyword_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = tmp_path / "project/backend"
    for name in ("app", "tests", "mutation"):
        (backend / name).mkdir(parents=True)
    (backend / "app/__init__.py").write_text("")
    (backend / "app/value.py").write_text("def value():\n    return 7\n")
    (backend / "tests/test_value.py").write_text(
        "from app.value import value\n"
        "def test_value():\n    assert value() == 7\n"
        "def test_other():\n    assert value() > 0\n"
    )
    (backend / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n[tool.coverage.report]\nfail_under=100\n"
    )
    shutil.copyfile(MUTATION / "coverage_inventory.py", backend / "mutation/coverage_inventory.py")
    monkeypatch.setattr(mutate_cover, "BACKEND", backend)
    monkeypatch.setattr(mutate_cover, "VENV_PY", Path(sys.executable))
    assert not (backend / ".venv").exists()
    monkeypatch.setattr(sys, "argv", ["mutate_cover.py", "--workers", "1"])
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k no_case_can_match_this_filter")
    operating_system_tmp = tmp_path / "os-temp"
    operating_system_tmp.mkdir()
    monkeypatch.setattr(mutate_cover.tempfile, "tempdir", str(operating_system_tmp))
    mutate_cover.main()
    publication = json.loads((backend / ".coverage-mut.provenance.json").read_text())
    assert collected_nodes(publication) == (
        "tests/test_value.py::test_value",
        "tests/test_value.py::test_other",
    )
    assert publication["pytest_addopts"] == "cleared"
    assert publication["execution_mode_policy"] == EXECUTION_MODE_POLICY
    assert publication["execution_modes_sha256"] == digest_json(
        execution_modes(backend, input_identity(backend))
    )
    source_root = Path(publication["source_root"])
    durable_root = backend / "mutation/campaigns/runtime"
    assert source_root.is_relative_to(durable_root)
    assert not source_root.exists()
    assert not list(durable_root.iterdir())
    assert not list(operating_system_tmp.iterdir())


@pytest.mark.parametrize("location", ["live", "snapshot"])
def test_a_passing_baseline_cannot_publish_after_captured_file_permissions_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    backend = tmp_path / "project/backend"
    for name in ("app", "tests", "mutation"):
        (backend / name).mkdir(parents=True)
    (backend / "app/__init__.py").write_text("")
    source = backend / "app/value.py"
    source.write_text("def value():\n    return 7\n")
    target = repr(str(source)) if location == "live" else "app.value.__file__"
    (backend / "tests/test_value.py").write_text(
        "from pathlib import Path\nimport app.value\n"
        "def test_value():\n    assert app.value.value() == 7\n"
        f"    target = Path({target})\n"
        "    target.chmod(target.stat().st_mode ^ 0o100)\n"
    )
    (backend / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n[tool.coverage.report]\nfail_under=100\n"
    )
    shutil.copyfile(MUTATION / "coverage_inventory.py", backend / "mutation/coverage_inventory.py")
    previous = {
        ".coverage-mut": b"previous coverage publication",
        ".coverage-mut.provenance.json": b"previous provenance publication",
    }
    for name, content in previous.items():
        (backend / name).write_bytes(content)
    monkeypatch.setattr(mutate_cover, "BACKEND", backend)
    monkeypatch.setattr(mutate_cover, "VENV_PY", Path(sys.executable))
    assert not (backend / ".venv").exists()
    monkeypatch.setattr(sys, "argv", ["mutate_cover.py", "--workers", "1"])
    with pytest.raises(SystemExit, match="execution permissions changed during baseline"):
        mutate_cover.main()
    assert {name: (backend / name).read_bytes() for name in previous} == previous


def test_collector_fixture_works_when_imported_from_a_checkout_without_a_venv(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "isolated/backend"
    (backend / "mutation").mkdir(parents=True)
    (backend / "tests").mkdir()
    for source in MUTATION.glob("*.py"):
        shutil.copyfile(source, backend / "mutation" / source.name)
    shutil.copyfile(Path(__file__), backend / "tests/test_mutation_selection_policy.py")
    (backend / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths=['tests']\n")
    assert not (backend / ".venv").exists()
    script = (
        "import sys\nsys.path.insert(0, 'mutation')\nimport mutate_cover\n"
        "assert not mutate_cover.VENV_PY.exists()\nimport pytest\n"
        "raise SystemExit(pytest.main(['-q', "
        "'tests/test_mutation_selection_policy.py::"
        "test_single_worker_coverage_publishes_every_case_despite_ambient_keyword_filter']))\n"
    )
    environment = os.environ.copy()
    environment.pop("PYTEST_ADDOPTS", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(backend)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_coverage_selects_precise_multiline_statement_and_short_circuit_coverers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coverage

    backend = tmp_path / "backend"
    for name in ("app", "tests", "mutation"):
        (backend / name).mkdir(parents=True)
    (backend / "app/__init__.py").write_text("")
    source = (
        "def value(flag):\n"
        "    result = (\n"
        "        (\n"
        "            7\n"
        "        )\n"
        "        if flag\n"
        "        else 3\n"
        "    )\n"
        "    return result\n\n"
        "def guarded(flag):\n"
        "    return (\n"
        "        flag\n"
        "        and (10 < 20)\n"
        "    )\n\n"
        "def unrelated():\n"
        "    return 91\n\n"
        "def never_called():\n"
        "    return 101\n"
    )
    path = backend / "app/value.py"
    path.write_text(source)
    (backend / "tests/test_values.py").write_text(
        "from app.value import value, guarded, unrelated\n"
        "def test_true():\n    assert value(True) == 7\n"
        "def test_false():\n    assert value(False) == 3\n"
        "def test_guard_true():\n    assert guarded(True) is True\n"
        "def test_guard_false():\n    assert guarded(False) is False\n"
        "def test_other():\n    assert unrelated() == 91\n"
    )
    (backend / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths=['tests']\n")
    for plugin in ("coverage_inventory.py", "pytest_receipt.py"):
        shutil.copyfile(MUTATION / plugin, backend / "mutation" / plugin)
    coverage_file = backend / ".coverage-selection"
    inventory_path = backend / "inventory.json"
    environment = os.environ.copy()
    environment.pop("PYTEST_ADDOPTS", None)
    environment.update(
        PYTHONPATH=str(backend),
        PYTHONDONTWRITEBYTECODE="1",
        COVERAGE_FILE=str(coverage_file),
        MUTATION_COVERAGE_INVENTORY_PATH=str(inventory_path),
    )
    clean = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--cov=app",
            "--cov-context=test",
            "--cov-report=",
            "-p",
            "mutation.coverage_inventory",
        ],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    cov = coverage.Coverage(data_file=str(coverage_file))
    cov.load()
    line_contexts = {
        line: {context.rsplit("|", 1)[0] for context in contexts if context}
        for line, contexts in (cov.get_data().contexts_by_lineno(str(path)) or {}).items()
    }
    data = {
        "inputs": input_identity(backend),
        "collection_inventory": json.loads(inventory_path.read_text()),
    }
    policy = GenericSelection({"app/value.py": line_contexts}, data, backend=backend)
    monkeypatch.setattr(mutate_gen, "BACKEND", backend)
    monkeypatch.setattr(mutate_gen, "target_files", lambda: [path])
    mutate_gen.generate()
    mutants = json.loads((backend / "mutation/manifest.json").read_text())
    multiline = next(row for row in mutants if row["kind"] == "ifexp")
    assert multiline["line"] == 3 and not line_contexts.get(multiline["line"])
    selected = policy.select(multiline)
    assert selected == ["tests/test_values.py::test_true", "tests/test_values.py::test_false"]
    guard = next(row for row in mutants if row["kind"] == "compare" and row["line"] == 14)
    assert "tests/test_values.py::test_guard_false" not in line_contexts[14]
    assert policy.select(guard) == [
        "tests/test_values.py::test_guard_true",
        "tests/test_values.py::test_guard_false",
    ]
    never = next(row for row in mutants if row["kind"] == "intconst" and row["line"] == 21)
    assert policy.select(never) == []

    def run(label: str) -> tuple[int, dict[str, Any]]:
        receipt_path = backend / f"{label}-receipt.json"
        selection_path = backend / f"{label}-selection.json"
        selection_path.write_text(json.dumps(selected))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-x",
                "-p",
                "mutation.pytest_receipt",
                *selected,
            ],
            cwd=backend,
            env=environment
            | {
                "MUTATION_RECEIPT_PATH": str(receipt_path),
                "MUTATION_SELECTION_PATH": str(selection_path),
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert receipt_path.is_file(), result.stdout + result.stderr
        return result.returncode, json.loads(receipt_path.read_text())

    code, control = run("control")
    assert code == 0 and control["collected"] == 2
    mutated = source.replace(
        multiline["orig_span"],
        textwrap.indent(multiline["mut_stmt"], " " * multiline["stmt_col"]) + "\n",
        1,
    )
    assert mutated != source
    path.write_text(mutated)
    code, result = run("mutant")
    failures = [report for report in result["reports"] if report["outcome"] == "failed"]
    assert code == 1 and result["collected"] == control["collected"]
    assert failures and all(row["when"] == "call" and row["assertion"] for row in failures)


@pytest.mark.parametrize(
    "bounds",
    [
        {},
        {"stmt_line": 0, "stmt_end_line": 2},
        {"stmt_line": 3, "stmt_end_line": 2},
        {"stmt_line": True, "stmt_end_line": 2},
        {"stmt_line": 2, "stmt_end_line": 99},
        {"stmt_line": 2, "stmt_end_line": 3},
    ],
)
def test_function_selection_refuses_missing_or_non_statement_source_bounds(
    tmp_path: Path, bounds: dict[str, Any]
) -> None:
    (tmp_path / "app").mkdir()
    source = "def first():\n    return 1\ndef second():\n    return 2\n"
    (tmp_path / "app/value.py").write_text(source)
    data = provenance({"tests/test_values.py": "def test_value(): pass\n"})
    data["inputs"]["sources"] = {"app/value.py": hashlib.sha256(source.encode()).hexdigest()}
    policy = GenericSelection({}, data, backend=tmp_path)
    with pytest.raises(ValueError, match="statement bounds"):
        policy.select({"file": "app/value.py", "line": 2, "scope": "function", **bounds})
