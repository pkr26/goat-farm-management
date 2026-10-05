"""Exact CI empty-schema roundtrip on one explicitly owned throwaway database."""
import asyncio
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time

import asyncpg

OUT = pathlib.Path(__file__).resolve().parent
ROOT = OUT.parents[3]
BACKEND = ROOT / "backend"
NAME = "goatfarm_test_a26_migration_9b83417e"
ADMIN = "postgresql://localhost:5432/postgres"
TARGET = f"postgresql://localhost:5432/{NAME}"
APP_TARGET = f"postgresql+asyncpg://localhost:5432/{NAME}"
assert NAME == "goatfarm_test_a26_migration_9b83417e" and "_test_" in NAME
assert not (BACKEND / ".env").exists(), "Refuse to load an ambient backend/.env"
env = {k: v for k, v in os.environ.items() if not k.startswith("GOATFARM_")}
env.update({"GOATFARM_TEST_DB": NAME,
            "GOATFARM_DATABASE_URL": APP_TARGET,
            "GOATFARM_MIGRATION_DATABASE_URL": APP_TARGET,
            "GOATFARM_DATABASE_URL_FILE": "",
            "GOATFARM_MIGRATION_DATABASE_URL_FILE": "",
            "GOATFARM_ENVIRONMENT": "development",
            "GOATFARM_DB_SSLMODE": "disable"})
steps = [("upgrade", "head"), ("check",), ("downgrade", "base"), ("upgrade", "head"), ("check",)]
result = {"database": NAME, "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
          "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
          "explicit_environment": {k: v for k, v in env.items() if k.startswith("GOATFARM_")},
          "backend_dotenv_absent": True, "steps": [], "scope": "Empty-schema CI roundtrip; no populated historical data or production TLS assurance."}

async def snapshot():
    connection = await asyncpg.connect(TARGET)
    try:
        current = await connection.fetchval("SELECT current_database()")
        assert current == NAME
        tables = await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        names = [row["tablename"] for row in tables]
        revisions = []
        if "alembic_version" in names:
            revisions = [row["version_num"] for row in await connection.fetch("SELECT version_num FROM alembic_version ORDER BY version_num")]
        return {"current_database": current, "public_tables": names, "alembic_revisions": revisions}
    finally:
        await connection.close()

async def main():
    admin = await asyncpg.connect(ADMIN)
    created = False
    try:
        assert await admin.fetchval("SELECT current_database()") == "postgres"
        exists = await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", NAME)
        assert not exists, "Refusing to manipulate a pre-existing database"
        result["preexisting_database"] = False
        result["server_version"] = await admin.fetchval("SHOW server_version")
        await admin.execute(f'CREATE DATABASE "{NAME}"')
        created = True
        result["initial_schema"] = await snapshot()
        assert result["initial_schema"]["public_tables"] == []
        with (OUT / "migration-roundtrip.log").open("w") as log:
            for sequence, arguments in enumerate(steps, start=1):
                assert not (BACKEND / ".env").exists()
                command = [sys.executable, "-m", "alembic", *arguments]
                started = time.monotonic()
                done = subprocess.run(command, cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180)
                elapsed = round(time.monotonic() - started, 3)
                log.write(f"STEP {sequence}: {' '.join(command)}\n")
                log.write(f"TARGET: {NAME}; EXIT: {done.returncode}; SECONDS: {elapsed}\n")
                log.write(done.stdout + done.stderr + "\n")
                log.flush()
                record = {"sequence": sequence, "command": command, "cwd": str(BACKEND), "exit": done.returncode,
                          "seconds": elapsed, "schema_after": await snapshot()}
                result["steps"].append(record)
                if done.returncode:
                    result["unexpected_failure"] = sequence
                    raise RuntimeError(f"Migration step {sequence} failed; stopped remaining steps")
                if arguments == ("downgrade", "base"):
                    assert record["schema_after"]["alembic_revisions"] == []
                    assert record["schema_after"]["public_tables"] == ["alembic_version"]
        result["all_steps_passed"] = True
    finally:
        if created:
            # No FORCE, no other database name, no connection termination.
            await admin.execute(f'DROP DATABASE "{NAME}"')
            result["owned_database_dropped"] = not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", NAME)
        await admin.close()
        result["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (OUT / "migration-roundtrip.json").write_text(json.dumps(result, indent=2) + "\n")

asyncio.run(main())
print(json.dumps({"all_steps_passed": result.get("all_steps_passed", False),
                  "step_exit_codes": [step["exit"] for step in result["steps"]],
                  "owned_database_dropped": result.get("owned_database_dropped"),
                  "database": NAME}, indent=2))
