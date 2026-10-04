"""PostgreSQL restart, exclusion and failure contracts for maintenance pages."""

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import main
from app.core.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import Animal, Farm, MaintenanceProgress, Task, User
from app.services import cadence
from app.services.maintenance_progress import (
    MaintenanceJob,
    MaintenancePage,
    run_maintenance_page,
)
from app.services.notifications.providers import ConsoleNotificationProvider, NotificationProvider
from app.utils import today

from .type_helpers import json_object

HOUR = datetime(2026, 10, 3, 12, tzinfo=UTC)
BACKEND = Path(__file__).resolve().parents[1]

# Each invocation is a genuinely fresh Python process/pool against the
# throwaway PostgreSQL database, including the abrupt process-death case.
_PROCESS_PAGE = """
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime

from app import main
from app.core.config import Settings
from app.db import get_engine, get_sessionmaker
from app.services.cadence import ensure_cadence_farm_batch
from app.services.maintenance_progress import MaintenancePage, run_maintenance_page
from app.services.notifications.providers import ConsoleNotificationProvider

async def run():
    mode = sys.argv[1]
    batch_size = int(sys.argv[2])
    async def work(cursor):
        if mode == 'skip':
            raise AssertionError('a competing scheduler entered the owned page')
        async with get_sessionmaker()() as db:
            processed, last_id = await ensure_cadence_farm_batch(
                db, batch_size=batch_size, after_farm_id=cursor
            )
            await db.commit()
        if mode == 'crash':
            print(json.dumps({'business_committed': processed}), flush=True)
            os._exit(91)
        return MaintenancePage(processed, last_id, processed < batch_size)

    if mode == 'alerts':
        result = await main._notification_alert_maintenance_page(
            Settings(environment='development', notifications_loop_batch_size=batch_size),
            ConsoleNotificationProvider(), datetime.fromisoformat(sys.argv[3])
        )
    else:
        async with get_sessionmaker()() as db:
            result = await run_maintenance_page(db, 'cadence', work)
    print(json.dumps(None if result is None else asdict(result)), flush=True)
    await get_engine().dispose()

asyncio.run(run())
"""


async def _child_page(
    mode: Literal["cadence", "alerts", "skip", "crash"], batch_size: int = 2
) -> tuple[int, object]:
    child_env = os.environ.copy()
    child_env["GOATFARM_DATABASE_URL"] = get_settings().database_url
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _PROCESS_PAGE,
        mode,
        str(batch_size),
        HOUR.isoformat(),
        cwd=BACKEND,
        env=child_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    assert process.returncode is not None
    assert process.returncode in {0, 91}, stderr.decode()
    result: object = json.loads(stdout.decode().splitlines()[-1])
    return process.returncode, result


async def _farms(count: int, *, with_animals: bool = False) -> list[int]:
    async with get_sessionmaker()() as db:
        owner = User(email="maintenance-owner@test.in", password_hash="not-an-auth-test")
        db.add(owner)
        await db.flush()
        farms = [
            Farm(name=f"Maintenance farm {index}", owner_id=owner.id) for index in range(count)
        ]
        db.add_all(farms)
        await db.flush()
        if with_animals:
            for farm in farms:
                db.add(
                    Animal(
                        farm_id=farm.id,
                        tag_number="durable-cadence-goat",
                        sex="F",
                        source="PURCHASED",
                        current_bucket="FOUNDATION",
                        date_of_birth=today(farm.timezone) - timedelta(days=365),
                        purchase_date=today(farm.timezone),
                    )
                )
        await db.commit()
        return [farm.id for farm in farms]


async def _saved(job: MaintenanceJob) -> tuple[int, datetime | None]:
    async with get_sessionmaker()() as db:
        progress = await db.get(MaintenanceProgress, job)
        assert progress is not None
        assert progress.updated_at.tzinfo is not None
        return progress.after_farm_id, progress.completed_hour


async def _page(
    job: MaintenanceJob,
    work: Callable[[int], Awaitable[MaintenancePage]],
    *,
    hour: datetime | None = None,
) -> MaintenancePage | None:
    async with get_sessionmaker()() as db:
        return await run_maintenance_page(db, job, work, sweep_hour=hour)


