"""Recurring husbandry cadence (services/cadence.ensure_cadence_tasks).

Domain rules under test:
- Seasonal calendar rounds (FMD Sep/Mar, ET+HS May, Goat Pox Nov, CCPP Jan,
  deworming Jun/Jan) fire once per (category, month, year) and are herd-level.
- Interval rounds (hoof trimming, spraying, disinfection, weighing) respect
  their lookback windows; a round due inside the window — including an
  operator-scheduled one due in the coming weeks — suppresses the next.
- The daily feed-room routine and the daily water check dedupe on (exact
  title, due date, any status).
- Feed reorder duties fire per under-level ingredient unless a PENDING FEED
  duty already names that ingredient.
- Buck rotation fires per male ≥ GOAT_PROFILE.buck_rotation_age_months with a
  365-day dedupe, coalescing dob/estimated_dob for the age math.
- A farm with no ACTIVE animals is a complete no-op.
- GET /api/tasks is read-only; the background sweep (run_ensure here)
  materializes the cadence idempotently, and every generated duty carries
  its localization key (title_key/title_args).

Business dates are frozen through the same monkeypatched ``today`` helper the
health suite uses (the farm-local business-date indirection), so no test
depends on the wall clock.
"""

from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.db import get_sessionmaker
from app.models import Animal, Farm, FeedInventory, Task, TaskStatus
from app.services.cadence import ensure_cadence_tasks
from app.utils import utcnow

from .conftest import owner_with_farm

FMD_TITLE_TEMPLATE = (
    "FMD vaccination round ({month} {year}) — all animals; "
    "close via a bucket/batch vaccine health event"
)
DEWORM_TITLE_TEMPLATE = "Deworming round ({month} {year}) — adults; kids 1–6 months every 3 months"
HOOF_TITLE = "Hoof trimming round (6-monthly) — trim all ages, heel to toe"
SPRAY_TITLE = "Ectoparasite spray/dip round (Butox/deltamethrin) — never heavily pregnant does"
DISINFECTION_TITLE = "Shed disinfection round — disinfect + lime; extra attention to kidding pens"
WEIGHING_TITLE = "Monthly weighing round — record weights; grow-out buckets first"
ROUTINE_TITLE = (
    "Morning routine: sweep bunks before the 6:30 AM feeding; check and refill water troughs"
)


def freeze_business_date(monkeypatch: pytest.MonkeyPatch, frozen: date) -> date:
    """Pin the farm-local business date every service decision uses."""

    def frozen_today(timezone_name: str) -> date:
        assert timezone_name == "Asia/Kolkata"
        return frozen

    monkeypatch.setattr("app.services.cadence.today", frozen_today)
    return frozen


