"""Public prose notes reject DEL/C1 without rejecting adjacent benign text."""

import httpx
import pytest

from .conftest import owner_with_farm
from .type_helpers import json_object


@pytest.mark.parametrize(
    ("character", "expected_status"),
    [
        pytest.param("\x7f", 422, id="DEL"),
        pytest.param("\u009f", 422, id="C1_UPPER"),
        pytest.param("~", 201, id="ASCII_TILDE"),
        pytest.param("\u00a0", 201, id="NBSP"),
        pytest.param("\u202e", 422, id="RLO"),
        pytest.param("\u202a", 422, id="LRE"),
        pytest.param("\u202f", 201, id="NARROW_NBSP"),
    ],
)
async def test_public_animal_notes_respect_shared_text_boundaries(
    client: httpx.AsyncClient, character: str, expected_status: int
) -> None:
    owner = await owner_with_farm(client)
    # Omitting the optional tag uses real server-generated unique tags, so both
    # public POST bodies can differ only in notes without a duplicate-tag error.
    payload: dict[str, object] = {
        "sex": "F",
        "source": "PURCHASED",
        "current_bucket": "FOUNDATION",
        "historical_import_reason": "Public shared-text boundary regression",
        "notes": "Shared text notes",
    }
    plain = await client.post("/api/animals", json=payload, headers=owner)
    assert plain.status_code == 201, "Plain historical-import notes must be accepted"
    plain_body = json_object(plain.json())
    assert plain_body.get("notes") == "Shared text notes", "Plain notes must be returned intact"

    notes = f"Shared text{character}notes"
    response = await client.post("/api/animals", json={**payload, "notes": notes}, headers=owner)
    assert response.status_code == expected_status, (
        "Animal prose notes must reject DEL/C1 and accept adjacent benign characters"
    )
    body = json_object(response.json())
    if expected_status == 422:
        detail = body.get("detail")
        assert isinstance(detail, list) and detail, "Rejected notes must report validation details"
        for value in detail:
            error = json_object(value)
            assert error.get("loc") == ["body", "notes"], "Only notes may fail validation"
            assert error.get("type") == "value_error", "Notes must fail the text validation rule"
            message = error.get("msg")
            assert isinstance(message, str) and "control" in message, (
                "Rejected notes must identify the control-character rule"
            )
    else:
        assert body.get("notes") == notes, "Accepted interior note characters must remain intact"
