"""Call-site alert hooks.

Clinical mutations enqueue their alert in the domain transaction. After
commit, ``emit_alert`` attempts that durable event immediately; the minute
worker resumes any deferred or interrupted fan-out. Hook failures never
raise into the caller of an already committed domain write.
"""

from __future__ import annotations

import logging
from contextlib import suppress

from ...core.config import get_settings
from ...db import get_sessionmaker
from .outbox import dispatch_outbox_event, enqueue_alert
from .providers import NotificationProvider, build_notification_provider

logger = logging.getLogger(__name__)


async def emit_alert(farm_id: int, alert_class: str, message: str, payload: str) -> None:
    settings = get_settings()
    if not settings.notifications_enabled:
        return
    provider: NotificationProvider | None = None
    try:
        async with get_sessionmaker()() as db:
            event_id = await enqueue_alert(db, farm_id, alert_class, message, payload)
            await db.commit()
        provider = build_notification_provider(settings)
        if event_id is not None:
            await dispatch_outbox_event(event_id, settings, provider)
    except Exception:
        # Deliberately broad: the alert is best-effort after a committed
        # domain write; its failure is logged, never propagated.
        logger.exception("notification alert %s for farm %s failed", alert_class, farm_id)
    finally:
        # One provider per alert must not leak its httpx transport — close the owned client like the
        # digest loop does at shutdown. Providers without a transport (console, test doubles) carry
        # no aclose and are skipped.
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            with suppress(Exception):
                await aclose()
