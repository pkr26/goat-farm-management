"""Native feed boundaries reject unrepresentable mass and preserve zero-price accounting."""

from decimal import Decimal

import pytest

from app.models.enums import FeedingShift
from app.services.feeding import (
    _allocate_recipe_grams,
    _positive_kg,
    _restock_money,
    _shift_quantities,
)


@pytest.mark.parametrize("value,expected", [(0.0005, 0.001), (0.001, 0.001), (1.2345, 1.235)])
def test_positive_stock_quantities_round_to_the_literal_whole_gram(
    value: float, expected: float
) -> None:
    try:
        actual = _positive_kg(value, "Purchased stock")
    except Exception as exc:
        pytest.fail(f"A valid representable stock quantity was rejected: {exc!r}")
    assert actual == expected


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf"), -float("inf")])
def test_invalid_native_stock_quantity_reports_a_value_error(value: float) -> None:
    try:
        _positive_kg(value, "Purchased stock")
    except ValueError as exc:
        assert str(exc) == "Purchased stock must be a positive finite number"
    except Exception as exc:
        pytest.fail(f"A rejected native quantity needs its documented ValueError: {exc!r}")
    else:
        pytest.fail("A nonpositive/nonfinite quantity was accepted")


def test_half_gram_floor_and_exact_two_ingredient_batch_admission() -> None:
    with pytest.raises(ValueError, match=r"at least 0\.001 kg after rounding"):
        _positive_kg(0.00049, "Purchased stock")
    try:
        allocation = _allocate_recipe_grams([Decimal(50), Decimal(50)], 2)
    except Exception as exc:
        pytest.fail(f"Two positive ingredient grams are representable: {exc!r}")
    assert allocation == [1, 1]


def test_small_shift_ration_uses_actual_largest_residuals_and_enum_ties() -> None:
    assert _shift_quantities(0.247) == {
        FeedingShift.MORNING: 0.099,
        FeedingShift.AFTERNOON: 0.049,
        FeedingShift.NIGHT: 0.099,
    }
    assert _shift_quantities(1.001) == {
        FeedingShift.MORNING: 0.401,
        FeedingShift.AFTERNOON: 0.2,
        FeedingShift.NIGHT: 0.4,
    }


def test_native_restock_preserves_zero_price_and_rejects_unrepresentable_positive_money() -> None:
    try:
        free = _restock_money(1.0, 0.0)
    except Exception as exc:
        pytest.fail(f"A genuine zero-price purchase is valid accounting: {exc!r}")
    assert free == (Decimal("0.00"), Decimal("0.00"))
    assert _restock_money(2.0, 0.01) == (Decimal("0.01"), Decimal("0.02"))
    with pytest.raises(ValueError, match="non-negative finite"):
        _restock_money(1.0, -1.0)
    with pytest.raises(ValueError, match=r"at least ₹0\.01 per kg"):
        _restock_money(1.0, 0.004)
    with pytest.raises(ValueError, match=r"total at least ₹0\.01"):
        _restock_money(0.001, 0.01)
