"""Actual backward-planner calendar primitives preserve dates and reject malformed targets."""

from collections.abc import Callable

import pytest

from app.simulation.backward_planner import month_label, month_offset, parse_year_month


def _completed[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A supported real planner calendar operation must complete: {error}")


@pytest.mark.parametrize(
    "label,expected",
    [
        ("1900-01", (1900, 1)),
        ("2200-12", (2200, 12)),
        ("2026-01", (2026, 1)),
        ("2026-12", (2026, 12)),
    ],
    ids=["first-planner-year", "last-planner-year", "january", "december"],
)
def test_real_planner_calendar_endpoint_parses(label: str, expected: tuple[int, int]) -> None:
    assert _completed(lambda: parse_year_month(label)) == expected


@pytest.mark.parametrize(
    "label", ["2026-00", "2026-13", "1899-12", "2201-01", "2026-1", "2026-01-extra"]
)
def test_invalid_planner_month_is_a_controlled_value_error(label: str) -> None:
    try:
        parse_year_month(label)
    except ValueError:
        pass
    except (ArithmeticError, AttributeError, LookupError, TypeError) as error:
        pytest.fail(f"A malformed planner target must receive a controlled rejection: {error}")
    else:
        pytest.fail("Malformed or unsupported target dates must be rejected")


@pytest.mark.parametrize(
    "start,target,expected",
    [("2026-12", "2027-01", 2), ("2026-01", "2027-01", 13), ("2026-03", "2026-08", 6)],
    ids=["first-allowed-sale-across-year", "same-month-next-year", "within-year"],
)
def test_actual_target_month_offset_matches_one_based_calendar(
    start: str, target: str, expected: int
) -> None:
    offset = _completed(lambda: month_offset(start, target))
    assert offset == expected
    assert _completed(lambda: month_label(start, offset)) == target


@pytest.mark.parametrize("target", ["2026-12", "2026-11"], ids=["start-month", "past-month"])
def test_no_lead_time_target_is_rejected_deliberately(target: str) -> None:
    try:
        month_offset("2026-12", target)
    except ValueError:
        pass
    except (ArithmeticError, AttributeError, LookupError, TypeError) as error:
        pytest.fail(f"A target without lead time must receive a controlled rejection: {error}")
    else:
        pytest.fail("A sale cannot be planned at or before the start month")


@pytest.mark.parametrize(
    "start,month,label",
    [
        ("1900-01", 1, "1900-01"),
        ("2026-01", 12, "2026-12"),
        ("2026-12", 2, "2027-01"),
        ("2026-12", 13, "2027-12"),
        ("2199-12", 2, "2200-01"),
    ],
    ids=[
        "anchor-month",
        "twelfth-month",
        "next-year",
        "thirteenth-month",
        "last-supported-calendar-year",
    ],
)
def test_stage_plan_month_has_the_actual_calendar_label(start: str, month: int, label: str) -> None:
    assert _completed(lambda: month_label(start, month)) == label
