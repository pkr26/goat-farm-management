"""Actual supported forecast dates, release-coverage warnings and operator calendars."""

import json

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import run_simulation


@pytest.mark.parametrize(
    "start,horizon,beyond",
    [
        ("2049-08", 17, False),
        ("2049-08", 18, True),
        ("2050-12", 12, True),
        ("2048-12", 13, False),
    ],
)
def test_actual_calendar_coverage_warning_tracks_the_last_inclusive_forecast_month(
    start: str,
    horizon: int,
    beyond: bool,
) -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps({"meta": {"start_year_month": start, "horizon_months": horizon}})
    )
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A supported real dated forecast must return its complete result: {exc}")
    assert len(result.months) == horizon
    first_month = int(start[5:7])
    assert [row.calendar_month for row in result.months] == [
        (first_month - 1 + offset) % 12 + 1 for offset in range(horizon)
    ]
    coverage = [warning for warning in result.warnings if "calendar covers through" in warning]
    assert bool(coverage) is beyond
    if beyond:
        assert len(coverage) == 1
        assert "through 2050" in coverage[0] and "no Bakrid uplift" in coverage[0]
    assert any("projections requiring local confirmation" in warning for warning in result.warnings)


@pytest.mark.parametrize("eid_month,expected", [(11, "12"), (12, "1")])
def test_actual_explicit_legacy_calendar_reports_only_months_inside_its_declared_window(
    eid_month: int,
    expected: str,
) -> None:
    # No embedded date is guessed after 2050. A user's supported legacy
    # calendar remains explicit, including the first and final run months.
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"start_year_month": "2051-12", "horizon_months": 12},
                "sales": {"eid_month": eid_month, "festival_sale_months": None},
            }
        )
    )
    assert assumptions.sales.festival_sale_months is None
    try:
        result = run_simulation(assumptions, with_break_even=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A supported actual legacy operator calendar must complete: {exc}")
    reported = [
        section.figures["festival_months"]
        for section in result.narrative_report
        if "festival_months" in section.figures
    ]
    assert reported == [expected]
    assert not any("calendar covers through" in warning for warning in result.warnings)


def test_native_user_override_before_the_window_is_ignored_without_rejecting_the_valid_plan() -> (
    None
):
    try:
        assumptions = SimulationAssumptions.model_validate_json(
            json.dumps(
                {
                    "meta": {"start_year_month": "2026-01", "horizon_months": 12},
                    "sales": {"festival_date_overrides": ["2025-12-01"]},
                }
            )
        )
    except ValueError as exc:
        pytest.fail(f"A dated override before this valid forecast window is ignored: {exc}")
    assert assumptions.sales.festival_sale_months == []
