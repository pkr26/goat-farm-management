"""Native/restored cohorts preserve actual dam provenance and displayed sex order."""

import httpx
from sqlalchemy import text

from app.db import get_sessionmaker
from app.models import Farm
from app.services.feeding import feeding_plan
from app.utils import today

from .conftest import owner_with_farm
from .test_feeding_extended import _orm_animal


async def test_creep_requires_its_actual_dam_to_be_in_recovery(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="feeding-native-dam-provenance@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        dam = _orm_animal(
            farm_id, "ACTUAL-FOUNDATION-DAM", sex="F", bucket="FOUNDATION", dob_days=800
        )
        db.add(dam)
        await db.flush()
        db.add(
            _orm_animal(
                farm_id,
                "RESTORED-RECOVERY-KID",
                sex="F",
                bucket="RECOVERY",
                dob_days=40,
                dam_id=dam.id,
            )
        )
        # This initially restored native cohort retains its actual FK parent
        # and recorded current buckets. No clinical fact is erased and no
        # constraint or business implementation is replaced.
        await db.commit()
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        lines = await feeding_plan(db, farm, today())
        assert [(line["bucket"], line["segment"], line["heads"]) for line in lines] == [
            ("FOUNDATION", "ALL", 1),
            ("RECOVERY", "ALL", 1),
        ], lines
        assert all(line["recipe_code"] == "LACTATING_60_40" for line in lines)
        assert sum(line["heads"] for line in lines) == 2


async def test_displayed_breeding_sex_order_survives_real_grouping_plan_options(
    client: httpx.AsyncClient,
) -> None:
    headers = await owner_with_farm(client, email="feeding-native-pg-sex-order@farm.in")
    farm_id = int(headers["X-Farm-Id"])
    async with get_sessionmaker()() as db:
        db.add_all(
            [_orm_animal(farm_id, "NATIVE-ORDER-M", sex="M", bucket="BREEDING", dob_days=900)]
            + [
                _orm_animal(
                    farm_id, f"NATIVE-ORDER-F-{n}", sex="F", bucket="BREEDING", dob_days=800
                )
                for n in range(3)
            ]
        )
        await db.commit()
        farm = await db.get(Farm, farm_id)
        assert farm is not None
        # Actual supported PostgreSQL session planning options affect how
        # equal SQL ordering keys arrive, while the native plan contract
        # still displays female then male with conserved identities/heads.
        await db.execute(text("SET LOCAL enable_sort = off"))
        await db.execute(text("SET LOCAL enable_incremental_sort = off"))
        lines = await feeding_plan(db, farm, today())
        assert [line["segment"] for line in lines] == ["FEMALE", "MALE"], lines
        assert [line["heads"] for line in lines] == [3, 1]
        assert [line["kg_per_head"] for line in lines] == [1.2, 1.7]
        assert sum(line["heads"] for line in lines) == 4
