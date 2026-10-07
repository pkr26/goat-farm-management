"""Real scheduled sales and bought-doe biology conserve forward planning cohorts."""

import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import MortalityAssumptions, SimulationAssumptions
from app.simulation.planner import (
    SaleTarget,
    _marginal_kids_per_doe,
    _purchase_month_for,
    _survival_to_event_age,
    close_gaps,
    evaluate_plan,
)


def _assumptions(events: list[dict[str, object]] | None = None) -> SimulationAssumptions:
    document = {
        "meta": {"start_year_month": "2026-01", "horizon_months": 36},
        "herd": {
            "does": 0,
            "bucks": 0,
            "auto_purchase_bucks": True,
            "foundation_flock_state": "open",
            "purchased_doe_settling_months": 0,
            "female_retention_fraction": 1.0,
        },
        "reproduction": {
            "conception_rate": 1.0,
            "gestation_months": 5,
            "lactation_months": 2,
            "months_open_before_breeding": 1,
            "litter_size": 2.0,
            "stillbirth_rate": 0.0,
            "sex_ratio_female": 0.5,
            "parity_multipliers": {"litter_size": [1.0], "conception_rate": [1.0]},
        },
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
        "sales": {"festival_sale_months": [], "annual_livestock_price_growth_rate": 0.0},
        "events": events or [],
    }
    return SimulationAssumptions.model_validate_json(json.dumps(document))


def _completed[T](label: str, work: Callable[[], T]) -> T:
    try:
        return work()
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A valid {label} must complete with conserved target cohorts: {exc}")


@pytest.mark.parametrize(
    "age,kid,weaner,grower",
    [
        (0, 1, 0, 0),
        (1, 2, 0, 0),
        (2, 3, 0, 0),
        (3, 3, 1, 0),
        (5, 3, 3, 0),
        (6, 3, 3, 1),
        (11, 3, 3, 6),
    ],
)
def test_sale_event_survival_accounts_for_each_completed_class_exposure(
    age: int, kid: int, weaner: int, grower: int
) -> None:
    mortality = MortalityAssumptions(kid_pre_weaning=0.2, kid_post_weaning=0.4, grower=0.64)
    survived = _completed("sale-age survival", lambda: _survival_to_event_age(mortality, age))
    # The event precedes the current month's aging: birth-month hazard has
    # already occurred, followed by the hand-tabulated lived class months.
    expected = 0.8 ** (kid / 3) * 0.6 ** (weaner / 3) * 0.36 ** (grower / 12)
    assert 0.0 < survived <= 1.0
    assert survived == pytest.approx(expected)


@pytest.mark.parametrize(
    "month,animal_class,expected",
    [
        (6, "male_kid", 0.0),
        (7, "male_kid", 1.0),
        (10, "male_weaner", 1.0),
        (13, "male_grower", 1.0),
        (24, "male_kid", 2.0),
    ],
)
def test_purchased_doe_yield_counts_only_live_in_window_offspring_and_retained_generation(
    month: int, animal_class: str, expected: float
) -> None:
    assumptions = _assumptions()
    target = SaleTarget.model_validate_json(
        json.dumps({"month": month, "animal_class": animal_class, "count": 1.0})
    )
    supplied = _completed(
        "bought-doe offspring calendar", lambda: _marginal_kids_per_doe(assumptions, target, 1)
    )
    # Bought in month1 and settled immediately: twins arrive6/14/22; one
    # daughter from the first litter breeds at18 and kids at23. A month24
    # kid sale therefore sees one male from month22 and one from month23.
    assert supplied == pytest.approx(expected)


@pytest.mark.parametrize(
    "animal_class,expected",
    [("male_kid", 16), ("male_weaner", 13), ("male_grower", 10), ("female_grower", 7)],
)
def test_purchase_deadline_preserves_class_age_gestation_and_one_month_slack(
    animal_class: str, expected: int
) -> None:
    assumptions = _assumptions()
    target = SaleTarget.model_validate_json(
        json.dumps({"month": 24, "animal_class": animal_class, "count": 1.0})
    )
    assert (
        _completed("purchase deadline", lambda: _purchase_month_for(target, assumptions))
        == expected
    )


def test_real_out_of_order_same_month_sales_preserve_target_order_and_partial_fill_money() -> None:
    assumptions = _assumptions(
        [
            {"month": 3, "kind": "purchase", "animal_class": "male_kid", "count": 1.5},
            {"month": 3, "kind": "purchase", "animal_class": "female_kid", "count": 2.5},
        ]
    )
    targets = [
        SaleTarget(month=4, animal_class="male_kid", count=1.0),
        SaleTarget(month=3, animal_class="female_kid", count=1.5),
        SaleTarget(month=4, animal_class="male_kid", count=1.0),
        SaleTarget(month=3, animal_class="female_kid", count=1.0),
    ]
    report = _completed(
        "actual scheduled sale evaluation", lambda: evaluate_plan(assumptions, targets)
    )
    assert [(row.month, row.animal_class, row.requested) for row in report.targets] == [
        (row.month, row.animal_class, row.count) for row in targets
    ]
    assert [row.filled for row in report.targets] == pytest.approx([1.0, 1.5, 0.5, 1.0])
    assert report.total_shortfall == pytest.approx(0.5)
    for row in report.targets:
        assert row.revenue == pytest.approx(row.filled * row.price_per_head)
        assert row.shortfall == pytest.approx(row.requested - row.filled)


def test_gap_closing_buys_the_two_does_whose_actual_kids_fill_the_sale() -> None:
    assumptions = _assumptions()
    targets = [SaleTarget(month=24, animal_class="male_kid", count=2.0)]
    purchases, after, closed, notes = _completed(
        "real gap closing", lambda: close_gaps(assumptions, targets)
    )
    assert closed, notes
    assert [(event.month, event.animal_class, event.count) for event in purchases] == [
        (16, "doe", 2.0)
    ]
    assert after.targets[0].filled == pytest.approx(2.0)
    assert after.total_shortfall == pytest.approx(0.0)
    assert assumptions.events == []


def test_adult_cull_sale_is_not_backed_by_buying_another_breeding_animal() -> None:
    assumptions = _assumptions()
    targets = [SaleTarget(month=12, animal_class="doe", count=1.0)]
    purchases, after, closed, notes = _completed(
        "unbackable adult sale", lambda: close_gaps(assumptions, targets)
    )
    assert purchases == [] and not closed
    assert after.targets[0].filled == 0.0
    assert after.total_shortfall == 1.0
    assert any("never buys breeding stock to fuel cull sales" in note for note in notes)


@pytest.mark.parametrize(
    "model,document",
    [
        (SaleTarget, {"month": True, "animal_class": "male_kid", "count": 1.0}),
        (SaleTarget, {"month": 1, "animal_class": "male_kid", "count": "1"}),
    ],
)
def test_native_sale_target_requires_real_typed_month_and_head_count(
    model: type[SaleTarget], document: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(document))
