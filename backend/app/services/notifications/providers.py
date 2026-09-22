"""Notification delivery providers (ITEM 4, 2026-09-21 playbook).

One seam, two implementations today: a console/log provider (dev and the
default when notifications are enabled without MSG91 outside production) and
an MSG91 SMS adapter. Adapters translate transport only — dedupe, caps and
quiet hours live in ``service.py`` so every provider shares them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from ...core.config import Settings

logger = logging.getLogger(__name__)

MSG91_SEND_URL = "https://control.msg91.com/api/v5/flow/"
MSG91_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class DeliveryResult:
    """One attempted delivery. ``message_id`` present exactly on success."""

    ok: bool
    message_id: str | None = None
    error: str | None = None


class NotificationDeliveryError(Exception):
    """Transport-level failure; the caller records it in the log and moves on."""


class NotificationProvider(Protocol):
    name: str

    async def send_sms(self, phone: str, message: str) -> DeliveryResult: ...


class ConsoleNotificationProvider:
    """Logs instead of sending — the dev default and the test double."""

    name = "console"

    async def send_sms(self, phone: str, message: str) -> DeliveryResult:
        logger.info("notification (console) to=%s text=%r", phone, message)
        return DeliveryResult(ok=True, message_id="console")


class Msg91Provider:
    """MSG91 SMS over its flow API. One template-free text per send.

    ``template_id`` (optional) selects a registered DLT template instead of
    the template-free ``dlt_manual`` route.
    """

    name = "msg91"

    def __init__(self, auth_key: str, sender_id: str, template_id: str | None = None) -> None:
        self._auth_key = auth_key
        self._sender_id = sender_id
        self._template_id = template_id

    async def send_sms(self, phone: str, message: str) -> DeliveryResult:
        body: dict[str, object] = {
            "sender": self._sender_id,
            "route": "dlt_manual",
            "recipients": [{"mobiles": phone, "MESSAGE": message}],
        }
        if self._template_id:
            body["template_id"] = self._template_id
        try:
            async with httpx.AsyncClient(timeout=MSG91_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    MSG91_SEND_URL,
                    headers={"authkey": self._auth_key},
                    json=body,
                )
            response.raise_for_status()
            payload = response.json()
            # MSG91 answers {"type": "success", "message": "..."} on accept.
            if str(payload.get("type", "")).lower() != "success":
                return DeliveryResult(ok=False, error=str(payload)[:500])
            return DeliveryResult(ok=True, message_id=str(payload.get("message", ""))[:64])
        except httpx.HTTPError as exc:
            raise NotificationDeliveryError(str(exc)) from exc


def build_notification_provider(settings: Settings) -> NotificationProvider:
    if settings.notifications_provider == "msg91":
        auth_key = settings.msg91_auth_key
        if auth_key is None:
            raise ValueError("notifications_provider=msg91 requires GOATFARM_MSG91_AUTH_KEY")
        return Msg91Provider(
            auth_key.get_secret_value(),
            settings.msg91_sender_id,
            settings.msg91_template_id,
        )
    return ConsoleNotificationProvider()
