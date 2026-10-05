"""Execute the complete failed/new test files against one fresh owned database."""
import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DATABASE = "goatfarm_test_fix29_recheck_104"


async def probe() -> None:
    connection = await asyncpg.connect("postgresql://localhost:5432/postgres", timeout=5)
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", DATABASE)
        if exists:
            raise RuntimeError("Owned recheck database unexpectedly exists; refusing replacement")
    finally:
        await connection.close()


asyncio.run(probe())
files = json.loads((OUT / "backend-recheck-files.json").read_text())
command = [
    sys.executable, "-m", "pytest", "-q", *files,
    "--cov=app", "--cov-branch", "--cov-report=term",
    f"--cov-report=xml:{OUT / 'backend-recheck-coverage.xml'}",
    "--cov-fail-under=0", "--durations=10",
    f"--junitxml={OUT / 'backend-recheck.xml'}",
]
environment = os.environ | {
    "GOATFARM_TEST_DB": DATABASE,
    "GOATFARM_DATABASE_URL": f"postgresql+asyncpg://localhost:5432/{DATABASE}",
    "GOATFARM_MIGRATION_DATABASE_URL": f"postgresql+asyncpg://localhost:5432/{DATABASE}",
    "COVERAGE_FILE": str(OUT / ".coverage-recheck"),
}
receipt = {
    "started_at_utc": datetime.now(UTC).isoformat(),
    "command": command, "database": DATABASE, "files": files,
    "coverage_note": "Scoped capture; the original unchanged 92% global floor is enforced separately on combined complete-suite data.",
}
start = time.monotonic()
with (OUT / "backend-recheck.log").open("w") as output:
    result = subprocess.run(command, cwd=ROOT / "backend", env=environment,
                            stdout=output, stderr=subprocess.STDOUT, check=False)
receipt.update(exit_code=result.returncode, seconds=round(time.monotonic() - start, 2),
               completed_at_utc=datetime.now(UTC).isoformat())
(OUT / "backend-recheck-run.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps({"exit": result.returncode, "seconds": receipt["seconds"]}))
raise SystemExit(result.returncode)
