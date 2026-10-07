"""Native sale risk, head conservation, completion and the current public CPU tariff."""

import json
from collections.abc import Callable, Iterator

import httpx
import pytest
from pydantic import ValidationError

from app.api import _run_limits
from app.simulation.assumptions import SimulationAssumptions
from app.simulation.planner import (
    SaleTarget,
    _check_purchase_event_budget,
    _marginal_kids_per_doe,
    _purchase_month_for,
    _purchases_from,
    build_plan_report,
    close_gaps,
    evaluate_plan,
    plan_probabilities,
)

from .conftest import owner_with_farm


@pytest.fixture(autouse=True)
def native_budget_isolation() -> Iterator[None]:
    _run_limits._run_budget.clear()
    _run_limits._farm_run_locks.clear()
    yield
    _run_limits._run_budget.clear()
    _run_limits._farm_run_locks.clear()


def _completed[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A supported native sale plan must complete coherently: {error}")


def _assumptions(*, stock: float = 0.0, sire: bool = False) -> SimulationAssumptions:
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
                    "auto_purchase_bucks": sire,
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


def test_native_first_month_target_is_admitted_and_month_zero_is_rejected() -> None:
    target = _completed(
        lambda: SaleTarget.model_validate_json('{"month":1,"animal_class":"male_kid","count":1.0}')
    )
    assert target.month == 1
    with pytest.raises(ValidationError):
        SaleTarget.model_validate_json('{"month":0,"animal_class":"male_kid","count":1.0}')


@pytest.mark.parametrize(
    "stock,requested_heads,full,eighty",
    [(0.5, 1.0, 1.0, 1.0), (1.5, 2.5, 0.0, 1.0), (4.0, 4.0, 1.0, 1.0)],
)
def test_actual_partial_sale_risk_respects_the_documented_half_head_tolerance(
    stock: float, requested_heads: float, full: float, eighty: float
) -> None:
    assumptions = _assumptions(stock=stock)
    targets = [SaleTarget(month=2, animal_class="male_kid", count=requested_heads)]
    risk = _completed(lambda: plan_probabilities(assumptions, targets, runs=2, seed=7))[0]
    assert risk.p_full == full and risk.p_eighty == eighty
    fill = _completed(lambda: evaluate_plan(assumptions, targets)).targets[0]
    assert fill.filled == min(stock, requested_heads)
    assert fill.met is (full == 1.0)


def test_unavailable_stock_retains_its_quote_without_booking_income() -> None:
    row = _completed(
        lambda: evaluate_plan(
            _assumptions(), [SaleTarget(month=2, animal_class="male_kid", count=1.0)]
        )
    ).targets[0]
    assert row.filled == row.revenue == 0.0
    assert row.price_per_head > 0.0
    assert row.shortfall == 1.0 and not row.met


def test_native_zero_risk_replays_have_finite_zero_frequencies() -> None:
    rows = _completed(
        lambda: plan_probabilities(
            _assumptions(),
            [SaleTarget(month=2, animal_class="male_kid", count=1.0)],
            runs=0,
            seed=7,
        )
    )
    assert len(rows) == 1 and rows[0].p_full == rows[0].p_eighty == 0.0


@pytest.mark.parametrize("seed,full,eighty", [(7, 0.915, 0.98), (42, 0.93, 0.995)])
def test_current_release_native_seeded_risk_replays_preserve_public_frequencies(
    seed: int, full: float, eighty: float
) -> None:
    # Independently captured native public outputs are bound in the root
    # reference sidecar. These are current-release replay regressions;
    # intentional algorithm changes require review of the reference.
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"start_year_month": "2051-01", "horizon_months": 12},
                "herd": {"does": 0, "bucks": 0, "auto_purchase_bucks": False},
                "mortality": {
                    "kid_pre_weaning": 0.5,
                    "kid_post_weaning": 0.0,
                    "grower": 0.0,
                    "adult": 0.0,
                },
                "sales": {"festival_sale_months": []},
                "events": [
                    {"month": 1, "kind": "purchase", "animal_class": "male_kid", "count": 1.0}
                ],
                "risk": {"seed": 17, "kid_mortality": {"enabled": True, "low": 0.5, "high": 1.999}},
            }
        )
    )
    rows = _completed(
        lambda: plan_probabilities(
            assumptions, [SaleTarget(month=2, animal_class="male_kid", count=1.0)], seed=seed
        )
    )
    assert len(rows) == 1
    assert rows[0].p_full == full and rows[0].p_eighty == eighty


def test_one_real_purchased_doe_can_back_the_one_head_shortfall() -> None:
    assumptions = _assumptions(sire=True)
    targets = [SaleTarget(month=24, animal_class="male_kid", count=1.0)]
    purchases, after, closed, _ = _completed(lambda: close_gaps(assumptions, targets))
    assert closed and after.targets[0].met
    assert sum(event.count for event in purchases) == 1.0
    report = _completed(lambda: build_plan_report(assumptions, targets, risk_runs=1))
    assert report.probabilities is not None and report.probabilities[0].p_full == 1.0


def test_no_conception_retains_the_real_shortfall_without_dividing_by_zero() -> None:
    assumptions = _assumptions(sire=True)
    assumptions.reproduction.conception_rate = 0.0
    assumptions = SimulationAssumptions.model_validate(assumptions.model_dump())
    purchases, after, closed, _ = _completed(
        lambda: close_gaps(assumptions, [SaleTarget(month=24, animal_class="male_kid", count=1.0)])
    )
    assert purchases == [] and not closed and after.targets[0].filled == 0.0


