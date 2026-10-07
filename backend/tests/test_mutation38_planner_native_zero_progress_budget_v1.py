"""A supported native planner document must roll back purchases with zero yield.

The exported native planner composes sales and purchases inside the public
500-event SimulationAssumptions budget. These 168 sales and first-round
168 purchase chunks fit that document; every target is schema-admitted.
"""

import json

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.planner import SaleTarget, build_plan_report


def test_supported_native_event_document_discards_a_zero_yield_purchase_round() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"start_year_month": "2051-01", "horizon_months": 24},
                "herd": {
                    "does": 0,
                    "bucks": 0,
                    "auto_purchase_bucks": False,
                    "purchased_doe_settling_months": 0,
                    "max_breeding_does": 0,
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
            }
        )
    )
    targets = [
        SaleTarget.model_validate_json('{"month":24,"animal_class":"male_grower","count":100000.0}')
        for _ in range(168)
    ]
    try:
        report = build_plan_report(assumptions, targets)
    except ValueError:
        report = None
    assert report is not None, "A zero-yield round must roll back inside the admitted event budget"
    assert not report.gaps_closed
    assert report.recommended_purchases == []
    assert report.after is not None and len(report.after.targets) == len(targets)
    assert all(fill.filled == 0.0 for fill in report.after.targets)
