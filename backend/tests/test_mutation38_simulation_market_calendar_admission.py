"""Market calendar admission agrees with the independent frontend calendar controls.

The published editor supports twelve January-indexed positive multipliers,
calendar/hold controls from zero through twelve, forty authored festival
entries and a 500-character provenance field. Explicit local dates take
precedence and retain their authored evidence while their run months are pruned.
"""

import json
from datetime import date, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import (
    SalesAssumptions,
    SimulationAssumptions,
    _normalized_seasonality,
)
from app.simulation.market import calendar_multiplier, meat_price_for_month


def _sales(payload: dict[str, Any]) -> SalesAssumptions:
    try:
        return SalesAssumptions.model_validate_json(json.dumps(payload))
    except (ArithmeticError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"Published valid market/calendar JSON must remain admissible: {error}")


def _scenario(payload: dict[str, Any]) -> SimulationAssumptions:
    try:
        return SimulationAssumptions.model_validate_json(json.dumps(payload))
    except (ArithmeticError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"Authored valid calendar must resolve into a valid run: {error}")


@pytest.mark.parametrize("curve", [[1.0] * 11, [1.0] * 13], ids=["missing-december", "extra-month"])
def test_authored_price_curve_requires_a_complete_twelve_month_calendar(curve: list[float]) -> None:
    with pytest.raises(ValidationError) as error:
        SalesAssumptions.model_validate_json(json.dumps({"monthly_meat_price_multipliers": curve}))
    assert error.value.errors()[0]["loc"] == ("monthly_meat_price_multipliers",)


@pytest.mark.parametrize("outside", [0.0, -1.0, 10.01], ids=["zero", "negative", "over-stress-cap"])
def test_authored_price_curve_rejects_nonpositive_and_excessive_months(outside: float) -> None:
    curve = [1.0] * 12
    curve[0] = outside
    if outside > 10.0:
        # Keep the authored annual mean at one so the cap applies to a genuine
        # excessive monthly stress, rather than a neutral whole-year rescale.
        curve = [outside, *[(12.0 - outside) / 11.0] * 11]
    with pytest.raises(ValidationError):
        SalesAssumptions.model_validate_json(json.dumps({"monthly_meat_price_multipliers": curve}))


def test_maximum_monthly_stress_curve_is_admitted_and_normalizes_annual_mean() -> None:
    curve = [10.0, *[2.0 / 11.0] * 11]
    sales = _sales({"monthly_meat_price_multipliers": curve})
    assert sales.monthly_meat_price_multipliers[0] == 10.0
    assert sum(sales.monthly_meat_price_multipliers) / 12.0 == pytest.approx(1.0)


def test_native_neutral_curve_prices_all_twelve_calendar_months() -> None:
    try:
        curve = _normalized_seasonality([0.0] * 12)
        prices = [calendar_multiplier(curve, month) * 200.0 for month in range(1, 13)]
    except (ArithmeticError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"The native neutral fallback must price a complete calendar: {error}")
    assert len(curve) == 12
    assert prices == [200.0] * 12


@pytest.mark.parametrize("field", ["eid_month", "festival_hold_months"])
@pytest.mark.parametrize("boundary", [0, 12], ids=["disabled", "full-year"])
def test_calendar_and_hold_controls_admit_both_published_boundaries(
    field: str, boundary: int
) -> None:
    sales = _sales({field: boundary})
    assert getattr(sales, field) == boundary


@pytest.mark.parametrize("field", ["eid_month", "festival_hold_months"])
@pytest.mark.parametrize("outside", [-1, 13], ids=["negative", "beyond-calendar-year"])
def test_calendar_and_hold_controls_reject_values_outside_editor_range(
    field: str, outside: int
) -> None:
    with pytest.raises(ValidationError) as error:
        SalesAssumptions.model_validate_json(json.dumps({field: outside}))
    assert error.value.errors()[0]["loc"] == (field,)


def test_omitted_legacy_month_does_not_add_a_recurring_january_premium() -> None:
    sales = _sales(
        {
            "meat_price_per_kg": 200.0,
            "monthly_meat_price_multipliers": [1.0] * 12,
            "annual_livestock_price_growth_rate": 0.0,
        }
    )
    assert [
        meat_price_for_month(sales, simulation_month=month, calendar_month=month)
        for month in range(1, 13)
    ] == [200.0] * 12


