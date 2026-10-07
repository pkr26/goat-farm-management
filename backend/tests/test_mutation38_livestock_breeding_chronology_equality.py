"""Breeding chronology rejects prior dates and preserves equality's domain meaning."""

from datetime import timedelta

import httpx
import pytest

from app.db import get_sessionmaker
from app.models import Animal, Farm
from app.services.breeding import (
    BreedingChronologyError,
    breeding_weights_as_of,
    create_breeding_record,
    doe_has_open_breeding,
)
from app.utils import today

from .conftest import owner_with_farm
from .test_breeding_extended import make_animal


async def test_native_service_on_birth_day_reports_eligibility_not_prior_birth_chronology(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    current = today()
    doe_birth = current - timedelta(days=400)
    doe = await make_animal(
        client,
        owner,
        "BIRTH-EQUALITY-DOE",
        date_of_birth=doe_birth.isoformat(),
        weight_kg=26.0,
        weight_date=(current - timedelta(days=1)).isoformat(),
    )
    buck = await make_animal(
        client,
        owner,
        "BIRTH-EQUALITY-SIRE",
        sex="M",
        bucket="BREEDING",
        date_of_birth=(current - timedelta(days=800)).isoformat(),
        weight_kg=30.0,
        weight_date=(current - timedelta(days=1)).isoformat(),
    )
    # The HTTP route has an earlier eligibility precheck, so this is the
    # native exported service's error classification contract. The animal
    # rows and bounded weight/pregnancy facts still come from real SQL.
    async with get_sessionmaker()() as db:
        farm = await db.get(Farm, int(owner["X-Farm-Id"]))
        doe_row = await db.get(Animal, doe["id"])
        buck_row = await db.get(Animal, buck["id"])
        assert farm is not None and doe_row is not None and buck_row is not None
        weights = await breeding_weights_as_of(db, [doe["id"], buck["id"]], doe_birth)
        open_breeding = await doe_has_open_breeding(db, farm.id, doe["id"])
        try:
            await create_breeding_record(
                db,
                farm,
                doe_row,
                buck_row,
                doe_birth,
                doe_latest_weight_kg=weights.get(doe["id"]),
                has_open_breeding=open_breeding,
            )
        except BreedingChronologyError as error:
            pytest.fail(f"DOB equality is not a prior-birth chronology error: {error!r}")
        except ValueError:
            pass
        except Exception as error:
            pytest.fail(f"valid native date must produce its eligibility ValueError: {error!r}")
        else:
            pytest.fail("a newborn at the recorded service date cannot be eligible")
    history = await client.get("/api/breeding", headers=owner)
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 0


async def test_historical_arrival_day_service_is_valid_for_eligible_imported_adults(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client)
    current = today()
    arrival = current - timedelta(days=90)
    dob = current - timedelta(days=800)
    doe = await make_animal(
        client,
        owner,
        "ARRIVAL-EQUALITY-DOE",
        date_of_birth=dob.isoformat(),
        purchase_date=arrival.isoformat(),
        weight_kg=26.0,
        weight_date=arrival.isoformat(),
    )
    buck = await make_animal(
        client,
        owner,
        "ARRIVAL-EQUALITY-SIRE",
        sex="M",
        bucket="BREEDING",
        date_of_birth=dob.isoformat(),
        purchase_date=arrival.isoformat(),
        weight_kg=30.0,
        weight_date=arrival.isoformat(),
    )
    response = await client.post(
        "/api/breeding",
        headers=owner,
        json={
            "doe_id": doe["id"],
            "buck_id": buck["id"],
            "breeding_date": arrival.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["breeding_date"] == arrival.isoformat()
    assert response.json()["doe_id"] == doe["id"]
    assert response.json()["buck_id"] == buck["id"]
    history = await client.get("/api/breeding", headers=owner)
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 1
