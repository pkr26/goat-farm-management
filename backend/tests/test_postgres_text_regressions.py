"""Findings #7-#10: client text must fail before an asyncpg NUL-byte write."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.animals import AnimalCreateIn, MoveIn, StatusChangeIn, WeightIn
from app.schemas.auth import FarmCreateIn, RegisterIn
from app.schemas.breeding import PregnancyLossIn
from app.schemas.health import HealthEventIn, MovementRestrictionClearIn
from app.schemas.kidding import KiddingCreateIn, KidIn
from app.schemas.team import RoleIn, WorkerCreateIn
from app.utils import today

from .conftest import owner_with_farm

SchemaCase = tuple[type[BaseModel], Callable[[str], dict[str, Any]], str]


SCHEMA_CASES: list[SchemaCase] = [
    *(
        (
            AnimalCreateIn,
            lambda bad, field=field: {
                "tag_number": "NUL-ANIMAL",
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
                "historical_import_reason": "Existing herd",
                field: bad,
            },
            field,
        )
        for field in (
            "tag_number",
            "name",
            "breed",
            "seller_name",
            "notes",
            "historical_import_reason",
        )
    ),
    (WeightIn, lambda bad: {"weight_kg": 20.0, "notes": bad}, "notes"),
    (MoveIn, lambda bad: {"to_bucket": "FOUNDATION", "reason": bad}, "reason"),
    *(
        (
            StatusChangeIn,
            lambda bad, field=field: {"new_status": "SOLD", field: bad},
            field,
        )
        for field in ("buyer_name", "notes")
    ),
    (
        StatusChangeIn,
        lambda bad: {"new_status": "DEAD", "mortality_cause": bad},
        "mortality_cause",
    ),
    (
        StatusChangeIn,
        lambda bad: {
            "new_status": "DEAD",
            "suspected_scheduled_disease": True,
            "suspected_disease": bad,
        },
        "suspected_disease",
    ),
    (
        RegisterIn,
        lambda bad: {"email": "nul-register@farm.in", "password": "password123", "name": bad},
        "name",
    ),
    (FarmCreateIn, lambda bad: {"name": bad}, "name"),
    (FarmCreateIn, lambda bad: {"name": "Farm", "location": bad}, "location"),
    (
        PregnancyLossIn,
        lambda bad: {"loss_date": today(), "cause": "OTHER", "notes": bad},
        "notes",
    ),
    (KidIn, lambda bad: {"tag": bad, "sex": "F"}, "tag"),
    (
        KiddingCreateIn,
        lambda bad: {
            "breeding_record_id": 1,
            "date": today(),
            "notes": bad,
            "kids": [{"sex": "F"}],
        },
        "notes",
    ),
    (
        WorkerCreateIn,
        lambda bad: {"email": "nul-worker@farm.in", "role_id": 1, "name": bad},
        "name",
    ),
    (RoleIn, lambda bad: {"name": bad}, "name"),
    (RoleIn, lambda bad: {"name": "Role", "description": bad}, "description"),
    (
        MovementRestrictionClearIn,
        lambda bad: {"clearance_reference": bad, "expected_restriction_version": 1},
        "clearance_reference",
    ),
    *(
        (
            HealthEventIn,
            lambda bad, field=field: {"animal_id": 1, "type": "VACCINE", field: bad},
            field,
        )
        for field in (
            "product_name",
            "disease_target",
            "dose",
            "route",
            "vet_name",
            "schedule_template_name",
            "next_due_authority",
            "product_lot",
            "certificate_number",
            "official_tag_number",
            "administered_by",
            "notes",
        )
    ),
]


@pytest.mark.parametrize(("schema", "payload", "field"), SCHEMA_CASES)
def test_every_persisted_free_text_field_rejects_nul(
    schema: type[BaseModel], payload: Callable[[str], dict[str, Any]], field: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        schema.model_validate(payload("bad\x00text"))
    assert any(field in error["loc"] for error in caught.value.errors())


async def test_nul_is_a_clean_422_at_each_reported_api_boundary(
    client: httpx.AsyncClient,
) -> None:
    unauthenticated = await client.post(
        "/api/auth/register",
        json={
            "email": "nul-api@farm.in",
            "password": "password123",
            "name": "bad\x00name",
        },
    )
    assert unauthenticated.status_code == 422, unauthenticated.text

    owner = await owner_with_farm(client)
    animal = await client.post(
        "/api/animals",
        json={
            "tag_number": "NUL-API",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Existing herd",
            "notes": "bad\x00notes",
        },
        headers=owner,
    )
    assert animal.status_code == 422, animal.text

    health = await client.post(
        "/api/health/events",
        json={"animal_id": 1, "type": "VACCINE", "product_name": "bad\x00product"},
        headers=owner,
    )
    assert health.status_code == 422, health.text


@pytest.mark.parametrize(
    ("path", "extra_params"),
    [
        ("/api/animals", {}),
        ("/api/breeding/candidates", {"kind": "doe"}),
        ("/api/health/animals", {}),
        ("/api/health/purchase-batches", {}),
    ],
)
async def test_nul_search_terms_never_reach_postgres(
    client: httpx.AsyncClient, path: str, extra_params: dict[str, str]
) -> None:
    owner = await owner_with_farm(client)
    response = await client.get(path, params=extra_params | {"q": "\x00"}, headers=owner)
    assert response.status_code == 422, response.text
