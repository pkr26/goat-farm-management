"""Health wire envelopes preserve text/date boundaries and native row adapters."""

from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import literal, select

from app.db import get_sessionmaker
from app.models import VaccineTemplate
from app.schemas.health import (
    HealthBulkTargetIn,
    HealthEventIn,
    HealthRoundTargetChangeIn,
    ScheduleTemplateOut,
)

from .conftest import owner_with_farm


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("product_name", 120),
        ("disease_target", 120),
        ("dose", 60),
        ("vet_name", 120),
        ("schedule_template_name", 120),
        ("next_due_authority", 120),
        ("product_lot", 120),
        ("certificate_number", 120),
        ("official_tag_number", 80),
        ("administered_by", 120),
    ],
)
def test_health_text_envelopes_accept_the_limit_and_reject_the_next_character(
    field: str, limit: int
) -> None:
    payload = {"animal_id": 1, "type": "VACCINE", field: "x" * limit}
    try:
        accepted = HealthEventIn.model_validate(payload)
    except ValidationError as exc:
        pytest.fail(f"The documented {field} wire limit must be accepted: {exc}")
    assert accepted.model_dump()[field] == "x" * limit
    with pytest.raises(ValidationError) as rejected:
        HealthEventIn.model_validate({**payload, field: "x" * (limit + 1)})
    assert any(
        error["loc"] == (field,) and error.get("ctx", {}).get("max_length") == limit
        for error in rejected.value.errors()
    )


def test_bulk_round_component_accepts_its_wire_limit_and_rejects_one_more() -> None:
    payload = {"scope": "bucket", "bucket": "FOUNDATION", "task_id": 1}
    try:
        accepted = HealthBulkTargetIn.model_validate({**payload, "round_component": "x" * 120})
    except ValidationError as exc:
        pytest.fail(f"The round component wire limit must be accepted: {exc}")
    assert accepted.round_component == "x" * 120
    with pytest.raises(ValidationError) as rejected:
        HealthBulkTargetIn.model_validate({**payload, "round_component": "x" * 121})
    assert any(error["loc"] == ("round_component",) for error in rejected.value.errors())


@pytest.mark.parametrize(
    "payload",
    [
        {"animal_ids": [1], "reason": "x"},
        {"animal_ids": list(range(1, 251)), "reason": "  Programme correction  "},
    ],
    ids=["single-target-short-reason", "full-bucket-trimmed-reason"],
)
def test_round_target_changes_accept_valid_boundary_payloads(payload: dict[str, Any]) -> None:
    try:
        accepted = HealthRoundTargetChangeIn.model_validate(payload)
    except ValidationError as exc:
        pytest.fail(f"A supported round correction must validate: {exc}")
    assert accepted.animal_ids == payload["animal_ids"]
    assert accepted.reason == payload["reason"].strip()


@pytest.mark.parametrize(
    "payload",
    [
        {"animal_ids": [], "reason": "Recorded correction"},
        {"animal_ids": [1], "reason": ""},
        {"animal_ids": [1], "reason": "  "},
        {"animal_ids": [1, 1], "reason": "Duplicate target"},
        {"animal_ids": list(range(1, 252)), "reason": "Too many targets"},
    ],
    ids=["empty-targets", "empty-reason", "blank-reason", "duplicate-targets", "oversize-bucket"],
)
def test_round_target_changes_reject_invalid_envelopes(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        HealthRoundTargetChangeIn.model_validate(payload)


@pytest.mark.parametrize(
    "dates",
    [
        {"product_manufactured_on": "2020-01-01", "product_expires_on": "2020-01-01"},
        {"product_expires_on": "2020-01-01"},
        {"product_expires_on": "2020-02-01", "vaccine_valid_until": "2020-02-01"},
        {"withdrawal_until": "2020-01-01"},
        {"withdrawal_until": (date(2020, 1, 1) + timedelta(days=730)).isoformat()},
        {
            "next_due_date": (date(2020, 1, 1) + timedelta(days=3650)).isoformat(),
            "schedule_template_name": "PPR",
            "next_due_authority": "Veterinarian's recorded programme",
        },
    ],
    ids=[
        "manufacture-expiry-same-day",
        "last-valid-product-day",
        "validity-at-product-expiry",
        "zero-withdrawal-period",
        "maximum-withdrawal-period",
        "maximum-next-dose-period",
    ],
)
def test_health_dates_accept_valid_equalities_and_inclusive_ceiling(
    dates: dict[str, Any],
) -> None:
    payload = {"animal_id": 1, "type": "VACCINE", "date": "2020-01-01", **dates}
    try:
        accepted = HealthEventIn.model_validate(payload)
    except ValidationError as exc:
        pytest.fail(f"Valid health evidence on the inclusive date boundary must validate: {exc}")
    wire = accepted.model_dump(mode="json")
    for field, value in payload.items():
        assert wire[field] == value


@pytest.mark.parametrize("next_days", [0, 3651], ids=["same-day-next-dose", "beyond-ten-years"])
def test_next_dose_requires_a_later_date_within_the_ten_year_ceiling(next_days: int) -> None:
    with pytest.raises(ValidationError):
        HealthEventIn.model_validate(
            {
                "animal_id": 1,
                "type": "VACCINE",
                "date": "2020-01-01",
                "next_due_date": (date(2020, 1, 1) + timedelta(days=next_days)).isoformat(),
                "schedule_template_name": "PPR",
                "next_due_authority": "Veterinarian's recorded programme",
            }
        )


@pytest.mark.parametrize(
    ("template_name", "event_type"),
    [("PPR", "VACCINE"), ("Deworming", "DEWORMING")],
    ids=["seeded-vaccine", "seeded-deworming"],
)
async def test_seeded_schedule_projection_preserves_the_native_attribute_adapter(
    client: httpx.AsyncClient, template_name: str, event_type: str
) -> None:
    owner = await owner_with_farm(client)
    response = await client.get("/api/health/schedule-templates", headers=owner)
    assert response.status_code == 200, response.text
    public = next(item for item in response.json()["templates"] if item["name"] == template_name)
    async with get_sessionmaker()() as db:
        # These persisted fields and the independently stated programme type
        # are a genuine PostgreSQL Row projection, with no synthetic object.
        row = (
            await db.execute(
                select(
                    VaccineTemplate.id,
                    VaccineTemplate.name,
                    VaccineTemplate.timing_note,
                    literal(event_type).label("event_type"),
                ).where(VaccineTemplate.name == template_name)
            )
        ).one()
        try:
            adapted = ScheduleTemplateOut.model_validate(row)
        except ValidationError as exc:
            pytest.fail(f"The seeded template projection must retain its wire adapter: {exc}")
    assert adapted.model_dump(mode="json") == public
    assert adapted.name == template_name and adapted.event_type == event_type
