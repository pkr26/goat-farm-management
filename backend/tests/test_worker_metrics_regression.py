"""Production worker telemetry must neither require API secrets nor vanish in its process."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app import metrics
from app.core.config import get_screening_worker_settings, get_settings
from app.db import get_sessionmaker
from app.models import ScreeningCallReservation, ScreeningImage, ScreeningImageStatus, ScreeningRun
from app.services.screening.pipeline import run_screening_cycle
from app.services.screening.rotation import ProviderRotation

from .conftest import owner_with_farm
from .test_screening import CountingProvider, FakeStorage, _jpeg_bytes, _register_fake_objects

TOKEN = "worker-scrape-fixture-secret-" + "x" * 32


def _production_worker_env() -> dict[str, str]:
    return {
        "GOATFARM_ENVIRONMENT": "production",
        "GOATFARM_DATABASE_URL": "postgresql+asyncpg://worker:fixture@db.example.invalid/goatfarm",
        "GOATFARM_DB_SSLMODE": "verify-full",
        "GOATFARM_SCREENING_ENABLED": "true",
        "GOATFARM_SCREENING_CROP_DETECTION_ENABLED": "false",
        "GOATFARM_S3_BUCKET": "goat-photos",
        "GOATFARM_S3_ACCESS_KEY_ID": "fixture-access-key",
        "GOATFARM_S3_SECRET_ACCESS_KEY": "fixture-secret-key",
        "GOATFARM_SCREENING_ANTHROPIC_API_KEY": "fixture-provider-key",
        "GOATFARM_METRICS_BEARER_TOKEN": TOKEN,
    }


@pytest.fixture
def worker_metrics_configuration_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in tuple(os.environ):
        if name.startswith("GOATFARM_"):
            monkeypatch.delenv(name)
    for name, value in _production_worker_env().items():
        monkeypatch.setenv(name, value)
    get_screening_worker_settings.cache_clear()
    yield
    get_screening_worker_settings.cache_clear()


@pytest.mark.usefixtures("worker_metrics_configuration_environment")
def test_worker_metrics_token_file_loads_and_redacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "metrics-token"
    token_file.write_text(TOKEN + "\n")
    monkeypatch.delenv("GOATFARM_METRICS_BEARER_TOKEN")
    monkeypatch.setenv("GOATFARM_METRICS_BEARER_TOKEN_FILE", str(token_file))
    settings = get_screening_worker_settings()
    assert settings.metrics_bearer_token is not None
    assert settings.metrics_bearer_token.get_secret_value() == TOKEN
    assert TOKEN not in repr(settings)
    assert TOKEN not in settings.model_dump_json()


@pytest.mark.usefixtures("worker_metrics_configuration_environment")
def test_worker_metrics_blank_optional_compose_variables_disable_scraping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOATFARM_METRICS_BEARER_TOKEN", "  ")
    monkeypatch.setenv("GOATFARM_METRICS_BEARER_TOKEN_FILE", "")
    settings = get_screening_worker_settings()
    assert settings.metrics_bearer_token is None
    assert settings.metrics_bearer_token_file is None


@pytest.mark.usefixtures("worker_metrics_configuration_environment")
def test_worker_metrics_rejects_ambiguous_secret_delivery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "metrics-token"
    token_file.write_text(TOKEN)
    monkeypatch.setenv("GOATFARM_METRICS_BEARER_TOKEN_FILE", str(token_file))
    with pytest.raises(ValidationError, match="both set; deliver exactly one"):
        get_screening_worker_settings()


@pytest.mark.usefixtures("worker_metrics_configuration_environment")
@pytest.mark.parametrize("delivery", ["plain", "file"])
def test_worker_metrics_rejects_short_token_from_either_delivery_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, delivery: str
) -> None:
    if delivery == "file":
        token_file = tmp_path / "metrics-token"
        token_file.write_text("x" * 31)
        monkeypatch.delenv("GOATFARM_METRICS_BEARER_TOKEN")
        monkeypatch.setenv("GOATFARM_METRICS_BEARER_TOKEN_FILE", str(token_file))
    else:
        monkeypatch.setenv("GOATFARM_METRICS_BEARER_TOKEN", "x" * 31)
    with pytest.raises(ValidationError, match="must contain at least 32 characters"):
        get_screening_worker_settings()


@pytest.mark.usefixtures("worker_metrics_configuration_environment")
@pytest.mark.parametrize("missing", [False, True])
def test_worker_metrics_fails_closed_for_unusable_token_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: bool
) -> None:
    token_file = tmp_path / "metrics-token"
    if not missing:
        token_file.write_text("\n ")
    monkeypatch.delenv("GOATFARM_METRICS_BEARER_TOKEN")
    monkeypatch.setenv("GOATFARM_METRICS_BEARER_TOKEN_FILE", str(token_file))
    with pytest.raises(ValidationError, match="GOATFARM_METRICS_BEARER_TOKEN_FILE"):
        get_screening_worker_settings()


@pytest.mark.parametrize("provider_fails", [False, True])
@pytest.mark.parametrize("collection_enabled", [False, True])
async def test_production_worker_persists_provider_success_and_failure_without_api_secrets(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    provider_fails: bool,
    collection_enabled: bool,
) -> None:
    headers = await owner_with_farm(client, email="worker-metrics-regression@synthetic.invalid")
    farm_id = int(headers["X-Farm-Id"])
    storage = FakeStorage()
    storage.objects[f"raw/{farm_id}/{dt.date.today().isoformat()}/tall.jpg"] = _jpeg_bytes(80, 160)
    provider = CountingProvider(name="anthropic", fail=provider_fails)
    async with get_sessionmaker()() as db:
        await _register_fake_objects(db, farm_id, storage)
    before = metrics.SCREENING_PROVIDER_CALLS.labels(
        provider="anthropic", outcome="error" if provider_fails else "ok"
    )._value.get()
    try:
        with monkeypatch.context() as scoped:
            for name in tuple(os.environ):
                if name.startswith("GOATFARM_"):
                    scoped.delenv(name)
            for name, value in _production_worker_env().items():
                scoped.setenv(name, value)
            scoped.setenv("GOATFARM_METRICS_ENABLED", str(collection_enabled).lower())
            get_screening_worker_settings.cache_clear()
            get_settings.cache_clear()
            settings = get_screening_worker_settings()
            # The same explicit process initializer used by worker._run_loop.
            metrics.configure(enabled=settings.metrics_enabled)
            async with get_sessionmaker()() as db:
                summary = await run_screening_cycle(
                    db, settings, storage, ProviderRotation([provider])
                )
                image = (await db.execute(select(ScreeningImage))).scalar_one()
                runs = (
                    await db.execute(select(func.count()).select_from(ScreeningRun))
                ).scalar_one()
                calls = (
                    await db.execute(select(func.count()).select_from(ScreeningCallReservation))
                ).scalar_one()
                assert provider.calls == calls == runs == 1
                assert image.status == (
                    ScreeningImageStatus.ERROR if provider_fails else ScreeningImageStatus.HEALTHY
                )
                assert summary.errors == int(provider_fails)
                assert "unexpected screening failure" not in (image.error or "")
        after = metrics.SCREENING_PROVIDER_CALLS.labels(
            provider="anthropic", outcome="error" if provider_fails else "ok"
        )._value.get()
        assert after - before == int(collection_enabled)
    finally:
        metrics.configure(enabled=True)
        get_settings.cache_clear()
        get_screening_worker_settings.cache_clear()


@pytest.mark.parametrize("collection_enabled", [False, True])
def test_worker_registry_is_scraped_from_a_separate_process(collection_enabled: bool) -> None:
    script = """