async def test_restarted_processes_reach_every_farm_beyond_tick_capacity() -> None:
    ids = await _farms(5, with_animals=True)
    # Three separate processes, each completing only one two-farm page.
    first = json_object((await _child_page("cadence"))[1])
    assert first == {"processed": 2, "after_farm_id": ids[1], "exhausted": False}
    assert await _saved("cadence") == (ids[1], None)
    second = json_object((await _child_page("cadence"))[1])
    assert second["after_farm_id"] == ids[3]
    assert await _saved("cadence") == (ids[3], None)
    third = json_object((await _child_page("cadence"))[1])
    assert third == {"processed": 1, "after_farm_id": ids[4], "exhausted": True}
    assert await _saved("cadence") == (0, None)
    async with get_sessionmaker()() as db:
        serviced = set(
            (await db.execute(select(Task.farm_id).where(Task.auto_generated).distinct())).scalars()
        )
    assert serviced == set(ids)


async def test_process_death_after_business_commit_keeps_old_cursor_and_retries_safely() -> None:
    ids = await _farms(2, with_animals=True)
    async with get_sessionmaker()() as db:
        db.add(MaintenanceProgress(job_name="cadence", after_farm_id=0))
        await db.commit()
    code, result = await _child_page("crash", batch_size=1)
    assert code == 91 and result == {"business_committed": 1}
    # The existing checkpoint, unlike the independent business fact, stays
    # at its last committed position after the process disappears.
    async with get_sessionmaker()() as db:
        progress = await db.get(MaintenanceProgress, "cadence")
        assert progress is not None and progress.after_farm_id == 0
        before = (
            await db.execute(select(func.count()).select_from(Task).where(Task.farm_id == ids[0]))
        ).scalar_one()
        assert before > 0
    _, result = await _child_page("cadence")
    assert json_object(result)["after_farm_id"] == ids[1]
    async with get_sessionmaker()() as db:
        after = (
            await db.execute(select(func.count()).select_from(Task).where(Task.farm_id == ids[0]))
        ).scalar_one()
        assert after == before
        assert (
            await db.execute(select(func.count()).select_from(Task).where(Task.farm_id == ids[1]))
        ).scalar_one() > 0


async def test_second_process_skips_owned_page_until_owner_commits() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held(cursor: int) -> MaintenancePage:
        assert cursor == 0
        entered.set()
        await release.wait()
        return MaintenancePage(1, 7, False)

    owner = asyncio.create_task(_page("cadence", held))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert await _child_page("skip") == (0, None)
    finally:
        release.set()
        await owner
    assert await _saved("cadence") == (7, None)

    async def next_page(cursor: int) -> MaintenancePage:
        assert cursor == 7
        return MaintenancePage(1, 8, False)

    assert await _page("cadence", next_page) == MaintenancePage(1, 8, False)


async def test_independent_jobs_can_advance_while_cadence_page_is_owned() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held(cursor: int) -> MaintenancePage:
        entered.set()
        await release.wait()
        return MaintenancePage(1, cursor + 1, False)

    owner = asyncio.create_task(_page("cadence", held))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert json_object((await _child_page("alerts"))[1])["exhausted"] is True
        assert await _saved("notification_alerts") == (0, HOUR)
    finally:
        release.set()
        await owner


async def test_row_lock_is_skipped_without_entering_work() -> None:
    async with get_sessionmaker()() as db:
        db.add(MaintenanceProgress(job_name="cadence", after_farm_id=4))
        await db.commit()

    async def forbidden(_cursor: int) -> MaintenancePage:
        pytest.fail("a locked checkpoint must not enter its page")

    async with get_sessionmaker()() as owner:
        await owner.execute(
            select(MaintenanceProgress)
            .where(MaintenanceProgress.job_name == "cadence")
            .with_for_update()
        )
        assert await asyncio.wait_for(_page("cadence", forbidden), timeout=3) is None
        await owner.rollback()
    assert await _saved("cadence") == (4, None)


async def test_alert_restart_only_marks_hour_complete_after_exhaustion() -> None:
    ids = await _farms(5)
    for cursor in [ids[1], ids[3]]:
        result = json_object((await _child_page("alerts"))[1])
        assert result["after_farm_id"] == cursor and result["exhausted"] is False
        assert await _saved("notification_alerts") == (cursor, None)
    result = json_object((await _child_page("alerts"))[1])
    assert result == {"processed": 1, "after_farm_id": ids[4], "exhausted": True}
    assert await _saved("notification_alerts") == (0, HOUR)
    assert await _child_page("alerts") == (0, None)


