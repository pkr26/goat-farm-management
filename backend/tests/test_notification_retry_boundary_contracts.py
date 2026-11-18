"""Notification workers release owned resources and report exhausted farm pages."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app import main as main_module
from app.core.config import Settings
from app.models import Farm
from app.services import notifications
from app.services.notifications.providers import DeliveryResult, NotificationProvider

from .conftest import owner_with_farm
from .test_notifications import RecordingProvider


async def test_disabled_notifications_do_not_build_or_wait_for_a_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_provider(_settings: Settings) -> NotificationProvider:
        pytest.fail("A disabled deployment must not create an outbound notification transport")

    async def unexpected_sleep(_delay: float) -> None:
        pytest.fail("Disabled notification workers must finish immediately")

    monkeypatch.setattr(main_module, "get_settings", lambda: Settings(notifications_enabled=False))
    monkeypatch.setattr(notifications, "build_notification_provider", unexpected_provider)
    monkeypatch.setattr(main_module, "asyncio", SimpleNamespace(sleep=unexpected_sleep))
    await main_module._notifications_loop()


async def test_notification_retry_and_shutdown_cancel_children_before_closing_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    started = asyncio.Event()
    active: set[str] = set()
    builds = 0

    class OwnedProvider:
        name = "owned-test-transport"

        async def send_sms(self, phone: str, message: str) -> DeliveryResult:
            pytest.fail("Lifecycle validation must not send a paid message")

        async def aclose(self) -> None:
            assert not active, "Every child must stop using the transport before it is closed"
            events.append("transport-closed")

    provider = OwnedProvider()

    def build(_settings: Settings) -> NotificationProvider:
        nonlocal builds
        builds += 1
        if builds == 1:
            raise RuntimeError("temporary provider configuration failure")
        return provider

    async def next_tick(_delay: float) -> None:
        await asyncio.sleep(0)

    async def child(name: str, _settings: Settings, _provider: NotificationProvider) -> None:
        assert _provider is provider
        active.add(name)
        if len(active) == 3:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(name)
            events.append(f"stopped-{name}")

    async def outbox(settings: Settings, transport: NotificationProvider) -> None:
        await child("outbox", settings, transport)

    async def digest(settings: Settings, transport: NotificationProvider) -> None:
        await child("digest", settings, transport)

    async def alerts(settings: Settings, transport: NotificationProvider) -> None:
        await child("alerts", settings, transport)

    monkeypatch.setattr(main_module, "get_settings", lambda: Settings(notifications_enabled=True))
    monkeypatch.setattr(notifications, "build_notification_provider", build)
    monkeypatch.setattr(main_module, "_notification_outbox_loop", outbox)
    monkeypatch.setattr(main_module, "_notification_digest_loop", digest)
    monkeypatch.setattr(main_module, "_notification_periodic_alert_loop", alerts)
    monkeypatch.setattr(
        main_module, "asyncio", SimpleNamespace(sleep=next_tick, gather=asyncio.gather)
    )
    worker = asyncio.create_task(main_module._notifications_loop())
    ready = asyncio.create_task(started.wait())
    try:
        await asyncio.wait({worker, ready}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        assert started.is_set(), "The enabled worker must start every independently scheduled class"
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
    finally:
        ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert builds == 2, "The provider build failure must be retried without losing the worker"
    assert set(events[:3]) == {"stopped-outbox", "stopped-digest", "stopped-alerts"}
    assert events[3:] == ["transport-closed"]


@pytest.mark.parametrize("farm_count", [2, 3], ids=["exact-page", "lookahead-page"])
async def test_alert_farm_page_reports_exhaustion_at_the_configured_boundary(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, farm_count: int
) -> None:
    farm_ids = []
    for index in range(farm_count):
        owner = await owner_with_farm(client, email=f"alert-page-{index}@farm.in")
        farm_ids.append(int(owner["X-Farm-Id"]))
    visits: list[tuple[str, int]] = []

    async def overdue(
        _db: AsyncSession, _settings: Settings, _provider: NotificationProvider, farm: Farm
    ) -> None:
        visits.append(("overdue", farm.id))

    async def kidding(
        _db: AsyncSession, _settings: Settings, _provider: NotificationProvider, farm: Farm
    ) -> None:
        visits.append(("kidding", farm.id))

    async def feed(
        _db: AsyncSession, _settings: Settings, _provider: NotificationProvider, farm: Farm
    ) -> None:
        visits.append(("feed", farm.id))

    monkeypatch.setattr(notifications, "overdue_critical_sweep", overdue)
    monkeypatch.setattr(notifications, "kidding_watch_daily", kidding)
    monkeypatch.setattr(notifications, "feed_reorder_daily", feed)
    settings = Settings(notifications_enabled=True, notifications_loop_batch_size=2)
    count, cursor, exhausted = await main_module._notification_alert_farm_batch(
        settings, RecordingProvider(), after_farm_id=0
    )
    assert (count, cursor, exhausted) == (2, farm_ids[1], farm_count == 2)
    assert set(visits) == {
        (kind, farm_id) for kind in ("overdue", "kidding", "feed") for farm_id in farm_ids[:2]
    }
