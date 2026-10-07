"""A genuine purchased grower at the one-month class boundary completes and conserves head."""

import pytest

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.engine import _run_core, weight_at_age


def test_default_arrival_age_in_the_shortest_nonempty_female_grower_chain_completes() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        '{"meta":{"horizon_months":12},"herd":{"does":0,"bucks":0,"auto_purchase_bucks":false,"female_retention_fraction":1,"foundation_flock_state":"open"},"reproduction":{"age_at_first_breeding_months":7,"conception_rate":0},"mortality":{"kid_pre_weaning":0,"kid_post_weaning":0,"grower":0,"adult":0},"culling":{"doe_cull_rate_annual":0},"events":[{"month":1,"kind":"purchase","animal_class":"female_grower","count":1}]}'
    )
    try:
        core = _run_core(assumptions)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(
            f"An admitted one-slot grower purchase must complete before graduation: {error}"
        )
    assert len(core.months) == 12
    assert core.months[0].purchases_head == 1.0
    assert core.months[0].purchase_cost == pytest.approx(
        weight_at_age(6, assumptions.growth, assumptions.growth.adult_weight_doe_kg)
        * core.months[0].meat_price_per_kg
    )
    assert all(row.total_herd == 1.0 for row in core.months)
    assert all(
        row.births == row.deaths == row.sales_head == row.culls_head == 0.0 for row in core.months
    )
