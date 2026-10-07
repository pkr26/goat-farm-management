"""Movement clearance evidence preserves a bounded, stripped reference."""

import pytest
from pydantic import ValidationError

from app.schemas.health import MovementRestrictionClearIn


@pytest.mark.parametrize(
    "reference",
    ["x", "  Veterinarian clearance record  ", "x" * 255],
    ids=["shortest-reference", "stripped-reference", "longest-reference"],
)
def test_restriction_clearance_accepts_valid_reference_boundaries(reference: str) -> None:
    try:
        accepted = MovementRestrictionClearIn.model_validate(
            {"clearance_reference": reference, "expected_restriction_version": 1}
        )
    except ValidationError as exc:
        pytest.fail(f"A valid movement clearance reference must validate: {exc}")
    assert accepted.clearance_reference == reference.strip()
    assert accepted.expected_restriction_version == 1


@pytest.mark.parametrize(
    "reference",
    ["", "  ", "x" * 256],
    ids=["empty-reference", "blank-reference", "overlong-reference"],
)
def test_restriction_clearance_rejects_missing_or_overlong_evidence(reference: str) -> None:
    with pytest.raises(ValidationError) as rejected:
        MovementRestrictionClearIn.model_validate(
            {"clearance_reference": reference, "expected_restriction_version": 1}
        )
    assert any(error["loc"] == ("clearance_reference",) for error in rejected.value.errors())
