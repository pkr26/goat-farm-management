"""Reproduce security/ops failures using exact fee30d5 function bodies.

The current tests/schema supply fixtures. Only the two audited function code
objects are restored inside the isolated pytest process; workspace files are
never rolled back. The throwaway probe is removed even if pytest fails.
"""

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
EVIDENCE = Path(__file__).resolve().parent
REVISION = "fee30d5"

PROBE = '''import ast
import subprocess
from pathlib import Path

import pytest

from app.api import auth
from app.services.notifications import service
from tests.test_independent_security_ops_audit import (
    test_outbox_preserves_pre_migration_delivery_claims_across_midnight,
    test_ownership_transfer_does_not_deadlock_with_reciprocal_roster_edit,
)

@pytest.fixture(autouse=True)
def exact_original_functions(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    for module, path, function in (
        (auth, "backend/app/api/auth.py", "transfer_farm_ownership"),
        (service, "backend/app/services/notifications/service.py", "send_notification"),
    ):
        original = subprocess.check_output(
            ["git", "show", "fee30d5:" + path], cwd=root, text=True
        )
        tree = ast.parse(original)
        node = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == function
        )
        body = "\\n".join(original.splitlines()[node.lineno - 1:node.end_lineno])
        namespace = dict(vars(module))
        exec(compile(body, "fee30d5:" + path, "exec"), namespace)
        monkeypatch.setattr(getattr(module, function), "__code__", namespace[function].__code__)
'''


def main() -> int:
    if not os.environ.get("GOATFARM_TEST_DB", "").startswith("goatfarm_test_"):
        raise SystemExit("Set GOATFARM_TEST_DB to a unique disposable goatfarm_test_* database")
    fd, name = tempfile.mkstemp(prefix="_audit_security_ops_replay_", suffix=".py", dir=BACKEND / "tests")
    os.close(fd)
    probe = Path(name)
    try:
        probe.write_text(PROBE)
        command = [sys.executable, "-m", "pytest", str(probe), "-q", "--tb=short"]
        result = subprocess.run(command, cwd=BACKEND, capture_output=True, text=True, check=False)
        log = result.stdout + result.stderr
        (EVIDENCE / "original-failures.log").write_text(log)
        originals = {
            path: hashlib.sha256(
                subprocess.check_output(["git", "show", REVISION + ":" + path], cwd=ROOT)
            ).hexdigest()
            for path in ("backend/app/api/auth.py", "backend/app/services/notifications/service.py")
        }
        reproduced = (
            result.returncode == 1
            and "6 failed" in log
            and "NoResultFound" in log
            and "DeadlockDetectedError" in log
        )
        receipt = {
            "revision": REVISION,
            "original_source_sha256": originals,
            "pytest_exit_code": result.returncode,
            "reproduced": reproduced,
            "expected_failures": 6,
            "log": "original-failures.log",
            "scope": "Exact original auth transfer/notification function bodies; current disposable schema and fixtures",
        }
        (EVIDENCE / "original-failures.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, indent=2))
        return 0 if reproduced else 1
    finally:
        probe.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
