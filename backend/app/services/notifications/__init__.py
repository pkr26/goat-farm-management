"""Notifications subsystem (ITEM 4, 2026-09-21 playbook)."""

from .hooks import emit_alert
from .providers import (
    ConsoleNotificationProvider,
    DeliveryResult,
    Msg91Provider,
    NotificationDeliveryError,
    NotificationProvider,
    build_notification_provider,
    redact_phone_numbers,
)
from .service import (
    ALERT_CLASSES,
    DigestSummary,
    farms_ready_for_digest,
    feed_reorder_daily,
    kidding_watch_daily,
    notify_alert_class,
    overdue_critical_sweep,
    run_digest_for_farm,
    send_notification,
)

__all__ = [
    "ALERT_CLASSES",
    "ConsoleNotificationProvider",
    "DeliveryResult",
    "DigestSummary",
    "Msg91Provider",
    "NotificationDeliveryError",
    "NotificationProvider",
    "build_notification_provider",
    "emit_alert",
    "farms_ready_for_digest",
    "feed_reorder_daily",
    "kidding_watch_daily",
    "notify_alert_class",
    "overdue_critical_sweep",
    "redact_phone_numbers",
    "run_digest_for_farm",
    "send_notification",
]
