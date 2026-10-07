"""Supported terminal-status payloads preserve text bounds and coherent facts."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.animals import StatusChangeIn
from app.utils import today


@pytest.mark.parametrize(
    "payload",
    [
        {"new_status": "DEAD"},
        {"new_status": "SOLD"},
        {"new_status": "CULLED"},
        {
            "new_status": "SOLD",
            "sale_price": 1200.25,
            "buyer_name": "  Village buyer  ",
            "sale_weight_kg": 25.0,
            "sale_price_per_kg": 48.01,
            "estimated_dob": "2020-01-01",
            "notes": "  Agreed sale  ",
        },
        {"new_status": "CULLED", "sale_price": 0, "buyer_name": "  Recorded buyer  "},
        {
            "new_status": "DEAD",
            "mortality_cause": "  Pneumonia  ",
            "mortality_cause_code": "PNEUMONIA",
            "disposal_method": "DEEP_BURIAL",
            "mortality_reported_at": "2020-01-01",
            "necropsy_done": True,
            "necropsy_findings": "  Inflammation  ",
            "suspected_scheduled_disease": True,
            "suspected_disease": "  PPR  ",
            "authority_notified_at": "2020-01-01",
        },
    ],
    ids=["plain-death", "plain-sale", "plain-cull", "priced-sale", "priced-cull", "medical-death"],
)
def test_supported_status_payloads_validate_and_preserve_their_facts(
    payload: dict[str, Any],
) -> None:
    try:
        validated = StatusChangeIn.model_validate(payload)
    except ValidationError as exc:
        pytest.fail(f"A supported terminal-status payload must validate: {exc}")
    result = validated.model_dump(mode="json")
    assert result["new_status"] == payload["new_status"]
    for field, value in payload.items():
        assert result[field] == (value.strip() if isinstance(value, str) else value)
    if "necropsy_done" not in payload:
        assert validated.necropsy_done is False
    if "suspected_scheduled_disease" not in payload:
        assert validated.suspected_scheduled_disease is False


@pytest.mark.parametrize(
    ("field", "limit", "base"),
    [
        ("buyer_name", 120, {"new_status": "SOLD"}),
        ("notes", 255, {"new_status": "DEAD"}),
        ("mortality_cause", 120, {"new_status": "DEAD"}),
        ("suspected_disease", 120, {"new_status": "DEAD", "suspected_scheduled_disease": True}),
    ],
    ids=["buyer", "notes", "mortality-cause", "suspected-disease"],
)
def test_status_text_accepts_the_wire_limit_and_rejects_one_more_character(
    field: str, limit: int, base: dict[str, Any]
) -> None:
    payload = {**base, field: "x" * limit}
    try:
        accepted = StatusChangeIn.model_validate(payload)
    except ValidationError as exc:
        pytest.fail(f"The documented {field} text limit must be accepted: {exc}")
    assert accepted.model_dump()[field] == "x" * limit
    with pytest.raises(ValidationError) as rejected:
        StatusChangeIn.model_validate({**base, field: "x" * (limit + 1)})
    assert any(
        error["loc"] == (field,) and error.get("ctx", {}).get("max_length") == limit
        for error in rejected.value.errors()
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"new_status": "DEAD", "sale_price_per_kg": 48.0},
        {"new_status": "CULLED", "sale_weight_kg": 25.0},
        {"new_status": "DEAD", "estimated_dob": "2020-01-01"},
        {"new_status": "DEAD", "necropsy_findings": "Inflammation"},
        {"new_status": "DEAD", "suspected_disease": "PPR"},
        {"new_status": "DEAD", "authority_notified_at": today().isoformat()},
    ],
    ids=[
        "death-sale-rate",
        "cull-sale-weight",
        "death-age-estimate",
        "findings-without-exam",
        "disease-without-suspicion",
        "notice-without-suspicion",
    ],
)
def test_status_schema_rejects_facts_that_belong_to_another_workflow(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        StatusChangeIn.model_validate(payload)