def test_early_target_purchase_deadline_never_precedes_the_plan() -> None:
    month = _completed(
        lambda: _purchase_month_for(
            SaleTarget(month=2, animal_class="male_kid", count=1.0), _assumptions()
        )
    )
    assert month == 1


def test_real_unbackable_adult_sale_has_only_its_policy_explanation() -> None:
    purchases, after, closed, notes = _completed(
        lambda: close_gaps(_assumptions(), [SaleTarget(month=24, animal_class="doe", count=1.0)])
    )
    assert purchases == [] and not closed and after.targets[0].filled == 0.0
    assert any("never buys breeding stock" in note for note in notes)
    assert not any("full round of purchased does" in note for note in notes)


def test_already_filled_target_does_not_hide_a_later_adult_policy_shortfall() -> None:
    targets = [
        SaleTarget(month=2, animal_class="male_kid", count=1.0),
        SaleTarget(month=24, animal_class="doe", count=1.0),
    ]
    _, after, closed, notes = _completed(lambda: close_gaps(_assumptions(stock=1.0), targets))
    assert after.targets[0].met and not after.targets[1].met and not closed
    assert any("never buys breeding stock" in note for note in notes)


def test_impossible_early_sale_does_not_prevent_backing_a_later_feasible_sale() -> None:
    targets = [
        SaleTarget(month=2, animal_class="male_kid", count=1.0),
        SaleTarget(month=24, animal_class="male_kid", count=1.0),
    ]
    purchases, after, closed, notes = _completed(
        lambda: close_gaps(_assumptions(sire=True), targets)
    )
    assert purchases and not closed and not after.targets[0].met and after.targets[1].met
    assert any("earliest month" in note for note in notes)


def test_no_actual_sire_rolls_back_one_failed_round_and_reports_it_once() -> None:
    purchases, after, closed, notes = _completed(
        lambda: close_gaps(
            _assumptions(), [SaleTarget(month=24, animal_class="male_kid", count=1.0)]
        )
    )
    assert purchases == [] and not closed and after.targets[0].filled == 0.0
    assert sum("full round of purchased does" in note for note in notes) == 1


@pytest.mark.parametrize("animal_class,expected", [("male_kid", 0.25), ("female_kid", 0.75)])
def test_real_first_litter_yield_preserves_the_requested_sex(
    animal_class: str, expected: float
) -> None:
    assumptions = _assumptions()
    assumptions.reproduction.conception_rate = 0.5
    assumptions.reproduction.sex_ratio_female = 0.75
    assumptions = SimulationAssumptions.model_validate(assumptions.model_dump())
    yield_head = _completed(
        lambda: _marginal_kids_per_doe(
            assumptions,
            SaleTarget.model_validate_json(
                json.dumps({"month": 7, "animal_class": animal_class, "count": 1.0})
            ),
            1,
        )
    )
    assert yield_head == expected


def test_native_chunked_purchases_fill_the_public_document_budget_without_a_ghost_reservation() -> (
    None
):
    events = _completed(lambda: _purchases_from({1: 50_000_000.0}))
    assert len(events) == 500 and sum(event.count for event in events) == 50_000_000.0
    assumptions = _assumptions()
    document = assumptions.model_dump(mode="json")
    document["events"] = [event.model_dump(mode="json") for event in events]
    assert (
        len(
            _completed(
                lambda: SimulationAssumptions.model_validate_json(json.dumps(document))
            ).events
        )
        == 500
    )
    with pytest.raises(ValueError, match="1 events are already reserved"):
        _check_purchase_event_budget({1: 50_000_000.0}, reserved=1)


async def test_public_gap_closing_prices_the_current_complete_worst_case_tariff(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="planner-complete-gap-tariff@farm.in")
    assumptions = _assumptions()
    response = await client.post(
        "/api/planner/plan",
        json={
            "assumptions": assumptions.model_dump(mode="json"),
            "targets": [{"year_month": "2052-12", "animal_class": "doe", "count": 1.0}],
            "close_gaps": True,
            "risk_runs": 0,
        },
        headers=owner,
    )
    assert response.status_code == 200, response.text
    assert response.json()["horizon_months"] == 24
    # The current public worst-case tariff covers the outer evaluations,
    # all iterative evaluations and rollback/final reporting. This checks
    # the real paid ledger rather than an internal iteration constant.
    assert _run_limits._run_budget._spent("farm", int(owner["X-Farm-Id"])) == 288


async def test_public_unfilled_sale_revenue_keeps_its_canonical_zero_wire_value(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="planner-unfilled-zero-revenue@farm.in")
    for zero in (0.0, -0.0):
        document = _assumptions().model_dump(mode="json")
        document["sales"]["cull_doe_price_per_kg"] = zero
        response = await client.post(
            "/api/planner/plan",
            json={
                "assumptions": document,
                "targets": [{"year_month": "2052-12", "animal_class": "doe", "count": 1.0}],
                "close_gaps": False,
                "risk_runs": 0,
            },
            headers=owner,
        )
        assert response.status_code == 200, response.text
        row = response.json()["plan"]["before"]["targets"][0]
        assert row["filled"] == 0.0 and row["shortfall"] == 1.0
        # No executed sale has canonical zero revenue on the public wire.
        # The legitimate zero quote sign may remain visible elsewhere;
        # this checks only the realized sale revenue, not all zero fields.
        assert json.dumps(row["revenue"]) == "0.0"
