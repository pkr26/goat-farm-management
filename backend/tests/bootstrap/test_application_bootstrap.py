"""The public stateless health application must construct in a fresh process.

Run from tests/bootstrap with --confcutdir=tests/bootstrap so an application
import failure is observed by this call assertion, before integration-fixture
collection can import the same defective application.
"""

import os
import subprocess
import sys
from pathlib import Path


def test_backend_constructs_and_serves_the_public_health_app() -> None:
    probe = """
import asyncio
import httpx
from app.main import create_app

async def main():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://localhost",
    ) as client:
        response = await client.get("/healthz")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok", response.text

asyncio.run(main())
"""
    environment = os.environ.copy()
    # /healthz does no database I/O. Name only the isolated test endpoint if
    # application construction unexpectedly creates an engine.
    environment["GOATFARM_DATABASE_URL"] = (
        "postgresql+asyncpg://localhost:5437/herdly_bootstrap_unused_test"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
