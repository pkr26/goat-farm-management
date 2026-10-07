"""An impossible earlier sale must not stop a later feasible sale from closing."""

import json

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.planner import SaleTarget, close_gaps


def test_unavailable_earlier_target_preserves_later_feasible_purchase_progress() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"start_year_month": "2051-01", "horizon_months": 48},
                "herd": {
                    "does": 0,
                    "bucks": 0,
                    "auto_purchase_bucks": True,
                    "purchased_doe_settling_months": 0,
                    "max_breeding_does": 0,
                },
                "reproduction": {
                    "conception_rate": 0.2,
                    "litter_size": 2.0,
                    "stillbirth_rate": 0.0,
                    "sex_ratio_female": 0.5,
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
    later = SaleTarget.model_validate_json('{"month":10,"animal_class":"male_kid","count":10.0}')
    impossible = SaleTarget.model_validate_json(
        '{"month":1,"animal_class":"male_grower","count":1.0}'
    )
    _, standalone, closed, _ = close_gaps(assumptions, [later])
    assert closed and standalone.targets[0].met
    _, combined, _, _ = close_gaps(assumptions, [impossible, later])
    assert combined.targets[0].filled == 0.0
    assert combined.targets[1].met
    assert combined.targets[1].filled == standalone.targets[0].filled
