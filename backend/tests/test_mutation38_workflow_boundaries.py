"""Independent geometry, call-ledger, retry-policy and digest boundary oracles.

Scratch draft only: production inputs are frozen during the full baseline.
Expected retry limits and boundary instants are literal policy facts rather
than values imported from the implementation being mutated.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
from zoneinfo import ZoneInfo

import httpx
import pytest
from PIL import Image
from sqlalchemy import func, literal, select

from app.core.config import Settings
from app.db import get_sessionmaker
from app.models import (
    Farm,
    FarmMembership,
    ScreeningCallReservation,
    ScreeningDailyBudget,
    ScreeningImage,
    Task,
)
from app.services.notifications.service import _digest_text_for_recipient, _in_quiet_hours
from app.services.screening.budget import ScreeningBudgetExhausted, reserve_provider_attempt
from app.services.screening.images import (
    CropError,
    ImageNormalizationError,
    crop_image,
    normalize_image,
)
from app.services.screening.pipeline import _claim_retry_rows

from .conftest import owner_with_farm
from .test_cadence import farm_tasks, freeze_business_date, make_animal, run_ensure
from .test_notifications import _farm, _membership_id, _recipient
from .test_screening import FakeStorage, _budget_ledger_runs, _jpeg_bytes


@pytest.mark.parametrize(
    ("frame", "box", "expected"),
    [
        ((1000, 500), (0, 0, 100, 100), (150, 75)),
        ((1000, 1000), (960, 960, 40, 40), (90, 90)),
    ],
)
def test_crop_frame_edges_and_normalized_scale_are_exact(
    frame: tuple[int, int], box: tuple[int, int, int, int], expected: tuple[int, int]
) -> None:
    try:
        crop = crop_image(_jpeg_bytes(*frame), box)
    except CropError as exc:
        pytest.fail(f"A usable detection crop was rejected: {exc}")
    assert (crop.width, crop.height) == expected


def test_eight_pixel_full_frame_crop_is_usable() -> None:
    try:
        crop = crop_image(_jpeg_bytes(8, 8), (0, 0, 1000, 1000))
    except CropError as exc:
        pytest.fail(f"The eight-pixel usable crop boundary was rejected: {exc}")
    assert (crop.width, crop.height) == (8, 8)


@pytest.mark.parametrize("frame", [(7, 8), (8, 7)])
def test_crop_requires_eight_pixels_on_each_independent_axis(frame: tuple[int, int]) -> None:
    with pytest.raises(CropError):
        crop_image(_jpeg_bytes(*frame), (0, 0, 1000, 1000))


@pytest.mark.parametrize("frame", [(1, 20), (20, 1)])
def test_one_pixel_normalized_source_axis_remains_valid(frame: tuple[int, int]) -> None:
    source = io.BytesIO()
    Image.new("RGB", frame, (190, 20, 30)).save(source, format="PNG")
    try:
        normalized = normalize_image(source.getvalue(), 64, "image/png")
    except ImageNormalizationError as exc:
        pytest.fail(f"A positive one-pixel raster axis must remain usable: {exc}")
    assert (normalized.width, normalized.height) == frame


def test_decode_edge_budget_accepts_the_exact_ten_thousand_pixel_boundary() -> None:
    source = io.BytesIO()
    Image.new("RGB", (10_000, 1), (190, 20, 30)).save(source, format="PNG")
    try:
        normalized = normalize_image(source.getvalue(), 64, "image/png")
    except ImageNormalizationError as exc:
        pytest.fail(f"A ten-thousand-pixel edge is within the decode budget: {exc}")
    assert (normalized.width, normalized.height) == (64, 1)


def test_decode_edge_budget_rejects_the_first_pixel_past_the_boundary() -> None:
    source = io.BytesIO()
    Image.new("RGB", (10_001, 1), (190, 20, 30)).save(source, format="PNG")
    with pytest.raises(ImageNormalizationError, match="dimension budget"):
        normalize_image(source.getvalue(), 64, "image/png")


@pytest.mark.parametrize("timezone_name", ["Asia/Kolkata", "America/New_York"])
async def test_legacy_call_seed_includes_only_same_farm_half_open_local_day(
    client: httpx.AsyncClient, timezone_name: str
) -> None:
    owner = await owner_with_farm(client, email="ledger-boundary@farm.in")
    foreign = await owner_with_farm(client, email="ledger-foreign@farm.in")
    farm_id, foreign_id = int(owner["X-Farm-Id"]), int(foreign["X-Farm-Id"])
    # New York's 2026-03-08 is a 23-hour DST day. Kolkata also exercises
    # a non-integral UTC offset. End is next LOCAL midnight, not start+24h.
    now = dt.datetime(2026, 3, 8, 16)
    zone = ZoneInfo(timezone_name)
    local_date = dt.date(2026, 3, 8)
    start = dt.datetime.combine(local_date, dt.time.min, zone).astimezone(dt.UTC)
    end = dt.datetime.combine(local_date + dt.timedelta(days=1), dt.time.min, zone).astimezone(
        dt.UTC
    )
    start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        farm.timezone = timezone_name
        await db.commit()
        await _budget_ledger_runs(
            db,
            farm_id,
            FakeStorage(),
            [start - dt.timedelta(microseconds=1), start, end - dt.timedelta(microseconds=1), end],
        )
        await _budget_ledger_runs(db, foreign_id, FakeStorage(), [start + dt.timedelta(hours=1)])
        try:
            await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name=timezone_name,
                provider="boundary",
                cap=5,
                attempt_id="boundary-first",
                now=now,
            )
        except ScreeningBudgetExhausted as exc:
            pytest.fail(
                f"Two in-day legacy calls must leave the fifth transport attempt free: {exc}"
            )
        await db.rollback()
        with pytest.raises(ScreeningBudgetExhausted, match="Daily screening call budget"):
            await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name=timezone_name,
                provider="boundary",
                cap=5,
                attempt_id="boundary-second",
                now=now,
            )
        budget = await db.get(ScreeningDailyBudget, (farm_id, local_date))
        assert budget is not None and budget.reserved_calls == 5
        reservations = (await db.execute(select(ScreeningCallReservation))).scalars().all()
        assert len(reservations) == 1 and reservations[0].attempt_id == "boundary-first"


async def test_cutover_hold_is_distinguished_from_an_ordinary_cap(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="ledger-cutover@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add(
            ScreeningDailyBudget(
                farm_id=farm_id, local_date=dt.date(2026, 10, 7), reserved_calls=2_147_483_647
            )
        )
        await db.commit()
        with pytest.raises(ScreeningBudgetExhausted, match="Legacy screening work is held"):
            await reserve_provider_attempt(
                db,
                farm_id=farm_id,
                timezone_name="America/Phoenix",
                provider="held",
                cap=1,
                attempt_id="held-attempt",
                now=dt.datetime(2026, 10, 7, 17),
            )
        assert (await db.execute(select(ScreeningCallReservation))).scalars().all() == []


async def test_paid_call_admission_does_not_wait_for_a_retention_planner_lock(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="ledger-lock-isolation@farm.in")
    farm_id = int(owner["X-Farm-Id"])

    async def charge() -> str:
        async with get_sessionmaker()() as db:
            return await reserve_provider_attempt(
                db, farm_id=farm_id, timezone_name="America/Phoenix", provider="lock-check", cap=1
            )

    async with get_sessionmaker()() as locker:
        # The retention planner's documented namespace is 4719. It must not
        # serialize unrelated provider-call admission in the same farm.
        await locker.execute(select(func.pg_advisory_xact_lock(literal(4719), literal(farm_id))))
        try:
            receipt = await asyncio.wait_for(charge(), timeout=3)
        except TimeoutError:
            pytest.fail("Paid call admission waited for the unrelated retention planner lock")
        finally:
            await locker.rollback()
    assert isinstance(receipt, str)


async def test_claim_has_an_independent_five_attempt_policy(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="retry-policy@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    now = dt.datetime(2026, 10, 7, 17)
    async with get_sessionmaker()() as db:
        fourth, fifth = [
            ScreeningImage(
                farm_id=farm_id,
                s3_bucket="goat-photos",
                s3_key=f"attempt-{attempts}",
                status="PENDING",
                screening_attempts=attempts,
                created_at=now,
            )
            for attempts in (4, 5)
        ]
        db.add_all([fourth, fifth])
        await db.commit()
        fourth_id, fifth_id = fourth.id, fifth.id
        claimed, errors, flagged = await _claim_retry_rows(
            db, 10, now, dt.timedelta(hours=1), dt.timedelta(minutes=30)
        )
        assert [image.id for image in claimed] == [fourth_id]
        assert errors == flagged == 0
        final = {image.id: image for image in (await db.execute(select(ScreeningImage))).scalars()}
        assert final[fourth_id].status == "PROCESSING" and final[fourth_id].screening_attempts == 5
        assert final[fifth_id].status == "PENDING" and final[fifth_id].screening_attempts == 5


async def test_error_retry_requires_more_than_one_full_hour(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client, email="retry-hour@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    now = dt.datetime(2026, 10, 7, 17)
    async with get_sessionmaker()() as db:
        old, boundary = [
            ScreeningImage(
                farm_id=farm_id,
                s3_bucket="goat-photos",
                s3_key=f"error-{index}",
                status="ERROR",
                error="Test transport failure",
                screening_attempts=1,
                created_at=now - dt.timedelta(hours=2),
                updated_at=now - dt.timedelta(hours=1, microseconds=offset),
            )
            for index, offset in enumerate((1, 0))
        ]
        db.add_all([old, boundary])
        await db.commit()
        old_id, boundary_id = old.id, boundary.id
        claimed, errors, flagged = await _claim_retry_rows(
            db, 10, now, dt.timedelta(hours=1), dt.timedelta(minutes=30)
        )
        assert [image.id for image in claimed] == [old_id]
        assert errors == 1 and flagged == 0
        unchanged = await db.get(ScreeningImage, boundary_id)
        assert unchanged is not None and unchanged.status == "ERROR"
        assert unchanged.screening_attempts == 1


async def test_annual_ppr_waits_for_the_calendar_anniversary_across_a_leap_day(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = await owner_with_farm(client, email="ppr-calendar-anniversary@farm.in")
    await make_animal(client, owner, "PPR-CALENDAR-001")
    farm_id = int(owner["X-Farm-Id"])
    freeze_business_date(monkeypatch, dt.date(2023, 3, 1))
    await run_ensure(farm_id)

    freeze_business_date(monkeypatch, dt.date(2024, 2, 29))
    await run_ensure(farm_id)
    ppr = [
        task
        for task in await farm_tasks(farm_id, "VACCINE")
        if task.title_key == "ppr_vaccination_round"
    ]
    assert [task.due_date for task in ppr] == [dt.date(2023, 3, 1)]

    freeze_business_date(monkeypatch, dt.date(2024, 3, 1))
    await run_ensure(farm_id)
    ppr = [
        task
        for task in await farm_tasks(farm_id, "VACCINE")
        if task.title_key == "ppr_vaccination_round"
    ]
    assert [task.due_date for task in ppr] == [dt.date(2023, 3, 1), dt.date(2024, 3, 1)]


@pytest.mark.parametrize("count", [5, 6])
async def test_digest_lists_five_titles_and_reports_only_actual_remaining_duties(
    client: httpx.AsyncClient, count: int
) -> None:
    owner = await owner_with_farm(client, email="digest-title-boundary@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    membership_id = await _membership_id(client, owner)
    reference = dt.date(2026, 10, 7)
    async with get_sessionmaker()() as db:
        farm = await _farm(db, farm_id)
        recipient = await _recipient(db, farm_id, membership_id)
        worker_id, role_id = (
            await db.execute(
                select(FarmMembership.user_id, FarmMembership.role_id).where(
                    FarmMembership.id == membership_id
                )
            )
        ).one()
        db.add_all(
            [
                Task(
                    farm_id=farm_id,
                    title=f"Duty {index}",
                    due_date=reference,
                    category="OTHER",
                    status="PENDING",
                    assigned_user_id=worker_id,
                    assigned_role_id=role_id,
                )
                for index in range(count)
            ]
        )
        await db.commit()
        message = await _digest_text_for_recipient(db, farm, recipient, reference)
    assert message is not None
    lines = message.splitlines()
    assert lines[0] == f"Herdly 2026-10-07: {count} duties today"
    assert [line for line in lines if line.startswith("- ")] == [f"- Duty {i}" for i in range(5)]
    assert [line for line in lines if line.startswith("... ")] == (
        [] if count == 5 else ["... and 1 more"]
    )


@pytest.mark.parametrize(
    ("start", "end", "quiet"),
    [(21, 6, {0, 1, 2, 3, 4, 5, 21, 22, 23}), (6, 7, {6}), (4, 4, set())],
)
def test_quiet_hour_boundaries_cover_overnight_daytime_and_empty_windows(
    start: int, end: int, quiet: set[int]
) -> None:
    settings = Settings(
        environment="development",
        notifications_quiet_start_hour=start,
        notifications_quiet_end_hour=end,
    )
    for hour in range(24):
        assert _in_quiet_hours(settings, dt.datetime(2026, 10, 7, hour)) is (hour in quiet)
