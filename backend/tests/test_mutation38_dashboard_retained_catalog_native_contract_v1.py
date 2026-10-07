"""A native retained herd count stays factual when one reference definition is absent."""

import httpx
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import BucketDefinition

from .conftest import owner_with_farm
from .test_finance_extended import make_animal


async def test_retained_animals_do_not_gain_a_phantom_count_from_an_absent_definition(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="mutation-dashboard-retained-catalog@farm.in")
    for index in range(2):
        await make_animal(client, owner, tag=f"RETAINED-CATALOG-{index}", bucket="FOUNDATION")
    async with get_sessionmaker()() as db:
        definition = (
            await db.execute(select(BucketDefinition).where(BucketDefinition.code == "FOUNDATION"))
        ).scalar_one()
        # This is an actual constraint-valid retained reference-table state in
        # the isolated native database, not a claim that the HTTP UI can delete
        # lifecycle definitions. The persisted animals and their enum-valued
        # bucket remain real; the operational count must remain factual.
        await db.delete(definition)
        await db.commit()
    response = await client.get("/api/dashboard", headers=owner)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["total_active"] == 2, "Two retained animals cannot become three on a read"
    assert document["sex_counts"] == {"M": 0, "F": 2}
    assert all(row["code"] != "FOUNDATION" for row in document["buckets"])
