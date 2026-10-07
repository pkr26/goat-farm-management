"""A valid dependent kid keeps its displayed terminal creep ration and conserved headcount."""

import httpx

from app.db import get_sessionmaker
from app.models import Farm
from app.services.feeding import feeding_plan
from app.utils import today

from .conftest import owner_with_farm
from .test_feeding_extended import _orm_animal


async def test_day_sixty_dependent_creep_row_is_present_and_conserves_the_real_cohort(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="feeding-dependent-conservation@farm.in")
    farm_id = int(owner["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        dam = _orm_animal(farm_id, "DEPENDENT-DAM", sex="F", bucket="RECOVERY", dob_days=800)
        db.add(dam)
        await db.flush()
        dam_id = dam.id
        db.add_all(
            _orm_animal(
                farm_id, f"DEPENDENT-{age}", sex="F", bucket="RECOVERY", dob_days=age, dam_id=dam_id
            )
            for age in [40, 60]
        )
        await db.commit()
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        before = await feeding_plan(db, farm, today())
        terminal_rows = [line for line in before if line["creep_band"] == "46–60 d"]
        assert len(terminal_rows) == 1, before
        assert sum(line["heads"] for line in before) == 3
        terminal = terminal_rows[0]
        assert terminal["heads"] == 1 and terminal["kg_per_head"] == 0.3
        assert terminal["segment"] == "CREEP_BAND"
    recorded = await client.post(
        f"/api/animals/{dam_id}/status",
        headers=owner,
        json={
            "new_status": "DEAD",
            "date": today().isoformat(),
            "notes": "Recorded death of the restored legacy dam",
        },
    )
    assert recorded.status_code == 200, recorded.text
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        after = await feeding_plan(db, farm, today())
        assert len(after) == 1 and after[0]["heads"] == 2
        assert after[0]["recipe_code"] == "LACTATING_60_40"
        assert after[0]["segment"] == "ALL"
