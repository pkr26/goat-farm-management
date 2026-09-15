"""Cross-record chronology regressions for animal and reproductive lifecycles."""

from datetime import timedelta

import httpx

from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import (
    all_tasks,
    get_animal,
    make_breeding,
    make_buck,
    make_doe,
    make_kidding,
    post_abort,
    post_breeding,
    tasks_by_category,
    ultrasound,
)


def iso_days_ago(days: int) -> str:
    return (today() - timedelta(days=days)).isoformat()


async def test_failed_cycle_blocks_backdated_or_same_boundary_rebreeding(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, "CHRON-FAILED")
    buck = await make_buck(client, owner, "CHRON-FAILED-BUCK")
    first = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=iso_days_ago(80),
    )
    result_date = today() - timedelta(days=45)
    failed = await ultrasound(
        client,
        owner,
        first["id"],
        pregnant=False,
        kid_count=None,
        date=result_date.isoformat(),
    )
    assert failed.status_code == 200, failed.text

    for rejected_date in (today() - timedelta(days=60), result_date):
        response = await post_breeding(
            client,
            owner,
            doe["id"],
            buck["id"],
            breeding_date=rejected_date.isoformat(),
            heat_cycle_number=2,
        )
        assert response.status_code == 409, response.text
        assert "latest reproductive event" in response.json()["detail"]

    accepted = await post_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=(result_date + timedelta(days=1)).isoformat(),
        heat_cycle_number=2,
    )
    assert accepted.status_code == 201, accepted.text


async def test_loss_boundary_blocks_overlapping_rebreeding(client: httpx.AsyncClient) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, "CHRON-LOSS")
    buck = await make_buck(client, owner, "CHRON-LOSS-BUCK")
    first = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=iso_days_ago(100),
    )
    confirmation_date = today() - timedelta(days=65)
    confirmed = await ultrasound(
        client,
        owner,
        first["id"],
        pregnant=True,
        kid_count=1,
        date=confirmation_date.isoformat(),
    )
    assert confirmed.status_code == 200, confirmed.text
    loss_date = today() - timedelta(days=30)
    loss = await post_abort(
        client,
        owner,
        first["id"],
        loss_date=loss_date.isoformat(),
        cause="UNKNOWN",
    )
    assert loss.status_code == 200, loss.text

    overlap = await post_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=iso_days_ago(40),
        heat_cycle_number=2,
    )
    assert overlap.status_code == 409, overlap.text
    same_day = await post_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=loss_date.isoformat(),
        heat_cycle_number=2,
    )
    assert same_day.status_code == 409, same_day.text


async def test_kidding_boundary_blocks_historical_insertion_after_completed_cycle(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, "CHRON-KIDDED")
    buck = await make_buck(client, owner, "CHRON-KIDDED-BUCK")
    first = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=iso_days_ago(220),
    )
    confirmed = await ultrasound(
        client,
        owner,
        first["id"],
        pregnant=True,
        kid_count=1,
        date=iso_days_ago(185),
    )
    assert confirmed.status_code == 200, confirmed.text
    kidding_date = today() - timedelta(days=70)
    await make_kidding(
        client,
        owner,
        first["id"],
        date=kidding_date.isoformat(),
        kids=[{"sex": "F", "tag": "CHRON-KID"}],
    )
    weaning = tasks_by_category(await all_tasks(client, owner), "WEANING")
    assert len(weaning) == 1
    completed = await client.post(f"/api/tasks/{weaning[0]['id']}/complete", headers=owner)
    assert completed.status_code == 200, completed.text

    overlap = await post_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=(kidding_date - timedelta(days=1)).isoformat(),
    )
    assert overlap.status_code == 409, overlap.text
    boundary = await post_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=kidding_date.isoformat(),
    )
    assert boundary.status_code == 409, boundary.text


