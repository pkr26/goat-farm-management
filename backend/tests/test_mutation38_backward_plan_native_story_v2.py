"""A real dated plan keeps its feasible stock, instructions and optional risk aligned."""

import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.backward_planner import PlannerTarget, build_backward_plan


def _assumptions(retention: float = 0.5, does: int = 0) -> SimulationAssumptions:
    return SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"start_year_month": "2026-01", "horizon_months": 12},
                "herd": {
                    "does": does,
                    "bucks": 0,
                    "auto_purchase_bucks": True,
                    "foundation_flock_state": "open",
                    "purchased_doe_settling_months": 0,
                    "female_retention_fraction": retention,
                },
                "reproduction": {
                    "conception_rate": 0.5,
                    "gestation_months": 5,
                    "lactation_months": 2,
                    "months_open_before_breeding": 1,
                    "litter_size": 2.0,
                    "stillbirth_rate": 0.0,
                    "sex_ratio_female": 0.75,
                    "max_services_before_cull": 1,
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
            }
        )
    )


def _completed[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A supported native dated plan must complete coherently: {error}")


def test_native_calendar_target_rejects_a_coerced_head_count() -> None:
    with pytest.raises(ValidationError):
        PlannerTarget.model_validate_json(
            '{"year_month":"2027-12","animal_class":"female_kid","count":"10"}'
        )


def test_native_empty_target_list_has_its_controlled_disposition() -> None:
    with pytest.raises(ValueError, match="at least one sale target"):
        build_backward_plan(_assumptions(), [])


def test_dated_twenty_year_endpoint_is_supported_and_next_month_is_rejected() -> None:
    assumptions = _assumptions()
    report = _completed(
        lambda: build_backward_plan(
            assumptions,
            [PlannerTarget(year_month="2045-12", animal_class="doe", count=1.0)],
            close_gaps_enabled=False,
        )
    )
    assert report.horizon_months == len(report.stage_plan) == 240
    assert report.stage_plan[-1].year_month == "2045-12"
    assert assumptions.meta.horizon_months == 12
    with pytest.raises(ValueError, match="beyond the simulation's 20-year horizon"):
        build_backward_plan(
            assumptions,
            [PlannerTarget(year_month="2046-01", animal_class="doe", count=1.0)],
            close_gaps_enabled=False,
        )


@pytest.mark.parametrize("retention", [0.5, 1.0])
def test_actual_purchase_plan_reconciles_stage_stock_and_dated_biological_advice(
    retention: float,
) -> None:
    assumptions = _assumptions(retention)
    target = PlannerTarget(year_month="2027-12", animal_class="female_kid", count=10.0)
    report = _completed(lambda: build_backward_plan(assumptions, [target]))
    assert report.plan.probabilities is None
    assert report.plan.recommended_purchases
    assert sum(row.purchases_head for row in report.stage_plan) == pytest.approx(
        sum(event.count for event in report.plan.recommended_purchases)
        + report.stage_plan[-1].bucks
    )
    evaluation = report.plan.after if report.plan.after is not None else report.plan.before
    filled = evaluation.targets[0]
    assert report.stage_plan[-1].sales_head == pytest.approx(filled.filled)
    chain = report.chains[0]
    # Ten females require ceil(10 / .75)=14 births, seven twin-bearing
    # does, and fourteen one-shot services at fifty-percent conception.
    assert [step.quantity for step in chain.steps] == [14, 7, 14, 10]
    breed = next(action for action in report.actions if action.kind == "breed")
    births = next(action for action in report.actions if action.kind == "expect_births")
    sale = next(action for action in report.actions if action.kind == "sell")
    assert breed.headline.startswith("Breed ~14 does")
    assert births.headline.startswith("Expect ~14 kids born")
    assert sale.year_month == "2027-12" and sale.month == 24
    assert f"Planned revenue ≈ ₹{filled.revenue:,.0f}." in sale.detail
    assert ("The closed plan fills this target." in sale.detail) is filled.met
    retain = [action for action in report.actions if action.kind == "retain"]
    assert len(retain) == (1 if retention < 1.0 else 0)
    if retain:
        assert retain[0].month == 1 and retain[0].year_month == "2026-01"
    assert report.notes[0].startswith("Plan runs 2026-01 → 2027-12")
    assert report.notes[1].startswith("Recommendation in one line:")
    assert assumptions.events == [] and assumptions.meta.horizon_months == 12


@pytest.mark.parametrize(
    "year_month,bred_month,missed", [("2026-07", 0, True), ("2026-08", 1, False)]
)
def test_actual_first_month_breeding_instruction_is_not_a_missed_deadline(
    year_month: str, bred_month: int, missed: bool
) -> None:
    report = _completed(
        lambda: build_backward_plan(
            _assumptions(),
            [PlannerTarget(year_month=year_month, animal_class="male_kid", count=1.0)],
            close_gaps_enabled=False,
        )
    )
    action = next(action for action in report.actions if action.kind == "breed")
    assert action.month == bred_month
    assert ("already behind you" in action.detail) is missed
    assert ("Serve every open, settled doe this month" in action.detail) is (not missed)


def test_an_already_filled_adult_sale_needs_no_purchase_or_retention_instruction() -> None:
    report = _completed(
        lambda: build_backward_plan(
            _assumptions(does=10),
            [PlannerTarget(year_month="2026-08", animal_class="doe", count=1.0)],
        )
    )
    assert report.plan.before.all_met and report.plan.recommended_purchases == []
    assert report.plan.probabilities is None
    assert [action.kind for action in report.actions] == ["sell"]
    assert report.chains[0].achievable
