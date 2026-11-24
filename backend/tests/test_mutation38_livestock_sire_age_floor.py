"""A historical BREEDING import is gated by the age floor for its own sex."""

from dataclasses import replace
from datetime import date

import httpx
import pytest

from app.api import animals as animals_api
from app.models.species import GOAT_PROFILE

from .conftest import owner_with_farm

# Fourteen completed months on the frozen farm date: between the doe floor
# (12) and the deliberately raised sire floor (18), so the sexes must disagree.
FOURTEEN_MONTHS_OLD = "2024-11-15"


def _reference_date(_timezone: str = "Asia/Kolkata") -> date:
    return date(2026, 1, 15)


@pytest.mark.parametrize(
    ("sex", "expected_status", "expected_floor"),
    [("M", 422, 18), ("F", 201, 12)],
    ids=["buck-below-the-sire-floor", "doe-above-the-doe-floor"],
)
async def test_historical_breeding_entry_reads_the_age_floor_for_its_own_sex(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    sex: str,
    expected_status: int,
    expected_floor: int,
) -> None:
    """Sire and doe age floors are independent profile knobs.

    The shipped profile happens to set both to 12, which hides which arm the
    registration gate reads. Raising only the sire floor — exactly what the
    sister weight knobs already do (25 kg buck vs 22 kg doe) — makes the two
    arms observable: a 14-month buck must be refused on the sire floor while a
    14-month doe is admitted on the doe floor.
    """
    owner = await owner_with_farm(client, email=f"sire-age-floor-{sex}@farm.in")
    monkeypatch.setattr(animals_api, "today", _reference_date)
    monkeypatch.setattr(
        animals_api, "GOAT_PROFILE", replace(GOAT_PROFILE, min_sire_breeding_age_months=18)
    )
    response = await client.post(
        "/api/animals",
        headers=owner,
        json={
            "tag_number": f"SIRE-AGE-{sex}",
            "sex": sex,
            "source": "BORN",
            "current_bucket": "BREEDING",
            "historical_import_reason": "Reconcile the signed annual herd register",
            "date_of_birth": FOURTEEN_MONTHS_OLD,
            # 30 kg clears both the 22 kg doe and 25 kg sire weight floors, so
            # age is the only discriminator left in the gate.
            "weight_kg": 30,
            "weight_date": "2026-01-15",
        },
    )
    assert response.status_code == expected_status, response.text
    if expected_status == 422:
        assert f"age at least {expected_floor} months" in response.json()["detail"]
    else:
        animal = response.json()
        assert animal["age_months"] == 14
        assert animal["current_bucket"] == "BREEDING"
        assert animal["latest_weight_kg"] == 30
