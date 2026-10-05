"""Owned disposable DB and secrets for production-build browser verification."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
DB = "goatfarm_test_fix29_browser_104"
STATE = ROOT / "frontend/e2e/.e2e-state.json"


async def database(create: bool) -> None:
    conn = await asyncpg.connect("postgresql://localhost:5432/postgres")
    try:
        if create:
            await conn.execute(f'CREATE DATABASE "{DB}"')
        else:
            await conn.execute(f'DROP DATABASE "{DB}" WITH (FORCE)')
    finally:
        await conn.close()


def main() -> None:
    previous = STATE.read_bytes() if STATE.exists() else None
    created = False
    results = {}
    with tempfile.TemporaryDirectory(prefix="herdly-fix29-browser-") as temporary:
        env = os.environ.copy()
        env.update({
            "PATH": f"{Path.home()}/.local/bin:{env['PATH']}",
            "GOATFARM_DATABASE_URL": f"postgresql+asyncpg://localhost:5432/{DB}",
            "GOATFARM_MIGRATION_DATABASE_URL": f"postgresql+asyncpg://localhost:5432/{DB}",
            "GOATFARM_ENVIRONMENT": "development",
            "GOATFARM_AUTH_RATE_LIMIT_ENABLED": "false",
            "GOATFARM_WORKER_ROSTER_ENABLED": "true",
            "GOATFARM_JWT_PRIVATE_KEY_PATH": f"{temporary}/jwt_private.pem",
            "GOATFARM_JWT_PUBLIC_KEY_PATH": f"{temporary}/jwt_public.pem",
            "TZ": "America/Phoenix",
        })
        try:
            asyncio.run(database(True))
            created = True
            with (OUT / "browser-migration.log").open("w") as log:
                subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                               cwd=ROOT / "backend", env=env, stdout=log,
                               stderr=subprocess.STDOUT, check=True)
            for browser in sys.argv[1:] or ["chromium", "webkit"]:
                env["E2E_BROWSER"] = browser
                with (OUT / f"browser-{browser}.log").open("w") as log:
                    result = subprocess.run([
                        "pnpm", "exec", "playwright", "test", "--config",
                        str(OUT / "playwright.remediation.config.mts"),
                    ], cwd=ROOT / "frontend", env=env, stdout=log, stderr=subprocess.STDOUT)
                results[browser] = result.returncode
                print(f"{browser}: exit {result.returncode}", flush=True)
        finally:
            if created:
                asyncio.run(database(False))
            if previous is None:
                STATE.unlink(missing_ok=True)
            else:
                STATE.write_bytes(previous)
            (OUT / "browser-run-exits.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
