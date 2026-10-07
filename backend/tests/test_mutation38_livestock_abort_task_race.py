"""A recorded pregnancy loss preserves a concurrent real kit preparation."""

import asyncio

import httpx
from sqlalchemy import select, text

from app.db import get_sessionmaker
from app.models import BreedingRecord, Farm, Task, User
from app.services.tasks import complete_task
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import pregnant_doe


async def test_recorded_loss_preserves_committed_birthing_kit_completion(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    _doe, _buck, breeding = await pregnant_doe(client, owner, gestation_days=144)
    abort: asyncio.Task[httpx.Response] | None = None
    async with get_sessionmaker()() as completer:
        farm = await completer.get(Farm, int(owner["X-Farm-Id"]))
        assert farm is not None
        user = await completer.get(User, farm.owner_id)
        assert user is not None
        kit = (
            await completer.execute(
                select(Task)
                .where(
                    Task.breeding_record_id == breeding["id"],
                    Task.category == "BIRTHING_KIT",
                )
                .with_for_update()
            )
        ).scalar_one()
        assert kit.status == "PENDING" and kit.due_date <= today()
        # This exported nonmovement helper needs no Animal lock. Preparing
        # a due physical kit is coherent while the confirmed pregnancy is
        # still live; it does not fabricate an ultrasound or clinical fact.
        await complete_task(completer, kit, user)
        assert kit.status == "DONE" and kit.completed_at is not None
        kit_id, completion_at = kit.id, kit.completed_at
        completer_pid = await completer.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(completer_pid, int)
        abort = asyncio.create_task(
            client.post(
                f"/api/breeding/{breeding['id']}/abort",
                json={"loss_date": today().isoformat(), "cause": "UNKNOWN"},
                headers=owner,
            )
        )
        try:
            for _ in range(1000):
                async with get_sessionmaker()() as observer:
                    blocked = await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND :completer_pid = ANY(pg_blocking_pids(pid)))"
                        ),
                        {"completer_pid": completer_pid},
                    )
                if blocked:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("Recorded loss never waited for the actual kit completion")
            await completer.commit()
            response = await abort
        finally:
            if not abort.done():
                abort.cancel()
            await asyncio.gather(abort, return_exceptions=True)
    assert response.status_code == 200, response.text
    async with get_sessionmaker()() as observer:
        recorded = await observer.get(BreedingRecord, breeding["id"])
        stored_kit = await observer.get(Task, kit_id)
        assert recorded is not None and recorded.outcome == "ABORTED"
        assert stored_kit is not None and stored_kit.status == "DONE"
        assert stored_kit.completed_by_id == user.id and stored_kit.completed_at == completion_at
        assert (
            stored_kit.skipped_at is None
            and stored_kit.skipped_by_id is None
            and stored_kit.skip_reason is None
        )