def test_forty_authored_festival_months_survive_without_calendar_replacement() -> None:
    authored = list(range(1, 41))
    scenario = _scenario(
        {"meta": {"horizon_months": 60}, "sales": {"festival_sale_months": authored}}
    )
    assert scenario.sales.festival_sale_months == authored


def test_forty_one_festival_months_are_rejected_at_the_authored_document_boundary() -> None:
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate_json(
            json.dumps(
                {
                    "meta": {"horizon_months": 60},
                    "sales": {"festival_sale_months": list(range(1, 42))},
                }
            )
        )


@pytest.mark.parametrize(
    "count,admitted", [(40, True), (41, False)], ids=["full-document", "oversized-document"]
)
def test_authored_local_festival_dates_preserve_the_document_budget(
    count: int, admitted: bool
) -> None:
    authored = [(date(2029, 6, 1) + timedelta(days=index)).isoformat() for index in range(count)]
    payload = {
        "meta": {"horizon_months": 12, "start_year_month": "2029-06"},
        "sales": {"festival_date_overrides": authored},
    }
    if not admitted:
        with pytest.raises(ValidationError):
            SimulationAssumptions.model_validate_json(json.dumps(payload))
        return
    scenario = _scenario(payload)
    assert scenario.sales.festival_date_overrides == authored
    assert scenario.sales.festival_sale_months == [1, 2]


@pytest.mark.parametrize(
    "length,admitted", [(500, True), (501, False)], ids=["editor-maximum", "over-editor-maximum"]
)
def test_authored_date_provenance_preserves_the_full_editor_document(
    length: int, admitted: bool
) -> None:
    provenance = "https://authority.example/local-calendar?".ljust(length, "x")
    payload = {"festival_date_source": provenance, "festival_date_overrides": ["2029-06-01"]}
    if not admitted:
        with pytest.raises(ValidationError):
            SalesAssumptions.model_validate_json(json.dumps(payload))
        return
    sales = _sales(payload)
    assert sales.festival_date_source == provenance


@pytest.mark.parametrize(
    "months", [[0], [1, 1]], ids=["zero-is-not-a-run-month", "duplicate-month"]
)
def test_explicit_festival_months_reject_invalid_run_positions(months: list[int]) -> None:
    with pytest.raises(ValidationError):
        SimulationAssumptions.model_validate_json(
            json.dumps({"sales": {"festival_sale_months": months}})
        )


def test_explicit_festival_months_include_first_and_last_run_month_and_prune_later() -> None:
    scenario = _scenario(
        {"meta": {"horizon_months": 12}, "sales": {"festival_sale_months": [1, 12, 13]}}
    )
    assert scenario.sales.festival_sale_months == [1, 12]


def test_local_date_override_translates_year_boundaries_and_prices_only_covered_months() -> None:
    dates = ["2027-07-31", "2027-08-31", "2028-07-01", "2028-08-01", "2029-07-31", "2029-08-01"]
    scenario = _scenario(
        {
            "meta": {"horizon_months": 24, "start_year_month": "2027-08"},
            "sales": {
                "festival_date_overrides": list(reversed(dates)),
                "festival_date_source": "Owner-confirmed local dates",
                "festival_sale_months": [2],
                "eid_month": 10,
                "meat_price_per_kg": 100.0,
                "eid_price_uplift": 1.0,
                "annual_livestock_price_growth_rate": 0.0,
                "monthly_meat_price_multipliers": [1.0] * 12,
            },
        }
    )
    assert scenario.sales.festival_date_overrides == dates
    assert scenario.sales.festival_date_source == "Owner-confirmed local dates"
    assert scenario.sales.festival_sale_months == [1, 12, 13, 24]
    for month in range(1, 25):
        price = meat_price_for_month(
            scenario.sales, simulation_month=month, calendar_month=(month + 6) % 12 + 1
        )
        assert price == (200.0 if month in {1, 12, 13, 24} else 100.0)


def test_explicit_empty_local_dates_disable_both_embedded_and_legacy_festivals() -> None:
    scenario = _scenario(
        {"sales": {"festival_date_overrides": [], "festival_sale_months": [1], "eid_month": 1}}
    )
    assert scenario.sales.festival_date_overrides == []
    assert scenario.sales.festival_sale_months == []


def test_omitted_local_dates_keep_the_automatic_calendar_live() -> None:
    scenario = _scenario({"meta": {"horizon_months": 12, "start_year_month": "2027-01"}})
    assert scenario.sales.festival_date_overrides is None
    assert scenario.sales.festival_sale_months == [5]
