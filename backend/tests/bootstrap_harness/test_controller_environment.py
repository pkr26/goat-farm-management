"""Real child-process gates for mutation controller environment isolation."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mutation"))
producer = importlib.import_module("mutate_run")
identity = importlib.import_module("mutate_identity")


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Only the individually named real child database belongs to these gates."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """A controller gate never truncates application tables."""


@pytest.mark.parametrize("flag_value", ["1", "0"], ids=["enabled", "present-zero"])
@pytest.mark.parametrize("bootstrap", [False, True], ids=["native", "bootstrap"])
def test_child_pytest_isolates_controller_flags_and_preserves_parent_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bootstrap: bool, flag_value: str
) -> None:
    admin_url = os.environ.get("MUTATION_TEST_ADMIN_URL", "postgresql://localhost:5432/postgres")
    monkeypatch.setattr(producer, "VENV_PY", Path(sys.executable))
    workspace = tmp_path / "backend"
    (workspace / "tests/bootstrap").mkdir(parents=True)
    (workspace / "mutation").mkdir()
    shutil.copyfile(
        producer.BACKEND / "mutation/pytest_receipt.py", workspace / "mutation/pytest_receipt.py"
    )
    child = workspace / "tests/bootstrap/test_controller_child.py"
    child.write_text(
        "import asyncio, json, os\n"
        "from pathlib import Path\n"
        "import asyncpg\n"
        "def test_child_environment():\n"
        "    assert 'MUTATE_BOOTSTRAP_FIRST' not in os.environ\n"
        "    assert 'MUTATE_FULL_PHASE' not in os.environ\n"
        "    assert os.environ['CONTROLLER_TEST_SENTINEL'] == 'preserved'\n"
        f"    assert os.environ['MUTATION_TEST_ADMIN_URL'] == {admin_url!r}\n"
        "    assert os.environ['PYTHONPATH'] == str(Path.cwd())\n"
        "    assert json.loads(Path(os.environ['MUTATION_SELECTION_PATH']).read_text()) == "
        "['tests/bootstrap/test_controller_child.py::test_child_environment']\n"
        "    name = os.environ['GOATFARM_TEST_DB']\n"
        "    async def create():\n"
        "        conn = await asyncpg.connect(os.environ['MUTATION_TEST_ADMIN_URL'])\n"
        "        try:\n"
        "            await conn.execute('CREATE DATABASE \"' + name + '\"')\n"
        "        finally:\n"
        "            await conn.close()\n"
        "    asyncio.run(create())\n"
        "    Path('controller-child.json').write_text(json.dumps({'database': name, "
        "'controller_flags_absent': True}))\n"
    )
    monkeypatch.setenv("MUTATE_BOOTSTRAP_FIRST", flag_value)
    monkeypatch.setenv("MUTATE_FULL_PHASE", flag_value)
    monkeypatch.setenv("MUTATION_TEST_ADMIN_URL", admin_url)
    monkeypatch.setenv("CONTROLLER_TEST_SENTINEL", "preserved")
    instance: Any = object.__new__(producer.Runner)
    instance.run_id = uuid4().hex
    instance.phase_timeout = 30
    instance.local = threading.local()
    instance.local.workspace = workspace
    instance.full_phase = True
    instance.bootstrap_first = True
    instance.identity = {"config": {"full": True, "bootstrap": {"enabled": True}}}
    parent_identity = json.loads(json.dumps(instance.identity))
    status, output, _seconds = instance.run_pytest(
        ["tests/bootstrap/test_controller_child.py::test_child_environment"],
        0,
        confcutdir="tests/bootstrap" if bootstrap else None,
    )
    assert status == "pass", output
    receipt = instance.local.pytest_receipt
    assert receipt["exit_code"] == 0 and receipt["collected"] == 1
    assert [row["when"] for row in receipt["reports"]] == ["setup", "call", "teardown"]
    assert all(row["outcome"] == "passed" for row in receipt["reports"])
    assert instance.local.phase_identity["confcutdir"] == ("tests/bootstrap" if bootstrap else None)
    assert instance.local.phase_identity["cleanup_error"] is None
    assert (
        instance.local.phase_identity["inputs_before"]
        == instance.local.phase_identity["inputs_after"]
    )
    assert instance.full_phase is True and instance.bootstrap_first is True
    assert instance.identity == parent_identity
    assert os.environ["MUTATE_BOOTSTRAP_FIRST"] == os.environ["MUTATE_FULL_PHASE"] == flag_value
    observed = json.loads((workspace / "controller-child.json").read_text())
    assert observed["controller_flags_absent"] is True

    async def remains() -> bool:
        connection = await asyncpg.connect(os.environ["MUTATION_TEST_ADMIN_URL"])
        try:
            return bool(
                await connection.fetchval(
                    "SELECT 1 FROM pg_database WHERE datname = $1", observed["database"]
                )
            )
        finally:
            await connection.close()

    assert not asyncio.run(remains())
