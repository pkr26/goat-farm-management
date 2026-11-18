"""Real legacy queues and tenant retention make bounded committed progress."""

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import main as main_module
from app import metrics
from app import seed as seed_module
from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import Farm, FeedInventory, Role, Task, TaskCategory, TaskStatus, User
from app.permissions import preset_codes
from app.seed import FARM_INGREDIENTS, seed_default_roles
from app.services.retention import RetentionSummary, run_retention_sweep
from app.utils import utcnow

from .conftest import owner_with_farm


class _Intervals:
    """Expose completed iterations; only the test releases the next tick."""

    def __init__(self) -> None:
        self.arrivals: asyncio.Queue[tuple[int, float]] = asyncio.Queue()
        self.releases: list[asyncio.Event] = []

    async def sleep(self, delay: float) -> None:
        gate = asyncio.Event()
        self.releases.append(gate)
        self.arrivals.put_nowait((len(self.releases), delay))
        await gate.wait()

    async def reached(self, worker: asyncio.Task[None], tick: int, delay: int) -> None:
        boundary = asyncio.create_task(self.arrivals.get())
        try:
            await asyncio.wait({boundary, worker}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
            assert boundary.done(), "Maintenance must complete a finite iteration before yielding"
            assert not worker.done(), "Maintenance must remain available for the next interval"
            assert boundary.result() == (tick, delay)
        finally:
            if not boundary.done():
                boundary.cancel()
            await asyncio.gather(boundary, return_exceptions=True)

    def release(self, tick: int) -> None:
        self.releases[tick - 1].set()


async def _legacy_rows(farm_count: int, task_count: int) -> tuple[list[int], list[int], int, int]:
    """The same pre-default/pre-assignment historical shape as test_ops."""
    async with get_sessionmaker()() as db:
        owner = User(email="legacy-driver-owner@farm.in", password_hash="argon2-placeholder")
        db.add(owner)
        await db.flush()
        farms = [
            Farm(name=f"Historical farm {index}", owner_id=owner.id) for index in range(farm_count)
        ]
        db.add_all(farms)
        await db.flush()
        # A half-seeded historical tenant has real editable roles but still
        # lacks the default inventory. Repair must preserve its edited label.
        await seed_default_roles(db, farms[0].id)
        cleaner = (
            await db.execute(
                select(Role).where(Role.farm_id == farms[0].id, Role.code == "CLEANER")
            )
        ).scalar_one()
        cleaner.name = "Tenant-edited caretaker"
        old = utcnow().date() - timedelta(days=10)
        tasks = [
            Task(
                farm_id=farms[0].id,
                title=f"Historical generated cleaning {index}",
                due_date=old,
                category=TaskCategory.CLEANING.value,
                auto_generated=True,
            )
            for index in range(task_count)
        ]
        manual = Task(
            farm_id=farms[0].id,
            title="Operator's unassigned cleaning",
            due_date=old,
            category=TaskCategory.CLEANING.value,
            auto_generated=False,
        )
        db.add_all([*tasks, manual])
        await db.commit()
        return [farm.id for farm in farms], [task.id for task in tasks], cleaner.id, manual.id


async def _legacy_state(
    farms: list[int], tasks: list[int], edited_role: int, manual_task: int
) -> tuple[set[int], set[int]]:
    completed = set()
    async with get_sessionmaker()() as db:
        for farm_id in farms:
            codes = set(
                (await db.execute(select(Role.code).where(Role.farm_id == farm_id))).scalars()
            )
            ingredients = set(
                (
                    await db.execute(
                        select(FeedInventory.ingredient).where(FeedInventory.farm_id == farm_id)
                    )
                ).scalars()
            )
            if codes == preset_codes() and ingredients == {name for name, _ in FARM_INGREDIENTS}:
                completed.add(farm_id)
        assigned = set(
            (
                await db.execute(
                    select(Task.id).where(Task.id.in_(tasks), Task.assigned_role_id.is_not(None))
                )
            ).scalars()
        )
        preserved = await db.get(Role, edited_role)
        untouched = await db.get(Task, manual_task)
        assert preserved is not None and preserved.name == "Tenant-edited caretaker"
        assert untouched is not None and untouched.assigned_role_id is None
    return completed, assigned


@pytest.mark.parametrize(("farm_count", "task_count"), [(5, 1), (1, 5)])
async def test_legacy_driver_drains_both_queues_and_yields_with_committed_totals(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    farm_count: int,
    task_count: int,
) -> None:
    farms, tasks, edited_role, manual_task = await _legacy_rows(farm_count, task_count)
    intervals = _Intervals()
    batches: list[tuple[int, int]] = []
    measured: list[int] = []
    farm_phase_probes: list[int] = []
    failed_transactions = 0
    real_repair = seed_module.repair_legacy_data_batch
    real_backfill = seed_module.backfill_task_assignments_batch
    real_metric = metrics.record_maintenance_batch

    async def backfill(db: AsyncSession, *, batch_size: int) -> int:
        # A real task phase must not retain the farm phase's inverse lock
        # edge. A separate session can key-share every historical farm NOWAIT.
        async with get_sessionmaker()() as probe:
            rows = list(
                (
                    await probe.execute(
                        select(Farm.id)
                        .where(Farm.id.in_(farms))
                        .with_for_update(read=True, key_share=True, nowait=True)
                    )
                ).scalars()
            )
            farm_phase_probes.append(len(rows))
            await probe.rollback()
        return await real_backfill(db, batch_size=batch_size)

    async def repair(
        db: AsyncSession, *, farm_batch_size: int, task_batch_size: int
    ) -> tuple[int, int]:
        nonlocal failed_transactions
        if failed_transactions == 0:
            # Abort this owned PostgreSQL transaction once. The real driver
            # must unwind the session and remain available for a healthy tick.
            failed_transactions += 1
            await db.execute(text("SELECT 1 / 0"))
        result = await real_repair(
            db, farm_batch_size=farm_batch_size, task_batch_size=task_batch_size
        )
        batches.append(result)
        return result

    def metric(operation: str, count: int) -> None:
        if operation == "legacy_repair":
            measured.append(count)
        real_metric(operation, count)

    caplog.set_level(logging.INFO, logger="goatfarm")
    monkeypatch.setattr(seed_module, "backfill_task_assignments_batch", backfill)
    monkeypatch.setattr(main_module, "repair_legacy_data_batch", repair)
    monkeypatch.setattr(metrics, "record_maintenance_batch", metric)
    monkeypatch.setattr(
        main_module,
        "asyncio",
        SimpleNamespace(sleep=intervals.sleep, CancelledError=asyncio.CancelledError),
    )
    worker = asyncio.create_task(main_module._legacy_data_repair_loop(300, 2, 2, 2))
    try:
        await intervals.reached(worker, 1, 300)
        assert failed_transactions == 1 and not batches and not measured
        assert await _legacy_state(farms, tasks, edited_role, manual_task) == (set(), set())
        failures = [
            record
            for record in caplog.records
            if record.getMessage() == "periodic legacy-data repair failed"
        ]
        assert len(failures) == 1 and failures[0].exc_info is not None

        intervals.release(1)
        await intervals.reached(worker, 2, 300)
        assert len(batches) == 2, "A full queue must use the available two-page budget"
        completed, assigned = await _legacy_state(farms, tasks, edited_role, manual_task)
        assert completed == set(farms[: min(farm_count, 4)])
        assert assigned == set(tasks[: min(task_count, 4)])

        intervals.release(2)
        await intervals.reached(worker, 3, 300)
        assert len(batches) == 3, "Both short queues must stop without another empty probe"
        assert await _legacy_state(farms, tasks, edited_role, manual_task) == (
            set(farms),
            set(tasks),
        )

        intervals.release(3)
        await intervals.reached(worker, 4, 300)
        assert len(batches) == 4 and batches[-1] == (0, 0)
        assert measured == [farms_count + tasks_count for farms_count, tasks_count in batches]
        assert farm_phase_probes == [farm_count] * 4
        expected = [(4, 1), (1, 0)] if farm_count > task_count else [(1, 4), (0, 1)]
        messages = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("legacy repair processed ")
        ]
        assert messages == [
            f"legacy repair processed farms={farm_total} tasks={task_total}"
            for farm_total, task_total in expected
        ], "Operational totals must match committed rows and stay quiet for empty work"
    finally:
        worker.cancel()
        outcomes = await asyncio.gather(worker, return_exceptions=True)
    assert len(outcomes) == 1 and isinstance(outcomes[0], asyncio.CancelledError)


