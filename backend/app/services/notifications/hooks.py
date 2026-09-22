"""Call-site alert hooks (ITEM 4, 2026-09-21 playbook).

``emit_alert`` is the one function API/service layers call after their own
commit: it opens its OWN session (the caller's transaction is closed), checks
the feature flag, and fans the alert out. Never raises into the caller — a
notification failure must not fail the domain write that triggered it.
"""

from __future__ import annotations

import logging

from ...core.config import get_settings
from ...db import get_sessionmaker
from ...models import Farm
from .providers import build_notification_provider
from .service import notify_alert_class

logger = logging.getLogger(__name__)


async def emit_alert(farm_id: int, alert_class: str, message: str, payload: str) -> None:
    settings = get_settings()
    if not settings.notifications_enabled:
        return
    try:
        provider = build_notification_provider(settings)
        async with get_sessionmaker()() as db:
            farm = await db.get(Farm, farm_id)
            if farm is None:
                return
            await notify_alert_class(
                db,
                settings,
                provider,
                farm=farm,
                alert_class=alert_class,
                message=message,
                payload=payload,
            )
    except Exception:
        # Deliberately broad: the alert is best-effort after a committed
        # domain write; its failure is logged, never propagated.
        logger.exception("notification alert %s for farm %s failed", alert_class, farm_id)
