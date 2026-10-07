"""Real persisted schedule facts, farm dates and the documented legacy window."""

from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Self

import httpx
import pytest
from sqlalchemy import select

from app import utils
from app.db import get_sessionmaker
from app.models import Animal, HealthEvent, VaccineTemplate
from app.services.health import inferred_schedule_template, vaccination_schedule_for_animal
from app.utils import today

from .conftest import owner_with_farm, register
from .test_health_extended import get_schedule, make_animal, record_event, row_by_name


@pytest.mark.parametrize("match_rank", [500, 501])
async def test_legacy_schedule_respects_the_literal_five_hundred_fact_window(
    client: httpx.AsyncClient, match_rank: int
) -> None:
    owner = await owner_with_farm(client, email="legacy-schedule-window@farm.in")
    animal = await make_animal(client, owner, tag="LEGACY-WINDOW")
    observed = today() - timedelta(days=match_rank)
    # These are constraint-enabled historical clinical records from an
    # out-of-process writer that omitted template linkage, the compatibility
    # case documented by the actual schedule reader. No existing facts are
    # erased, reclassified or supplied by a mocked query.
    async with get_sessionmaker()() as db:
        db.add_all(
            HealthEvent(
                farm_id=int(owner["X-Farm-Id"]),
                animal_id=animal["id"],
                date=today() - timedelta(days=rank),
                type="VACCINE",
                product_name="PPR vaccine"
                if rank == match_rank
                else f"Unclassified historical vaccine {rank}",
                schedule_template_id=None,
                schedule_template_name=None,
            )
            for rank in range(1, match_rank + 1)
        )
        await db.commit()
    response = await get_schedule(client, owner, animal["id"])
    ppr = row_by_name(response, "PPR")
    assert ppr["last_done"] == (observed.isoformat() if match_rank == 500 else None)
    if match_rank == 500:
        assert ppr["status"] == "OVERDUE"
    else:
        assert ppr["status"] == "UNKNOWN"


async def test_seeded_first_dose_only_programme_participates_in_unique_native_inference(
    client: httpx.AsyncClient,
) -> None:
    await owner_with_farm(client, email="first-dose-only-inference@farm.in")
    async with get_sessionmaker()() as db:
        programme = (
            await db.execute(
                select(VaccineTemplate).where(VaccineTemplate.name == "Anti-coccidial drench")
            )
        ).scalar_one()
        assert programme.first_dose_age_months == 1 and programme.repeat_months is None
        # This exercises the published native classifier and the same legacy
        # compatibility event-kind contract used by existing schedule tests.
        # It creates no clinical event or invented vaccination completion.
        inferred = await inferred_schedule_template(db, "VACCINE", "Anti-coccidial drench", "")
        assert inferred is not None and inferred.id == programme.id


async def test_schedule_uses_its_farm_calendar_on_the_first_due_anniversary(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = await register(client, email="schedule-own-calendar@farm.in")
    created = await client.post(
        "/api/auth/farms",
        headers=auth,
        json={"name": "Western calendar farm", "timezone": "Pacific/Honolulu"},
    )
    assert created.status_code == 201, created.text
    owner = {**auth, "X-Farm-Id": str(created.json()["id"])}
    animal_data = await make_animal(
        client, owner, tag="FIRST-DUE-ANNIVERSARY", date_of_birth="2026-07-04"
    )
    fixed = datetime(2026, 10, 5, 6, 30, tzinfo=UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            assert tz is not None
            return cls.fromtimestamp(fixed.timestamp(), tz)

    # Pin only the external clock; genuine IANA conversion and genuine today
    # execute normally. Setup/auth precedes the pin, so token clocks are not
    # replaced and no business/session/SQL output is fabricated.
    monkeypatch.setattr(utils, "datetime", FixedDatetime)
    assert today("Pacific/Honolulu") == date(2026, 10, 4)
    assert today() == date(2026, 10, 5)
    async with get_sessionmaker()() as db:
        animal = await db.get(Animal, animal_data["id"])
        assert animal is not None
        try:
            rows = await vaccination_schedule_for_animal(db, animal)
        except AttributeError as error:
            pytest.fail(f"A valid persisted animal's schedule must complete: {error}")
        fmd = next(row for row in rows if row["template"].name == "FMD")
        assert fmd["first_due"] == date(2026, 10, 4)
        assert fmd["status"] == "UPCOMING"
        assert fmd["last_done"] is None and fmd["next_due"] is None


async def test_a_recorded_primary_dose_is_not_overdue_on_its_actual_booster_day(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="booster-due-today@farm.in")
    animal = await make_animal(
        client,
        owner,
        tag="BOOSTER-TODAY",
        date_of_birth=(today() - timedelta(days=400)).isoformat(),
    )
    administered = today() - timedelta(days=25)
    await record_event(
        client,
        owner,
        animal_id=animal["id"],
        type="VACCINE",
        product_name="FMD vaccine",
        date=administered.isoformat(),
    )
    fmd = row_by_name(await get_schedule(client, owner, animal["id"]), "FMD")
    assert fmd["last_done"] == administered.isoformat()
    assert fmd["booster_due"] == fmd["next_due"] == today().isoformat()
    assert fmd["status"] == "DONE"
