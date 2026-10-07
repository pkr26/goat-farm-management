"""Actual backward instructions retain birth windows, survivors and purchase lead times."""

from collections.abc import Callable

import pytest

from app.simulation.assumptions import HerdEventAssumptions, SimulationAssumptions
from app.simulation.backward_planner import (
    PlannerTarget,
    _ClassWindow,
    _effective_conception,
    _purchase_actions,
    _requirement_chain,
)
from app.simulation.vocabulary import GOAT_NOUNS


def _completed[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A valid backward biological instruction must complete: {error}")


def _assumptions() -> SimulationAssumptions:
    return SimulationAssumptions.model_validate_json(
        '{"meta":{"start_year_month":"2026-01","horizon_months":24},"growth":{"sale_age_months":10},"herd":{"does":0,"bucks":0,"auto_purchase_bucks":false,"purchased_doe_settling_months":2},"reproduction":{"conception_rate":0.5,"litter_size":2,"stillbirth_rate":0,"sex_ratio_female":0.75,"max_services_before_cull":1},"mortality":{"kid_pre_weaning":0.875,"kid_post_weaning":0,"grower":0,"adult":0}}'
    )


@pytest.mark.parametrize(
    "animal_class,entry,last,typical,birth",
    [
        ("female_kid", 0, 2, 1, 22),
        ("male_kid", 0, 2, 1, 22),
        ("female_weaner", 3, 5, 4, 19),
        ("male_weaner", 3, 5, 4, 19),
        ("female_grower", 6, 11, 8, 15),
        ("male_grower", 6, 9, 7, 16),
    ],
)
def test_real_class_window_dates_the_birth_that_reaches_mid_class_at_sale(
    animal_class: str, entry: int, last: int, typical: int, birth: int
) -> None:
    window = _completed(lambda: _ClassWindow(_assumptions(), animal_class))
    assert (window.entry_age, window.max_age, window.typical_age) == (entry, last, typical)
    assert window.birth_month(24) == birth
    assert window.bred_month(24, 5) == birth - 5


def test_real_requirement_chain_accounts_for_survival_litter_and_single_service() -> None:
    assumptions = _assumptions()
    target = PlannerTarget(year_month="2027-12", animal_class="female_kid", count=25.0)
    window = _completed(lambda: _ClassWindow(assumptions, target.animal_class))
    chain = _completed(
        lambda: _requirement_chain(assumptions, target, 24, False, window, GOAT_NOUNS)
    )
    # Two of the three monthly preweaning exposures leave one quarter of
    # births.100 births of the desired sex are needed for25 surviving sales;
    # at75% female that is134 total births,67 twin-bearing does and134 services.
    assert [step.quantity for step in chain.steps] == [134, 67, 134, 25]
    assert [step.year_month for step in chain.steps] == ["2027-05", "2027-10", "2027-10", "2027-12"]
    assert "75% born-to-sale mortality" in chain.explanation
    assert "75% of the sex you sell" in chain.explanation
    assert "one service" in chain.steps[0].label
    assert "later births" not in chain.steps[0].label
    assert chain.achievable is False


@pytest.mark.parametrize(
    "zero", ["sex", "conception"], ids=["no-target-sex", "no-successful-service"]
)
def test_impossible_requirement_chain_is_a_controlled_value_error(zero: str) -> None:
    assumptions = _assumptions()
    if zero == "sex":
        assumptions.reproduction.sex_ratio_female = 0.0
    else:
        assumptions.reproduction.conception_rate = 0.0
    assumptions = SimulationAssumptions.model_validate(assumptions.model_dump())
    target = PlannerTarget(year_month="2027-12", animal_class="female_kid", count=25.0)
    window = _completed(lambda: _ClassWindow(assumptions, target.animal_class))
    try:
        _requirement_chain(assumptions, target, 24, False, window, GOAT_NOUNS)
    except ValueError as error:
        assert ("sex_ratio_female" if zero == "sex" else "conception_rate") in str(error)
    except (ArithmeticError, AttributeError, LookupError, TypeError) as error:
        pytest.fail(
            f"An impossible dated biological chain must have a controlled rejection: {error}"
        )
    else:
        pytest.fail("Certain conception failure or absent target sex must be rejected")


def test_zero_conception_native_disposition_does_not_promise_successful_births() -> None:
    assumptions = _assumptions()
    assumptions.reproduction.conception_rate = 0.0
    probability, explanation = _completed(lambda: _effective_conception(assumptions))
    assert probability == 0.0
    assert "no successful conception" in explanation


@pytest.mark.parametrize(
    "target_classes,growth,lead",
    [(["female_weaner", "male_grower"], 5, 13), ([], 9, 17)],
    ids=["tightest-of-two-classes", "native-unselected-class-fallback"],
)
def test_real_purchase_actions_group_actual_head_and_disclose_the_tightest_lead(
    target_classes: list[str], growth: int, lead: int
) -> None:
    assumptions = _assumptions()
    purchases = [
        HerdEventAssumptions(month=1, kind="purchase", animal_class="doe", count=1.5),
        HerdEventAssumptions(month=1, kind="purchase", animal_class="doe", count=2.0),
        HerdEventAssumptions(month=3, kind="purchase", animal_class="doe", count=1.0),
    ]
    actions = _completed(
        lambda: _purchase_actions(assumptions, purchases, "2026-01", target_classes, GOAT_NOUNS)
    )
    assert [(action.month, action.year_month, action.kind) for action in actions] == [
        (1, "2026-01", "purchase"),
        (3, "2026-03", "purchase"),
    ]
    assert actions[0].headline == "Buy ~4 does"
    assert actions[1].headline == "Buy ~1 does"
    assert all("2 month(s) to settle" in action.detail for action in actions)
    assert all(f"~{growth} months to grow" in action.detail for action in actions)
    assert all(f"~{lead}-month chain" in action.detail for action in actions)
