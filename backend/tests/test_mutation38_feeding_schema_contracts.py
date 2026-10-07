"""Feeding input envelopes preserve recipe identifiers and bounded rations."""

import pytest
from pydantic import ValidationError

from app.schemas.feeding import DispenseIn, FeedSettingIn, MixIn


@pytest.mark.parametrize("code", ["A", "x" * 30], ids=["shortest-code", "longest-code"])
def test_recipe_code_accepts_its_native_wire_boundaries(code: str) -> None:
    try:
        mix = MixIn.model_validate({"recipe_code": code, "batch_kg": 1})
        dispense = DispenseIn.model_validate(
            {"bucket": "FOUNDATION", "shift": "MORNING", "recipe_code": code, "qty_kg": 1}
        )
    except ValidationError as exc:
        pytest.fail(f"A recipe identifier within its declared wire bounds must validate: {exc}")
    assert mix.recipe_code == dispense.recipe_code == code
    assert mix.batch_kg == dispense.qty_kg == 1


def test_dispensed_recipe_strips_ordinary_spaces_and_retains_the_identifier() -> None:
    try:
        dispense = DispenseIn.model_validate(
            {
                "bucket": "FOUNDATION",
                "shift": "MORNING",
                "recipe_code": "  DRY_ROUGHAGE  ",
                "qty_kg": 1,
            }
        )
    except ValidationError as exc:
        pytest.fail(f"An ordinary printable dispensing identifier must validate: {exc}")
    assert dispense.recipe_code == "DRY_ROUGHAGE"


@pytest.mark.parametrize(
    "code",
    ["", "x" * 31, "A\x00", "A\x7f", "A\n"],
    ids=["empty-code", "overlong-code", "nul-code", "del-code", "newline-code"],
)
def test_recipe_code_rejects_invalid_native_envelopes(code: str) -> None:
    with pytest.raises(ValidationError) as rejected:
        MixIn.model_validate({"recipe_code": code, "batch_kg": 1})
    assert any(error["loc"] == ("recipe_code",) for error in rejected.value.errors())


def test_whitespace_only_dispensing_code_has_no_recipe_identity() -> None:
    with pytest.raises(ValidationError):
        DispenseIn.model_validate(
            {"bucket": "FOUNDATION", "shift": "MORNING", "recipe_code": "   ", "qty_kg": 1}
        )


@pytest.mark.parametrize("quantity", [0.001, 50.0], ids=["whole-gram-floor", "ration-ceiling"])
def test_native_feed_rations_accept_supported_inclusive_boundaries(quantity: float) -> None:
    try:
        accepted = FeedSettingIn.model_validate(
            {"bucket": "FOUNDATION", "daily_kg_per_head": quantity}
        )
    except ValidationError as exc:
        pytest.fail(f"A supported whole-gram ration override must validate: {exc}")
    assert accepted.daily_kg_per_head == quantity


@pytest.mark.parametrize("quantity", [0.0, 50.001], ids=["zero-ration", "above-ration-ceiling"])
def test_native_feed_rations_reject_absent_or_excessive_daily_feed(quantity: float) -> None:
    with pytest.raises(ValidationError):
        FeedSettingIn.model_validate({"bucket": "FOUNDATION", "daily_kg_per_head": quantity})