async def test_doe_status_cannot_be_backdated_before_recorded_kidding(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, "STATUS-DOE")
    buck = await make_buck(client, owner, "STATUS-DOE-BUCK")
    breeding = await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=iso_days_ago(180),
    )
    confirmation = await ultrasound(
        client,
        owner,
        breeding["id"],
        pregnant=True,
        kid_count=1,
        date=iso_days_ago(145),
    )
    assert confirmation.status_code == 200, confirmation.text
    kidding_date = today() - timedelta(days=30)
    await make_kidding(
        client,
        owner,
        breeding["id"],
        date=kidding_date.isoformat(),
        kids=[{"sex": "F", "tag": "STATUS-DOE-KID"}],
    )

    response = await client.post(
        f"/api/animals/{doe['id']}/status",
        json={"new_status": "DEAD", "date": (kidding_date - timedelta(days=1)).isoformat()},
        headers=owner,
    )
    assert response.status_code == 422, response.text
    assert "latest recorded lifecycle event" in response.json()["detail"]
    assert (await get_animal(client, owner, doe["id"]))["status"] == "ACTIVE"


async def test_sire_status_cannot_be_backdated_before_recorded_service(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    doe = await make_doe(client, owner, "STATUS-SIRE-DOE")
    buck = await make_buck(client, owner, "STATUS-SIRE")
    breeding_date = today() - timedelta(days=10)
    await make_breeding(
        client,
        owner,
        doe["id"],
        buck["id"],
        breeding_date=breeding_date.isoformat(),
    )

    response = await client.post(
        f"/api/animals/{buck['id']}/status",
        json={"new_status": "SOLD", "date": (breeding_date - timedelta(days=1)).isoformat()},
        headers=owner,
    )
    assert response.status_code == 422, response.text
    assert (await get_animal(client, owner, buck["id"]))["status"] == "ACTIVE"


async def test_status_cannot_predate_later_weight_or_health_fact(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    animal = await make_doe(client, owner, "STATUS-FACTS")
    weight_date = today() - timedelta(days=5)
    weight = await client.post(
        f"/api/animals/{animal['id']}/weight",
        json={"date": weight_date.isoformat(), "weight_kg": 27},
        headers=owner,
    )
    assert weight.status_code == 201, weight.text
    health_date = today() - timedelta(days=3)
    health = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal["id"],
            "date": health_date.isoformat(),
            "type": "TREATMENT",
        },
        headers=owner,
    )
    assert health.status_code == 201, health.text

    response = await client.post(
        f"/api/animals/{animal['id']}/status",
        json={"new_status": "CULLED", "date": (health_date - timedelta(days=1)).isoformat()},
        headers=owner,
    )
    assert response.status_code == 422, response.text


async def test_chronology_fact_probes_are_farm_scoped(client: httpx.AsyncClient) -> None:
    """Every lifecycle-fact subquery carries the tenant predicate.

    The weight and bucket-move probes used to filter by animal_id alone while
    their health/kidding/breeding siblings also pinned farm_id; on a
    multi-tenant schema the fact queries must all scope by farm (defense in
    depth — a stray cross-tenant row must never move another farm's
    chronology boundary).
    """
    from sqlalchemy import event

    from app.db import get_engine

    owner = await owner_with_farm(client)
    animal = await make_doe(client, owner, "STATUS-SCOPED")

    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement.lower())

    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        response = await client.post(
            f"/api/animals/{animal['id']}/status",
            json={"new_status": "CULLED", "date": today().isoformat()},
            headers=owner,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)
    assert response.status_code == 200, response.text

    def fact_probes(aggregate: str) -> list[str]:
        # Only the chronology fact subqueries: the scalar max()/min() probes
        # (other readers filter these tables by animal_id alone on purpose).
        return [statement for statement in statements if aggregate in statement]

    for aggregate, table in (
        ("max(weight_records.date)", "weight_records"),
        ("min(weight_records.date)", "weight_records"),
        ("max(bucket_moves.effective_date)", "bucket_moves"),
        ("min(bucket_moves.effective_date)", "bucket_moves"),
    ):
        probes = fact_probes(aggregate)
        for probe in probes:
            assert f"{table}.farm_id" in probe, probe
    # The status change itself runs the max() probes; both were captured.
    assert fact_probes("max(weight_records.date)")
    assert fact_probes("max(bucket_moves.effective_date)")
