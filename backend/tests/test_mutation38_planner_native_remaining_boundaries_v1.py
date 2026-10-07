"""Native sale-plan admission, guidance and independent target processing."""

import json
from collections.abc import Callable

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.planner import (
    SaleTarget,
    _check_purchase_event_budget,
    _purchases_from,
    build_plan_report,
    close_gaps,
    plan_probabilities,
)


def _completed[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A supported native sale plan must complete coherently: {error}")


def _assumptions(*, stock: float = 0.0) -> SimulationAssumptions:
    events = (
        [{"month": 1, "kind": "purchase", "animal_class": "male_kid", "count": stock}]
        if stock
        else []
    )
    return SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"start_year_month": "2051-01", "horizon_months": 24},
                "herd": {
                    "does": 0,
                    "bucks": 0,
                    "auto_purchase_bucks": True,
                    "purchased_doe_settling_months": 0,
                },
                "reproduction": {
                    "conception_rate": 1.0,
                    "litter_size": 2.0,
                    "stillbirth_rate": 0.0,
                    "sex_ratio_female": 0.5,
                    "parity_multipliers": {"litter_size": [1.0], "conception_rate": [1.0]},
                },
                "mortality": {
                    "kid_pre_weaning": 0.0,
                    "kid_post_weaning": 0.0,
                    "grower": 0.0,
                    "adult": 0.0,
                },
                "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
                "sales": {"festival_sale_months": []},
                "events": events,
            }
        )
    )


def test_one_of_four_heads_does_not_meet_eighty_percent_sale_target() -> None:
    risk = _completed(
        lambda: plan_probabilities(
            _assumptions(stock=1.0),
            [SaleTarget(month=2, animal_class="male_kid", count=4.0)],
            runs=2,
            seed=7,
        )
    )[0]
    assert risk.p_full == risk.p_eighty == 0.0


def test_report_omits_unrequested_risk_simulation() -> None:
    report = _completed(
        lambda: build_plan_report(
            _assumptions(stock=1.0), [SaleTarget(month=2, animal_class="male_kid", count=1.0)]
        )
    )
    assert report.gaps_closed and report.before.all_met
    assert report.probabilities is None


def test_successfully_closed_plan_does_not_report_a_remaining_shortfall() -> None:
    report = _completed(
        lambda: build_plan_report(
            _assumptions(), [SaleTarget(month=24, animal_class="male_kid", count=1.0)]
        )
    )
    assert not report.before.all_met
    assert report.after is not None and report.after.all_met and report.gaps_closed
    assert sum(event.count for event in report.recommended_purchases) == 1.0
    assert all("Gap closing stopped with a shortfall" not in note for note in report.notes)


def test_retained_purchases_leave_one_real_event_slot_for_gap_closing() -> None:
    document = _assumptions().model_dump(mode="json")
    document["events"] = [
        {"month": 24, "kind": "purchase", "animal_class": "doe", "count": 1.0} for _ in range(498)
    ]
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(document))
    purchases, evaluation, closed, _ = _completed(
        lambda: close_gaps(assumptions, [SaleTarget(month=24, animal_class="male_kid", count=1.0)])
    )
    assert closed and evaluation.all_met
    assert len(purchases) == 1 and purchases[0].count == 1.0
    assert len(assumptions.events) == 498


def test_standalone_purchase_document_has_no_phantom_reserved_event() -> None:
    # This checks the event-document contract, not admission of a forecast
    # with fifty million live animals. Whole-forecast head limits still apply.
    counts = {1: 50_000_000.0}
    _completed(lambda: _check_purchase_event_budget(counts))
    purchases = _completed(lambda: _purchases_from(counts))
    document = _assumptions().model_dump(mode="json")
    document["events"] = [event.model_dump(mode="json") for event in purchases]
    admitted = _completed(lambda: SimulationAssumptions.model_validate_json(json.dumps(document)))
    assert len(admitted.events) == 500
    assert sum(event.count for event in admitted.events) == 50_000_000.0
    with pytest.raises(ValueError, match="already reserved"):
        _check_purchase_event_budget(counts, reserved=1)


@pytest.mark.parametrize(
    "animal_class,earliest",
    [("male_kid", 7), ("male_weaner", 10), ("male_grower", 13)],
)
def test_impossible_early_sales_report_class_specific_supply_calendar(
    animal_class: str, earliest: int
) -> None:
    target = SaleTarget.model_validate_json(
        json.dumps({"month": 2, "animal_class": animal_class, "count": 1.0})
    )
    purchases, evaluation, closed, notes = _completed(lambda: close_gaps(_assumptions(), [target]))
    assert not purchases and not closed and not evaluation.all_met
    assert any(f"in the sale pool is month {earliest}." in note for note in notes)


@pytest.mark.parametrize("first_class,first_month,stock", [("male_kid", 2, 1.0), ("doe", 2, 0.0)])
def test_a_filled_or_policy_excluded_target_does_not_block_the_next_target(
    first_class: str, first_month: int, stock: float
) -> None:
    targets = [
        SaleTarget.model_validate_json(
            json.dumps({"month": first_month, "animal_class": first_class, "count": 1.0})
        ),
        SaleTarget(month=24, animal_class="male_kid", count=1.0),
    ]
    purchases, evaluation, _, _ = _completed(lambda: close_gaps(_assumptions(stock=stock), targets))
    assert purchases and evaluation.targets[1].met
    assert evaluation.targets[1].filled == 1.0


def test_an_impossible_early_target_does_not_block_a_later_feasible_sale() -> None:
    purchases, evaluation, closed, notes = _completed(
        lambda: close_gaps(
            _assumptions(),
            [
                SaleTarget(month=2, animal_class="male_kid", count=1.0),
                SaleTarget(month=24, animal_class="male_kid", count=1.0),
            ],
        )
    )
    assert purchases and evaluation.targets[1].met
    assert not closed and not evaluation.targets[0].met
    assert any("impossible to supply" in note for note in notes)
