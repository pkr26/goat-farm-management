"""Mutation-campaign gap tests for feed shift allocation (2026-09-30).

`_shift_quantities` distributes whole grams across the 40/20/40 shifts by
largest remainder; its percentage scaling and remainder ranking mutated
freely because no test pinned an exact uneven split.
"""

from app.models.constants import SHIFT_SPLIT
from app.models.enums import FeedingShift
from app.services.feeding import _shift_quantities


def test_shift_split_proportions_pinned() -> None:
    assert SHIFT_SPLIT == {
        FeedingShift.MORNING: 0.4,
        FeedingShift.AFTERNOON: 0.2,
        FeedingShift.NIGHT: 0.4,
    }


def test_shift_split_even_total_distributes_exactly() -> None:
    # 1005 g splits cleanly: 402/201/402.
    assert _shift_quantities(1.005) == {
        FeedingShift.MORNING: 0.402,
        FeedingShift.AFTERNOON: 0.201,
        FeedingShift.NIGHT: 0.402,
    }


def test_shift_split_uneven_total_uses_largest_remainder() -> None:
    # 1001 g: floor allocation 400/200/400 leaves 1 g; every remainder ties
    # at .40/.20/.40 numerators mod 100 = 40/20/40, so the deterministic
    # enum-order tie-break gives the gram to MORNING.
    assert _shift_quantities(1.001) == {
        FeedingShift.MORNING: 0.401,
        FeedingShift.AFTERNOON: 0.2,
        FeedingShift.NIGHT: 0.4,
    }


def test_shift_split_never_invents_or_loses_grams() -> None:
    for kg in (0.003, 0.027, 3.7, 12.345):
        split = _shift_quantities(kg)
        allocated_g = round(sum(split.values()) * 1000)
        assert allocated_g == round(kg * 1000), kg