async def test_alert_new_hour_preserves_incomplete_rotation_and_previous_completion() -> None:
    async def first(_cursor: int) -> MaintenancePage:
        return MaintenancePage(1, 3, True)

    async def next_page(cursor: int) -> MaintenancePage:
        assert cursor == 0
        return MaintenancePage(2, 8, False)

    async def last_page(cursor: int) -> MaintenancePage:
        assert cursor == 8
        return MaintenancePage(1, 12, True)

    await _page("notification_alerts", first, hour=HOUR)
    await _page("notification_alerts", next_page, hour=HOUR + timedelta(hours=1))
    assert await _saved("notification_alerts") == (8, HOUR)
    await _page("notification_alerts", last_page, hour=HOUR + timedelta(hours=2))
    assert await _saved("notification_alerts") == (0, HOUR + timedelta(hours=2))


async def test_cadence_loop_restart_and_poison_wrap_preserve_fairness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _farms(3)
    visited: list[int] = []
    ticks = 0

    async def farm_work(_db: AsyncSession, farm: Farm) -> None:
        visited.append(farm.id)
        if farm.id == ids[0]:
            raise RuntimeError("poison farm is retried on wrap")

    async def one_tick(_seconds: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks % 2 == 0:
            raise asyncio.CancelledError

    monkeypatch.setattr(cadence, "ensure_cadence_tasks", farm_work)
    monkeypatch.setattr(asyncio, "sleep", one_tick)
    # Restart the actual loop before every page. Even a failed first farm
    # cannot pin the saved cursor or prevent the third farm getting service.
    for cursor in [ids[0], ids[1], ids[2], 0, ids[0]]:
        with pytest.raises(asyncio.CancelledError):
            await main._cadence_materialization_loop(1, 1, 1)
        assert await _saved("cadence") == (cursor, None)
    assert visited == [*ids, ids[0]]


async def test_alert_poison_is_retried_after_durable_rotation_wrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _farms(3)
    visited: list[int] = []

    async def alert(
        _db: AsyncSession, _settings: Settings, _provider: NotificationProvider, farm: Farm
    ) -> int:
        visited.append(farm.id)
        if farm.id == ids[0]:
            raise RuntimeError("isolated poison farm")
        return 0

    async def no_op(
        _db: AsyncSession, _settings: Settings, _provider: NotificationProvider, _farm: Farm
    ) -> int:
        return 0

    import app.services.notifications as notifications

    monkeypatch.setattr(notifications, "overdue_critical_sweep", alert)
    monkeypatch.setattr(notifications, "kidding_watch_daily", no_op)
    monkeypatch.setattr(notifications, "feed_reorder_daily", no_op)
    settings = Settings(environment="development", notifications_loop_batch_size=2)
    provider = ConsoleNotificationProvider()
    await main._notification_alert_maintenance_page(settings, provider, HOUR)
    assert await _saved("notification_alerts") == (ids[1], None)
    await main._notification_alert_maintenance_page(settings, provider, HOUR)
    assert await _saved("notification_alerts") == (0, HOUR)
    assert await main._notification_alert_maintenance_page(settings, provider, HOUR) is None
    await main._notification_alert_maintenance_page(settings, provider, HOUR + timedelta(hours=1))
    assert sorted(visited[:2]) == ids[:2]
    assert visited[2] == ids[2]
    assert sorted(visited[3:]) == ids[:2]


async def test_notification_alert_schedule_restarts_resume_hour_and_skip_completed_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _farms(3)
    settings = Settings(
        environment="development", notifications_enabled=True, notifications_loop_batch_size=2
    )
    provider = ConsoleNotificationProvider()
    cursors: list[int] = []
    original_batch = main._notification_alert_farm_batch

    async def alert_batch(
        config: Settings, sink: NotificationProvider, after_farm_id: int
    ) -> tuple[int, int, bool]:
        cursors.append(after_farm_id)
        return await original_batch(config, sink, after_farm_id)

    async def stop_after_tick(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(main, "utcnow", lambda: HOUR.replace(tzinfo=None))
    monkeypatch.setattr(main, "_notification_alert_farm_batch", alert_batch)
    monkeypatch.setattr(asyncio, "sleep", stop_after_tick)
    for saved in [(ids[1], None), (0, HOUR), (0, HOUR)]:
        with pytest.raises(asyncio.CancelledError):
            await main._notification_periodic_alert_loop(settings, provider)
        assert await _saved("notification_alerts") == saved
    assert cursors == [0, ids[1]]


async def test_cancellation_rolls_back_checkpoint_and_releases_owner() -> None:
    async def previous(_cursor: int) -> MaintenancePage:
        return MaintenancePage(1, 4, False)

    await _page("cadence", previous)

    async def cancelled(cursor: int) -> MaintenancePage:
        assert cursor == 4
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _page("cadence", cancelled)
    assert await _saved("cadence") == (4, None)

    async def resumed(cursor: int) -> MaintenancePage:
        return MaintenancePage(1, cursor + 1, False)

    assert await _page("cadence", resumed) == MaintenancePage(1, 5, False)


async def test_failed_checkpoint_commit_keeps_business_work_and_old_cursor() -> None:
    ids = await _farms(2, with_animals=True)
    async with get_sessionmaker()() as db:
        db.add(MaintenanceProgress(job_name="cadence", after_farm_id=0))
        await db.execute(
            text(
                "CREATE FUNCTION reject_checkpoint_test() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'injected checkpoint commit failure'; END $$"
            )
        )
        await db.execute(
            text(
                "CREATE CONSTRAINT TRIGGER reject_checkpoint_test AFTER UPDATE "
                "ON maintenance_progress DEFERRABLE INITIALLY DEFERRED "
                "FOR EACH ROW EXECUTE FUNCTION reject_checkpoint_test()"
            )
        )
        await db.commit()

    async def actual_work(cursor: int) -> MaintenancePage:
        async with get_sessionmaker()() as db:
            processed, last_id = await cadence.ensure_cadence_farm_batch(
                db, batch_size=1, after_farm_id=cursor
            )
            await db.commit()
        return MaintenancePage(processed, last_id, False)

    try:
        with pytest.raises(DBAPIError, match="injected checkpoint commit failure"):
            await _page("cadence", actual_work)
        assert await _saved("cadence") == (0, None)
        async with get_sessionmaker()() as db:
            before = (
                await db.execute(
                    select(func.count()).select_from(Task).where(Task.farm_id == ids[0])
                )
            ).scalar_one()
            assert before > 0
    finally:
        async with get_sessionmaker()() as db:
            await db.execute(text("DROP TRIGGER reject_checkpoint_test ON maintenance_progress"))
            await db.execute(text("DROP FUNCTION reject_checkpoint_test()"))
            await db.commit()
    assert await _page("cadence", actual_work) == MaintenancePage(1, ids[0], False)
    async with get_sessionmaker()() as db:
        after = (
            await db.execute(select(func.count()).select_from(Task).where(Task.farm_id == ids[0]))
        ).scalar_one()
    assert after == before


@pytest.mark.parametrize(
    "page",
    [
        MaintenancePage(-1, 5, False),
        MaintenancePage(1, 3, True),
        MaintenancePage(0, 5, False),
        MaintenancePage(1, 4, False),
    ],
)
async def test_invalid_work_results_cannot_advance_checkpoint(page: MaintenancePage) -> None:
    async with get_sessionmaker()() as db:
        db.add(MaintenanceProgress(job_name="cadence", after_farm_id=4))
        await db.commit()

    async def invalid(_cursor: int) -> MaintenancePage:
        return page

    with pytest.raises(ValueError):
        await _page("cadence", invalid)
    assert await _saved("cadence") == (4, None)


@pytest.mark.parametrize(
    ("job", "hour"),
    [
        ("cadence", HOUR),
        ("notification_alerts", None),
        ("notification_alerts", HOUR.replace(tzinfo=None)),
        ("notification_alerts", HOUR + timedelta(minutes=1)),
    ],
)
async def test_invalid_schedule_parameters_fail_before_entering_work(
    job: MaintenanceJob, hour: datetime | None
) -> None:
    async def forbidden(_cursor: int) -> MaintenancePage:
        pytest.fail("invalid schedule parameters entered work")

    with pytest.raises(ValueError):
        await _page(job, forbidden, hour=hour)


@pytest.mark.parametrize(("job", "cursor"), [("unknown", 0), ("cadence", -1)])
async def test_database_rejects_unknown_jobs_and_negative_positions(job: str, cursor: int) -> None:
    async with get_sessionmaker()() as db:
        db.add(MaintenanceProgress(job_name=job, after_farm_id=cursor))
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