async def _aged_terminal_task(farm_id: int, title: str) -> int:
    old = utcnow() - timedelta(days=400)
    async with get_sessionmaker()() as db:
        task = Task(
            farm_id=farm_id,
            title=title,
            due_date=old.date(),
            status=TaskStatus.SKIPPED.value,
            skipped_at=old,
            category=TaskCategory.OTHER.value,
            auto_generated=False,
        )
        db.add(task)
        await db.commit()
        return task.id


async def _remaining_tasks(ids: set[int]) -> set[int]:
    async with get_sessionmaker()() as db:
        return set((await db.execute(select(Task.id).where(Task.id.in_(ids)))).scalars())


async def test_retention_driver_carries_partial_pages_and_wraps_to_new_first_farm_work(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    farms = []
    for index in range(3):
        owner = await owner_with_farm(client, email=f"cursor-driver-{index}@farm.in")
        farms.append(int(owner["X-Farm-Id"]))
    assert farms[0] == 1, "The first real tenant must expose a cursor that incorrectly wraps to one"
    tasks = [await _aged_terminal_task(farm, f"Original aged task {farm}") for farm in farms]
    settings: Settings | ValidationError
    try:
        settings = Settings(
            environment="development",
            retention_sweep_enabled=True,
            retention_sweep_interval_seconds=60,
            retention_farm_batch_size=1,
            retention_delete_batch_size=2,
            retention_max_batches_per_farm=1,
        )
    except ValidationError as observed_error:
        details = observed_error.errors(include_url=False)
        context = details[0].get("ctx") if len(details) == 1 else None
        if (
            observed_error.title != "Settings"
            or len(details) != 1
            or details[0]["loc"] != ("retention_sweep_interval_seconds",)
            or details[0]["type"] != "greater_than_equal"
            or details[0]["msg"] != "Input should be greater than or equal to 61"
            or not isinstance(context, dict)
            or set(context) != {"ge"}
            or type(context["ge"]) is not int
            or context["ge"] != 61
            or type(details[0]["input"]) is not int
            or details[0]["input"] != 60
        ):
            raise
        settings = observed_error
    assert isinstance(settings, Settings), (
        "a valid 60-second retention interval must construct real Settings"
    )
    intervals = _Intervals()
    calls: list[tuple[int, int, bool, int]] = []
    fail_next = False
    failed_cursors: list[int] = []
    real_sweep = run_retention_sweep

    async def sweep(
        db: AsyncSession, configured: Settings, *, after_farm_id: int = 0
    ) -> RetentionSummary:
        nonlocal fail_next
        if fail_next:
            fail_next = False
            failed_cursors.append(after_farm_id)
            await db.execute(text("SELECT 1 / 0"))
        summary = await real_sweep(db, configured, after_farm_id=after_farm_id)
        calls.append(
            (after_farm_id, summary.last_farm_id, summary.exhausted, summary.terminal_tasks)
        )
        return summary

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(main_module, "run_retention_sweep", sweep)
    monkeypatch.setattr(
        main_module,
        "asyncio",
        SimpleNamespace(sleep=intervals.sleep, CancelledError=asyncio.CancelledError),
    )
    worker = asyncio.create_task(main_module._retention_sweep_loop())
    try:
        await intervals.reached(worker, 1, 60)
        assert calls == [(0, farms[0], False, 1)]
        assert await _remaining_tasks(set(tasks)) == set(tasks[1:])
        late_first = await _aged_terminal_task(farms[0], "First-farm work during partial rotation")

        intervals.release(1)
        await intervals.reached(worker, 2, 60)
        assert calls[-1] == (farms[0], farms[1], False, 1)
        assert await _remaining_tasks({*tasks, late_first}) == {tasks[2], late_first}

        fail_next = True
        intervals.release(2)
        await intervals.reached(worker, 3, 60)
        assert failed_cursors == [farms[1]] and len(calls) == 2
        assert await _remaining_tasks({*tasks, late_first}) == {tasks[2], late_first}

        intervals.release(3)
        await intervals.reached(worker, 4, 60)
        assert calls[-1] == (farms[1], farms[2], True, 1)
        assert await _remaining_tasks({*tasks, late_first}) == {late_first}
        new_rotation = await _aged_terminal_task(
            farms[0], "First-farm work after completed rotation"
        )

        intervals.release(4)
        await intervals.reached(worker, 5, 60)
        assert calls[-1] == (0, farms[0], True, 2)
        assert await _remaining_tasks({*tasks, late_first, new_rotation}) == set()
    finally:
        worker.cancel()
        outcomes = await asyncio.gather(worker, return_exceptions=True)
    assert len(outcomes) == 1 and isinstance(outcomes[0], asyncio.CancelledError)