async def make_animal(
    client: httpx.AsyncClient,
    headers: dict,
    tag: str,
    *,
    sex: str = "F",
    date_of_birth: str | None = None,
    estimated_dob: str | None = None,
) -> dict:
    payload: dict = {
        "tag_number": tag,
        "sex": sex,
        "source": "PURCHASED",
        "current_bucket": "FOUNDATION",
        "historical_import_reason": "Cadence test fixture",
    }
    if date_of_birth is not None:
        payload["date_of_birth"] = date_of_birth
    if estimated_dob is not None:
        payload["estimated_dob"] = estimated_dob
    resp = await client.post("/api/animals", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def load_farm(farm_id: int) -> Farm:
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        return farm


async def run_ensure(farm_id: int) -> None:
    farm = await load_farm(farm_id)
    async with get_sessionmaker()() as db:
        await ensure_cadence_tasks(db, farm)


async def farm_tasks(farm_id: int, category: str | None = None) -> list[Task]:
    async with get_sessionmaker()() as db:
        stmt = select(Task).where(Task.farm_id == farm_id)
        if category is not None:
            stmt = stmt.where(Task.category == category)
        rows = (await db.execute(stmt.order_by(Task.id))).scalars().all()
    return list(rows)


async def seed_history_task(
    farm_id: int,
    *,
    category: str,
    title: str,
    due_date: date,
    status: str = TaskStatus.DONE.value,
    animal_id: int | None = None,
) -> None:
    """A pre-existing duty standing in for history created by earlier rounds."""
    async with get_sessionmaker()() as db:
        db.add(
            Task(
                farm_id=farm_id,
                title=title,
                due_date=due_date,
                category=category,
                status=status,
                animal_id=animal_id,
                auto_generated=True,
                completed_at=utcnow() if status == TaskStatus.DONE.value else None,
                skipped_at=utcnow() if status == TaskStatus.SKIPPED.value else None,
            )
        )
        await db.commit()


async def set_inventory(farm_id: int, ingredient: str, qty: float) -> None:
    async with get_sessionmaker()() as db:
        await db.execute(
            update(FeedInventory)
            .where(FeedInventory.farm_id == farm_id, FeedInventory.ingredient == ingredient)
            .values(qty_on_hand=qty)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Calendar vaccination/deworming rounds
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("frozen", "category", "expected_title"),
    [
        (
            date(2026, 3, 3),
            "VACCINE",
            FMD_TITLE_TEMPLATE.format(month="March", year=2026),
        ),
        (
            date(2026, 9, 14),
            "VACCINE",
            FMD_TITLE_TEMPLATE.format(month="September", year=2026),
        ),
        (date(2026, 5, 2), "VACCINE", "ET + HS pre-monsoon round (2026) — all animals"),
        (date(2026, 11, 7), "VACCINE", "Goat Pox round (2026)"),
        (date(2027, 1, 12), "VACCINE", "CCPP round (2027)"),
        (
            date(2027, 1, 12),
            "DEWORMING",
            DEWORM_TITLE_TEMPLATE.format(month="January", year=2027),
        ),
        (
            date(2026, 6, 15),
            "DEWORMING",
            DEWORM_TITLE_TEMPLATE.format(month="June", year=2026),
        ),
    ],
)
async def test_calendar_round_fires_in_month(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    frozen: date,
    category: str,
    expected_title: str,
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "C-001")
    farm_id = int(headers["X-Farm-Id"])
    freeze_business_date(monkeypatch, frozen)

    await run_ensure(farm_id)
    rounds = [t for t in await farm_tasks(farm_id, category) if t.title == expected_title]
    assert len(rounds) == 1
    assert rounds[0].due_date == frozen
    assert rounds[0].animal_id is None
    assert rounds[0].auto_generated

    # A second board load in the same month must not mint a second round.
    await run_ensure(farm_id)
    rounds = [t for t in await farm_tasks(farm_id, category) if t.title == expected_title]
    assert len(rounds) == 1


async def test_calendar_round_is_not_created_off_month(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "C-002")
    farm_id = int(headers["X-Farm-Id"])
    freeze_business_date(monkeypatch, date(2026, 8, 14))  # August: no round defined

    await run_ensure(farm_id)
    vaccine_titles = [t.title for t in await farm_tasks(farm_id, "VACCINE")]
    assert vaccine_titles == []


async def test_calendar_round_dedupe_is_scoped_to_month_and_year(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Last year's differently-titled round never blocks this year's copy."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "C-003")
    farm_id = int(headers["X-Farm-Id"])
    frozen = freeze_business_date(monkeypatch, date(2026, 9, 14))

    await seed_history_task(
        farm_id,
        category="VACCINE",
        title=FMD_TITLE_TEMPLATE.format(month="September", year=2025),
        due_date=date(2025, 9, 14),
    )
    await seed_history_task(
        farm_id,
        category="VACCINE",
        title=FMD_TITLE_TEMPLATE.format(month="September", year=2026),
        due_date=date(2026, 9, 1),
    )
    await run_ensure(farm_id)

    # The 2025 round is outside this month; the 2026 one is inside it, so the
    # September 2026 round is suppressed despite the fresh load being due on
    # a different day of the month.
    fmd_2026 = [
        t
        for t in await farm_tasks(farm_id, "VACCINE")
        if t.title == FMD_TITLE_TEMPLATE.format(month="September", year=2026)
    ]
    assert len(fmd_2026) == 1  # only the seeded history row
    assert fmd_2026[0].due_date == date(2026, 9, 1)
    assert frozen == date(2026, 9, 14)  # the frozen date drove the month/year decision


# ---------------------------------------------------------------------------
# Interval husbandry rounds
# ---------------------------------------------------------------------------
async def test_interval_rounds_fire_on_a_fresh_farm(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "I-001")
    farm_id = int(headers["X-Farm-Id"])
    frozen = freeze_business_date(monkeypatch, date(2026, 9, 14))

    await run_ensure(farm_id)
    for category, title in (
        ("HOOF_TRIMMING", HOOF_TITLE),
        ("SPRAYING", SPRAY_TITLE),
        ("DISINFECTION", DISINFECTION_TITLE),
        ("WEIGHING", WEIGHING_TITLE),
    ):
        rounds = [t for t in await farm_tasks(farm_id, category)]
        assert [t.title for t in rounds] == [title]
        assert rounds[0].due_date == frozen


async def test_interval_rounds_respect_lookback_history(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "I-002")
    farm_id = int(headers["X-Farm-Id"])
    frozen = freeze_business_date(monkeypatch, date(2026, 9, 14))

    # A weighing 30 days back sits inside the 45-day window: no new round.
    await seed_history_task(
        farm_id,
        category="WEIGHING",
        title="Monthly weighing round (crew A)",
        due_date=frozen - timedelta(days=30),
    )
    # A hoof round 183 days back is outside the 182-day window: regenerate.
    await seed_history_task(
        farm_id,
        category="HOOF_TRIMMING",
        title="Hoof trimming round (previous)",
        due_date=frozen - timedelta(days=183),
        status=TaskStatus.SKIPPED.value,
    )
    await run_ensure(farm_id)

    assert [t.title for t in await farm_tasks(farm_id, "WEIGHING")] == [
        "Monthly weighing round (crew A)"
    ]
    hoof_titles = [t.title for t in await farm_tasks(farm_id, "HOOF_TRIMMING")]
    assert HOOF_TITLE in hoof_titles


# ---------------------------------------------------------------------------
# Daily feed-room routine
# ---------------------------------------------------------------------------
async def test_daily_routine_dedupes_per_business_day(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-001")
    farm_id = int(headers["X-Farm-Id"])
    day_one = freeze_business_date(monkeypatch, date(2026, 9, 14))

    await run_ensure(farm_id)
    await run_ensure(farm_id)
    routine = [t for t in await farm_tasks(farm_id, "FEED") if t.title == ROUTINE_TITLE]
    assert len(routine) == 1
    assert routine[0].due_date == day_one

    # The next business day gets its own copy even while yesterday's is still
    # PENDING (the dedupe is the exact title due TODAY).
    freeze_business_date(monkeypatch, day_one + timedelta(days=1))
    await run_ensure(farm_id)
    routine = [t for t in await farm_tasks(farm_id, "FEED") if t.title == ROUTINE_TITLE]
    assert len(routine) == 2


async def test_daily_routine_honours_a_pending_manual_copy(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-002")
    farm_id = int(headers["X-Farm-Id"])
    frozen = freeze_business_date(monkeypatch, date(2026, 9, 14))

    await seed_history_task(
        farm_id,
        category="FEED",
        title=ROUTINE_TITLE,
        due_date=frozen,
        status=TaskStatus.PENDING.value,
    )
    await run_ensure(farm_id)
    routine = [t for t in await farm_tasks(farm_id, "FEED") if t.title == ROUTINE_TITLE]
    assert len(routine) == 1


# ---------------------------------------------------------------------------
# Feed reorder
# ---------------------------------------------------------------------------
async def test_reorder_fires_for_under_level_ingredients_only(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "R-001")
    farm_id = int(headers["X-Farm-Id"])
    frozen = freeze_business_date(monkeypatch, date(2026, 9, 14))

    # Seeded inventory starts every canonical ingredient at 0 kg against a
    # 100 kg reorder level. Refill one ingredient above its level; leave a
    # pending owner note that merely MENTIONS another ingredient.
    await set_inventory(farm_id, "Crushed maize", 150.0)
    await seed_history_task(
        farm_id,
        category="FEED",
        title="Owner note: reorder Groundnut haulms this week",
        due_date=frozen,
        status=TaskStatus.PENDING.value,
    )
    await run_ensure(farm_id)

    feed_titles = [t.title for t in await farm_tasks(farm_id, "FEED")]
    assert "Reorder Salt: 0 kg on hand (reorder level 100 kg)" in feed_titles
    assert not any("Crushed maize" in title for title in feed_titles)
    # Dedupe is a PREFIX match on the generated "Reorder {ingredient}: "
    # title: a pending note that merely mentions the ingredient does not
    # suppress the engine's own duty (the morning routine talks about bunks
    # and water; a substring match swallowed real alerts).
    groundnut = sorted(title for title in feed_titles if "Groundnut haulms" in title)
    assert groundnut == [
        "Owner note: reorder Groundnut haulms this week",
        "Reorder Groundnut haulms: 0 kg on hand (reorder level 100 kg)",
    ]


async def test_reorder_dedupe_is_pending_only(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "R-002")
    farm_id = int(headers["X-Farm-Id"])
    frozen = freeze_business_date(monkeypatch, date(2026, 9, 14))

    await seed_history_task(
        farm_id,
        category="FEED",
        title="Reorder Salt: 5 kg on hand (reorder level 100 kg)",
        due_date=frozen - timedelta(days=3),
        status=TaskStatus.DONE.value,
    )
    await run_ensure(farm_id)
    salt_tasks = [t for t in await farm_tasks(farm_id, "FEED") if "Salt" in t.title]
    # The completed reminder does not count: a fresh under-level duty appears.
    assert len(salt_tasks) == 2

    await run_ensure(farm_id)
    salt_tasks = [t for t in await farm_tasks(farm_id, "FEED") if "Salt" in t.title]
    assert len(salt_tasks) == 2  # the fresh PENDING copy now suppresses


# ---------------------------------------------------------------------------
# Buck rotation
# ---------------------------------------------------------------------------
async def test_buck_rotation_age_math_and_coalesced_dob(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    # dob 2023-08-20 → 36 whole months on 2026-09-14 (day 14 < 20 floors to 36).
    old_buck = await make_animal(client, headers, "B-OLD", sex="M", date_of_birth="2023-08-20")
    # estimated_dob only (no recorded birth date) must coalesce the same way.
    estimated_buck = await make_animal(
        client, headers, "B-EST", sex="M", estimated_dob="2023-05-01"
    )
    # 27 months: under the 36-month rotation age.
    young_buck = await make_animal(client, headers, "B-YNG", sex="M", date_of_birth="2024-06-01")
    # Age alone must never rotate a doe.
    old_doe = await make_animal(client, headers, "B-DOE", sex="F", date_of_birth="2020-01-01")
    farm_id = int(headers["X-Farm-Id"])
    freeze_business_date(monkeypatch, date(2026, 9, 14))

    await run_ensure(farm_id)
    rotations = await farm_tasks(farm_id, "BUCK_ROTATION")
    assert sorted(t.animal_id for t in rotations) == [old_buck["id"], estimated_buck["id"]]
    by_animal = {t.animal_id: t for t in rotations}
    assert by_animal[old_buck["id"]].title == (
        "Rotate/replace buck B-OLD — 36 months old (inbreeding management)"
    )
    # estimated_dob 2023-05-01 → 40 whole months on 2026-09-14 (day 14 >= 1).
    assert by_animal[estimated_buck["id"]].title == (
        "Rotate/replace buck B-EST — 40 months old (inbreeding management)"
    )
    assert old_doe["id"] not in by_animal  # age alone never rotates a doe
    assert young_buck["id"] not in by_animal  # 27 months is under the floor


async def test_buck_rotation_dedupes_within_a_year(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    recent = await make_animal(client, headers, "B-REC", sex="M", date_of_birth="2020-01-01")
    stale = await make_animal(client, headers, "B-STL", sex="M", date_of_birth="2020-01-01")
    farm_id = int(headers["X-Farm-Id"])
    frozen = freeze_business_date(monkeypatch, date(2026, 9, 14))

    # Rotated 75 days ago (inside the 365-day dedupe) — no fresh duty.
    await seed_history_task(
        farm_id,
        category="BUCK_ROTATION",
        title="Rotate/replace buck B-REC — 79 months old (inbreeding management)",
        due_date=frozen - timedelta(days=75),
        animal_id=recent["id"],
    )
    # Rotated 378 days ago (outside the dedupe) — regenerate.
    await seed_history_task(
        farm_id,
        category="BUCK_ROTATION",
        title="Rotate/replace buck B-STL — 76 months old (inbreeding management)",
        due_date=frozen - timedelta(days=378),
        animal_id=stale["id"],
    )
    await run_ensure(farm_id)
    rotations = await farm_tasks(farm_id, "BUCK_ROTATION")
    by_animal: dict[int | None, int] = {}
    for task in rotations:
        by_animal[task.animal_id] = by_animal.get(task.animal_id, 0) + 1
    assert by_animal[recent["id"]] == 1  # only the history row
    assert by_animal[stale["id"]] == 2  # history + regenerated reminder


# ---------------------------------------------------------------------------
# Empty farm
# ---------------------------------------------------------------------------
async def test_empty_farm_is_a_no_op(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await owner_with_farm(client)
    farm_id = int(headers["X-Farm-Id"])
    freeze_business_date(monkeypatch, date(2026, 9, 14))

    # No animals — even though September's FMD round, the daily routine and a
    # fully under-level seeded inventory would otherwise all fire.
    await run_ensure(farm_id)
    assert await farm_tasks(farm_id) == []


# ---------------------------------------------------------------------------
# Board hook (removed: the GET is read-only; the sweep materializes)
# ---------------------------------------------------------------------------
async def test_task_board_load_is_read_only_and_the_sweep_materializes(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/tasks never mutates the board; cadence arrives via the sweep.

    Before the split, the list endpoint took the farm advisory lock and
    committed generated duties inside a read request. Now a bare board load
    returns only what the background sweep (or any earlier write) committed.
    """
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "H-001")
    farm_id = int(headers["X-Farm-Id"])
    frozen = freeze_business_date(monkeypatch, date(2026, 9, 14))

    def frozen_board_today(timezone_name: str) -> date:
        assert timezone_name == "Asia/Kolkata"
        return frozen

    monkeypatch.setattr("app.api.tasks.today", frozen_board_today)

    first = await client.get("/api/tasks", headers=headers)
    assert first.status_code == 200, first.text
    fmd_title = FMD_TITLE_TEMPLATE.format(month="September", year=2026)
    assert fmd_title not in [t["title"] for t in first.json()["today"]]
    assert await farm_tasks(farm_id) == []

    await run_ensure(farm_id)
    second = await client.get("/api/tasks", headers=headers)
    assert second.status_code == 200, second.text
    today_titles = [t["title"] for t in second.json()["today"]]
    assert fmd_title in today_titles
    assert ROUTINE_TITLE in today_titles

    await run_ensure(farm_id)
    fmd_tasks = [t for t in await farm_tasks(farm_id, "VACCINE") if t.title == fmd_title]
    assert len(fmd_tasks) == 1
    routine = [t for t in await farm_tasks(farm_id, "FEED") if t.title == ROUTINE_TITLE]
    assert len(routine) == 1


async def test_daily_routine_completed_today_is_not_re_minted(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completing the routine and reloading the board must not duplicate it.

    The dedupe is any-status on the exact (title, due date): yesterday's
    DONE row has a different due date, but today's completed copy still
    proves today's routine exists.
    """
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "D-003")
    farm_id = int(headers["X-Farm-Id"])
    day = freeze_business_date(monkeypatch, date(2026, 9, 14))

    await run_ensure(farm_id)
    async with get_sessionmaker()() as db:
        routine = (
            await db.execute(
                select(Task).where(
                    Task.farm_id == farm_id,
                    Task.title == ROUTINE_TITLE,
                    Task.due_date == day,
                )
            )
        ).scalar_one()
        routine.status = TaskStatus.DONE.value
        routine.completed_at = utcnow()
        await db.commit()

    await run_ensure(farm_id)
    await run_ensure(farm_id)
    routine_rows = [t for t in await farm_tasks(farm_id, "FEED") if t.title == ROUTINE_TITLE]
    assert len(routine_rows) == 1
    assert routine_rows[0].status == TaskStatus.DONE.value


async def test_weighing_cadence_is_monthly_and_disinfection_quarterly(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lookbacks match their titles: 30 days for the monthly weighing
    round, 91 for quarterly disinfection — not the drifted 45/122 that made
    "monthly" fire ~8×/yr and "quarterly" ~3×/yr."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "I-003")
    farm_id = int(headers["X-Farm-Id"])
    frozen = freeze_business_date(monkeypatch, date(2026, 9, 14))

    # 30 days back: inside the monthly window → suppressed; 31: regenerate.
    await seed_history_task(
        farm_id,
        category="WEIGHING",
        title="Monthly weighing round (crew A)",
        due_date=frozen - timedelta(days=30),
    )
    # 91 days back: inside the quarterly window → suppressed; 92: regenerate.
    await seed_history_task(
        farm_id,
        category="DISINFECTION",
        title="Shed disinfection round (previous quarter)",
        due_date=frozen - timedelta(days=91),
    )
    await run_ensure(farm_id)
    assert [t.title for t in await farm_tasks(farm_id, "WEIGHING")] == [
        "Monthly weighing round (crew A)"
    ]
    assert [t.title for t in await farm_tasks(farm_id, "DISINFECTION")] == [
        "Shed disinfection round (previous quarter)"
    ]

    # Nudge both one day past their windows: both rounds regenerate.
    async with get_sessionmaker()() as db:
        await db.execute(
            update(Task)
            .where(
                Task.farm_id == farm_id,
                Task.title.in_(
                    (
                        "Monthly weighing round (crew A)",
                        "Shed disinfection round (previous quarter)",
                    )
                ),
            )
            .values(due_date=frozen - timedelta(days=92))
        )
        await db.commit()
    await run_ensure(farm_id)
    assert len(await farm_tasks(farm_id, "WEIGHING")) == 2
    assert len(await farm_tasks(farm_id, "DISINFECTION")) == 2


async def age_farm_creation(farm_id: int, created: date) -> None:
    """Backfill must not resurrect rounds from before the farm existed."""
    async with get_sessionmaker()() as db:
        await db.execute(update(Farm).where(Farm.id == farm_id).values(created_at=created))
        await db.commit()


async def age_animal_introduction(farm_id: int, introduced: date) -> None:
    """Age the farm's animal rows to their "arrival" (created_at floor), so a
    backfill test can stand for a farm whose stock predates the test run."""
    async with get_sessionmaker()() as db:
        await db.execute(
            update(Animal)
            .where(Animal.farm_id == farm_id)
            .values(created_at=datetime(introduced.year, introduced.month, introduced.day))
        )
        await db.commit()


async def test_missed_seasonal_round_is_backfilled_late(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A round whose month passed without a board load is not silently
    dropped: the series' latest occurrence materializes late, due at that
    month's end (overdue), once per series."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "C-010")
    farm_id = int(headers["X-Farm-Id"])
    freeze_business_date(monkeypatch, date(2026, 9, 14))
    await age_farm_creation(farm_id, date(2025, 1, 1))
    # The animals stood since 2025 too — the backfill floor is the earliest
    # animal-introduction fact, not merely farm creation.
    await age_animal_introduction(farm_id, date(2025, 1, 1))

    await run_ensure(farm_id)
    rounds = sorted(
        (t.category, t.title, t.due_date)
        for t in await farm_tasks(farm_id)
        if t.category in ("VACCINE", "DEWORMING")
    )
    assert rounds == [
        (
            "DEWORMING",
            DEWORM_TITLE_TEMPLATE.format(month="January", year=2026),
            date(2026, 1, 31),
        ),
        (
            "DEWORMING",
            DEWORM_TITLE_TEMPLATE.format(month="June", year=2026),
            date(2026, 6, 30),
        ),
        ("VACCINE", "CCPP round (2026)", date(2026, 1, 31)),
        ("VACCINE", "ET + HS pre-monsoon round (2026) — all animals", date(2026, 5, 31)),
        (
            "VACCINE",
            FMD_TITLE_TEMPLATE.format(month="March", year=2026),
            date(2026, 3, 31),
        ),
        (
            "VACCINE",
            FMD_TITLE_TEMPLATE.format(month="September", year=2026),
            date(2026, 9, 14),
        ),
        ("VACCINE", "Goat Pox round (2025)", date(2025, 11, 30)),
    ]

    # The late copies are the series' record now: no second mint on reload.
    await run_ensure(farm_id)
    again = [t for t in await farm_tasks(farm_id) if t.category in ("VACCINE", "DEWORMING")]
    assert len(again) == len(rounds)


async def test_backfill_never_precedes_farm_creation(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rounds older than the farm itself are history, not duties: the floor
    is the later of one cycle ago, the farm's creation date and the first
    animal's arrival."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "C-011")
    farm_id = int(headers["X-Farm-Id"])
    freeze_business_date(monkeypatch, date(2026, 9, 14))
    await age_farm_creation(farm_id, date(2026, 6, 1))
    await age_animal_introduction(farm_id, date(2026, 6, 1))

    await run_ensure(farm_id)
    titles = [t.title for t in await farm_tasks(farm_id) if t.category in ("VACCINE", "DEWORMING")]
    assert sorted(titles) == [
        DEWORM_TITLE_TEMPLATE.format(month="June", year=2026),
        FMD_TITLE_TEMPLATE.format(month="September", year=2026),
    ]


async def test_backfill_never_precedes_the_first_animal(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A farm registered months before its first goat arrived must not wake
    up to a board of "overdue" rounds from the animal-less months: the
    backfill floor is the farm's earliest animal-introduction fact (first
    animal row or purchase batch), not merely farm creation."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "C-012")
    farm_id = int(headers["X-Farm-Id"])
    freeze_business_date(monkeypatch, date(2026, 9, 14))
    await age_farm_creation(farm_id, date(2026, 1, 1))
    await age_animal_introduction(farm_id, date(2026, 8, 20))

    await run_ensure(farm_id)
    rounds = [t for t in await farm_tasks(farm_id) if t.category in ("VACCINE", "DEWORMING")]
    # Only the current-month September FMD round survives: every January-June
    # series occurrence ended before the first animal stood on the farm, and
    # no duty from those months is materialized as overdue.
    assert [(t.category, t.due_date) for t in rounds] == [("VACCINE", date(2026, 9, 14))]
    assert all("September 2026" in t.title for t in rounds)


async def test_interval_round_forward_window_suppresses_operator_scheduled_round(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator-created same-category duty due NEXT week counts too.

    The dedupe probe used to cover only [today - lookback, today]: a hoof
    trimming round already scheduled for next week did not suppress today's
    auto round, and the board grew a duplicate pair. The window now extends
    _INTERVAL_FORWARD_DEDUPE_DAYS ahead.
    """
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "F-001")
    farm_id = int(headers["X-Farm-Id"])
    day = freeze_business_date(monkeypatch, date(2026, 9, 14))
    await seed_history_task(
        farm_id,
        category="HOOF_TRIMMING",
        title="Hoof trimming round (operator-scheduled)",
        due_date=day + timedelta(days=7),
        status=TaskStatus.PENDING.value,
    )

    await run_ensure(farm_id)
    hoof = [t for t in await farm_tasks(farm_id, "HOOF_TRIMMING")]
    assert len(hoof) == 1
    assert hoof[0].title == "Hoof trimming round (operator-scheduled)"

    # Beyond the forward window the auto round fires again.
    await seed_history_task(
        farm_id,
        category="SPRAYING",
        title="Spray round scheduled far ahead",
        due_date=day + timedelta(days=31),
        status=TaskStatus.PENDING.value,
    )
    await run_ensure(farm_id)
    spray_titles = sorted(t.title for t in await farm_tasks(farm_id, "SPRAYING"))
    assert spray_titles == [
        SPRAY_TITLE,
        "Spray round scheduled far ahead",
    ]


async def test_daily_water_check_fires_once_per_business_day(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trough round is its own daily WATER duty, deduped any-status."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "W-001")
    farm_id = int(headers["X-Farm-Id"])
    day = freeze_business_date(monkeypatch, date(2026, 9, 14))

    await run_ensure(farm_id)
    water = await farm_tasks(farm_id, "WATER")
    assert len(water) == 1
    assert water[0].due_date == day
    assert water[0].title_key == "daily_water_check"
    assert water[0].title_args["due_date"] == day.isoformat()
    # The trough duty routes to the feed crew like the feed routine.
    assert water[0].assigned_role_id is not None

    # Completed today still proves today's round exists; nothing re-mints.
    async with get_sessionmaker()() as db:
        row = await db.get(Task, water[0].id)
        assert row is not None
        row.status = TaskStatus.DONE.value
        row.completed_at = utcnow()
        await db.commit()
    await run_ensure(farm_id)
    assert len(await farm_tasks(farm_id, "WATER")) == 1

    # The next business day gets its own copy.
    freeze_business_date(monkeypatch, date(2026, 9, 15))
    await run_ensure(farm_id)
    assert len(await farm_tasks(farm_id, "WATER")) == 2


async def test_generated_duties_carry_title_keys_and_english_fallbacks(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every sweep-generated duty publishes title_key + structured args."""
    headers = await owner_with_farm(client)
    await make_animal(client, headers, "K-001")
    farm_id = int(headers["X-Farm-Id"])
    freeze_business_date(monkeypatch, date(2026, 9, 14))

    await run_ensure(farm_id)
    tasks = await farm_tasks(farm_id)
    assert tasks
    for task in tasks:
        assert task.title_key is not None, task.title
        assert task.title  # English fallback always populated
        assert task.title_args["due_date"] == task.due_date.isoformat()
    by_category = {task.category: task.title_key for task in tasks}
    assert by_category["WATER"] == "daily_water_check"
    assert by_category["WEIGHING"] == "monthly_weighing_round"
    assert by_category["HOOF_TRIMMING"] == "hoof_trimming_round"


async def test_farm_batch_isolates_per_farm_failures(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BIZ-2 (2026-09-16): one failing farm must not abort the whole keyset
    page — the next farm still materializes and the cursor advances past
    the failure so subsequent intervals make progress."""
    from app.services import cadence as cadence_module

    headers_a = await owner_with_farm(client, email="cad-fail-a@farm.in", farm_name="A")
    headers_b = await owner_with_farm(client, email="cad-fail-b@farm.in", farm_name="B")
    farm_a = int(headers_a["X-Farm-Id"])
    farm_b = int(headers_b["X-Farm-Id"])
    assert farm_b > farm_a

    calls: list[int] = []
    real = cadence_module.ensure_cadence_tasks

    from sqlalchemy import inspect

    def farm_id_of(farm) -> int:
        # farm.id attribute access lazy-loads once the session rolls a
        # poisoned farm back; the identity key survives expiration.
        return inspect(farm).identity[0]

    async def flaky(db, farm):
        fid = farm_id_of(farm)
        calls.append(fid)
        if fid == farm_a:
            raise RuntimeError("persistent per-farm poison")
        return await real(db, farm)

    monkeypatch.setattr(cadence_module, "ensure_cadence_tasks", flaky)
    async with get_sessionmaker()() as db:
        fetched, cursor = await cadence_module.ensure_cadence_farm_batch(
            db, batch_size=10, after_farm_id=0
        )
        await db.rollback()  # nothing should have been mutated by this probe

    assert farm_a in calls and farm_b in calls
    assert cursor >= farm_b  # the cursor advanced PAST the failing farm
    assert fetched >= 2