import json, sys
from app import metrics
from app.core.config import get_screening_worker_settings
from app.worker.metrics_server import worker_metrics_server
settings = get_screening_worker_settings()
metrics.configure(enabled=settings.metrics_enabled)
metrics.record_screening_provider_call("anthropic", True)
metrics.record_screening_provider_call("anthropic", False)
with worker_metrics_server(settings, host="127.0.0.1", port=0) as server:
    print(json.dumps({"port": None if server is None else server.server_port}), flush=True)
    sys.stdin.readline()
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOATFARM_")}
    env.update(_production_worker_env())
    env["GOATFARM_METRICS_ENABLED"] = str(collection_enabled).lower()
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        result = json.loads(process.stdout.readline())
        if not collection_enabled:
            assert result["port"] is None
        else:
            url = f"http://127.0.0.1:{result['port']}/metrics"
            with httpx.Client(timeout=5) as scrape:
                assert scrape.get(url).status_code == 401
                assert scrape.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
                response = scrape.get(url, headers={"Authorization": f"Bearer {TOKEN}"})
                assert response.status_code == 200
                assert (
                    'goatfarm_screening_provider_calls_total{outcome="ok",provider="anthropic"} 1.0'
                    in response.text
                )
                assert (
                    "goatfarm_screening_provider_calls_total"
                    '{outcome="error",provider="anthropic"} 1.0' in response.text
                )
                assert (
                    'goatfarm_screening_provider_estimated_cost_inr_total{provider="anthropic"} 1.8'
                    in response.text
                )
                assert (
                    scrape.get(
                        url + "/other", headers={"Authorization": f"Bearer {TOKEN}"}
                    ).status_code
                    == 404
                )
        stdout, stderr = process.communicate("stop\n", timeout=10)
        assert process.returncode == 0, stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_worker_metrics_listener_authentication_and_shutdown_in_process() -> None:
    """Exercise the real thread, socket, WSGI auth and shutdown in this interpreter."""
    import socket

    from pydantic import SecretStr

    from app.core.config import ScreeningWorkerSettings
    from app.worker.metrics_server import worker_metrics_server

    from .settings_helpers import settings_from_input

    settings = settings_from_input(
        ScreeningWorkerSettings,
        env_file=None,
        metrics_enabled=True,
        metrics_bearer_token=SecretStr(TOKEN),
    )
    previous = metrics.enabled()
    metrics.configure(enabled=True)
    try:
        metrics.record_screening_provider_call("in-process-worker-probe", True)
        with worker_metrics_server(settings, host="127.0.0.1", port=0) as server:
            assert server is not None
            port = server.server_port
            url = f"http://127.0.0.1:{port}/metrics"
            with httpx.Client(timeout=5) as client:
                assert client.get(url).status_code == 401
                assert (
                    client.get(url, headers={"Authorization": "Bearer invalid"}).status_code == 401
                )
                auth = {"Authorization": f"Bearer {TOKEN}"}
                assert client.post(url, headers=auth).status_code == 404
                assert client.get(url + "/unexpected", headers=auth).status_code == 404
                response = client.get(url, headers=auth)
                assert response.status_code == 200
                assert 'provider="in-process-worker-probe"' in response.text
                assert "goatfarm_screening_provider_calls_total" in response.text
        # Server shutdown releases its listener, not just the serving thread.
        with socket.socket() as replacement:
            replacement.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            replacement.bind(("127.0.0.1", port))
    finally:
        metrics.configure(enabled=previous)


@pytest.mark.parametrize("disabled", [False, True])
def test_worker_metrics_without_collection_or_token_opens_no_listener(disabled: bool) -> None:
    from pydantic import SecretStr

    from app.core.config import ScreeningWorkerSettings
    from app.worker.metrics_server import worker_metrics_server

    from .settings_helpers import settings_from_input

    settings = settings_from_input(
        ScreeningWorkerSettings,
        env_file=None,
        metrics_enabled=not disabled,
        metrics_bearer_token=SecretStr(TOKEN) if disabled else None,
    )
    # Binding this invalid address would fail; both policy states return
    # without starting any listener at all.
    with worker_metrics_server(
        settings, host="not-a-valid-bind-address.invalid", port=-1
    ) as server:
        assert server is None
