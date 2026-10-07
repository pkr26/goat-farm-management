"""Native fanout settles peers and reports actual valid-class/overdue deliveries.

The fault scenario delegates to the real MSG91 adapter through a declared
httpx transport returning one malformed HTTP-success body. It exercises a
vendor-response failure, not a claim about conforming remote MSG91 responses.
"""

import asyncio
import json
from datetime import timedelta
from typing import Literal

import httpx
import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import NotificationLog, Task
from app.services.notifications import (
    notify_alert_class,
    overdue_critical_sweep,
    run_digest_for_farm,
)
from app.services.notifications.providers import DeliveryResult, Msg91Provider
from app.utils import today

from .conftest import owner_with_farm
from .test_mutation38_notification_native_cycle_contracts import _always_open_settings
from .test_notifications import RecordingProvider, _farm, _membership_id, _recipient


async def test_valid_native_fanout_returns_one_fresh_delivery(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="native-valid-alert-fanout@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    provider = RecordingProvider()
    async with get_sessionmaker()() as worker:
        await _recipient(worker, farm_id, membership_id)
        farm = await _farm(worker, farm_id)
        try:
            digest = await run_digest_for_farm(worker, _always_open_settings(), provider, farm)
            alert = await notify_alert_class(
                worker,
                _always_open_settings(),
                provider,
                farm=farm,
                alert_class="SCREENING_FLAG",
                message="A confirmed screening finding needs review.",
                payload="native-valid-confirmed-finding",
            )
        except (TypeError, ValueError) as exc:
            pytest.fail(f"Native supported digest and alert classes must complete: {exc}")
    assert (digest.sent, digest.skipped, alert) == (1, 0, 1)
    assert len(provider.sent) == 2
    async with get_sessionmaker()() as observer:
        logs = list((await observer.execute(select(NotificationLog))).scalars())
        assert {(row.alert_class, row.status) for row in logs} == {
            ("DAILY_DIGEST", "SENT"),
            ("SCREENING_FLAG", "SENT"),
        }


async def test_native_critical_sweep_counts_only_this_farms_pending_old_duties(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-critical-target@farm.in")
    other = await owner_with_farm(client, email="native-critical-other@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    other_id = int(other["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    provider = RecordingProvider()
    async with get_sessionmaker()() as worker:
        farm = await _farm(worker, farm_id)
        reference = today(farm.timezone)
        oldest = reference - timedelta(days=4)
        await _recipient(worker, farm_id, membership_id)
        worker.add_all(
            [
                Task(farm_id=farm_id, title="Old pending", due_date=oldest, category="OTHER"),
                Task(
                    farm_id=farm_id,
                    title="Recently overdue",
                    due_date=reference - timedelta(days=1),
                    category="OTHER",
                ),
                Task(
                    farm_id=other_id,
                    title="Another farm's old duty",
                    due_date=reference - timedelta(days=12),
                    category="OTHER",
                ),
            ]
        )
        await worker.commit()
        try:
            sent = await overdue_critical_sweep(worker, _always_open_settings(), provider, farm)
        except (AttributeError, TypeError) as exc:
            pytest.fail(f"A native nonempty pending overdue pile must produce a valid alert: {exc}")
    assert sent == 1, "one opted recipient must receive the one actual qualifying overdue pile"
    assert len(provider.sent) == 1
    assert provider.sent[0][1] == (
        f"Herdly: 1 duties are 3+ days overdue (oldest {oldest.isoformat()}). Please clear them."
    )
    async with get_sessionmaker()() as observer:
        log = (await observer.execute(select(NotificationLog))).scalar_one()
        assert log.farm_id == farm_id and log.alert_class == "OVERDUE_CRITICAL"
        assert log.status == "SENT"


class _ObservedMsg91Provider(Msg91Provider):
    """Observe actual child tasks while delegating all adapter results unchanged."""

    def __init__(self, client: httpx.AsyncClient, bad_phone: str) -> None:
        super().__init__("native-test-auth", "HERDLY", client=client)
        self.bad_phone = bad_phone
        self.tasks: dict[str, asyncio.Task[object]] = {}
        self.bad_child_finished = asyncio.Event()

    async def send_sms(self, phone: str, message: str) -> DeliveryResult:
        child = asyncio.current_task()
        assert child is not None
        self.tasks[phone] = child
        if phone == self.bad_phone:
            child.add_done_callback(lambda _: self.bad_child_finished.set())
        return await super().send_sms(phone, message)


@pytest.mark.parametrize("kind", ["digest", "alert"])
async def test_native_vendor_body_failure_waits_for_successful_recipient_settlement(
    client: httpx.AsyncClient, kind: Literal["digest", "alert"]
) -> None:
    owner = await owner_with_farm(client, email="native-peer-fanout@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    bad_membership = await _membership_id(client, owner)
    good_membership = await _membership_id(client, owner)
    bad_phone, good_phone = "+919999999991", "+919999999992"
    good_request_entered, release_good_request = asyncio.Event(), asyncio.Event()

    async def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        phone = str(body["recipients"][0]["mobiles"])
        if phone == bad_phone:
            await good_request_entered.wait()
            return httpx.Response(200, content=b"malformed vendor JSON", request=request)
        assert phone == good_phone
        good_request_entered.set()
        await release_good_request.wait()
        return httpx.Response(
            200, json={"type": "success", "message": "native-peer-accepted"}, request=request
        )

    async with get_sessionmaker()() as setup:
        bad_recipient = await _recipient(setup, farm_id, bad_membership, bad_phone)
        good_recipient = await _recipient(setup, farm_id, good_membership, good_phone)
        bad_id, good_id = bad_recipient.id, good_recipient.id
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as sms_client:
        provider = _ObservedMsg91Provider(sms_client, bad_phone)
        peer_finished_when_caller_finished: list[bool] = []
        surfaced_vendor_failure: list[bool] = []

        async def caller() -> None:
            async with get_sessionmaker()() as worker:
                farm = await _farm(worker, farm_id)
                try:
                    if kind == "digest":
                        summary = await run_digest_for_farm(
                            worker, _always_open_settings(), provider, farm
                        )
                        assert (summary.sent, summary.skipped) == (1, 1)
                    else:
                        sent = await notify_alert_class(
                            worker,
                            _always_open_settings(),
                            provider,
                            farm=farm,
                            alert_class="SCREENING_FLAG",
                            message="Native peer failure fanout",
                            payload="native-peer-failure",
                        )
                        assert sent == 1
                    surfaced_vendor_failure.append(False)
                except json.JSONDecodeError:
                    surfaced_vendor_failure.append(True)
                finally:
                    peer = provider.tasks.get(good_phone)
                    peer_finished_when_caller_finished.append(peer is not None and peer.done())

        parent = asyncio.create_task(caller())
        try:
            await asyncio.wait_for(good_request_entered.wait(), timeout=20)
            await asyncio.wait_for(provider.bad_child_finished.wait(), timeout=20)
        finally:
            release_good_request.set()
            results = await asyncio.gather(parent, *provider.tasks.values(), return_exceptions=True)
        assert results[0] is None, (
            f"Fanout must truthfully deliver or surface its fault: {results[0]}"
        )
        assert peer_finished_when_caller_finished == [True], (
            "fanout must await the independently successful recipient's committed settlement "
            "before returning or propagating a peer failure"
        )
        async with get_sessionmaker()() as observer:
            logs = list((await observer.execute(select(NotificationLog))).scalars())
            assert len(logs) == 2
            good = next(row for row in logs if row.recipient_id == good_id)
            bad = next(row for row in logs if row.recipient_id == bad_id)
            assert good.status == "SENT" and good.provider_message_id == "native-peer-accepted"
            assert surfaced_vendor_failure == [True] or bad.status == "FAILED", (
                "an unsuccessful adapter call must be reported or durably settled as failed"
            )
