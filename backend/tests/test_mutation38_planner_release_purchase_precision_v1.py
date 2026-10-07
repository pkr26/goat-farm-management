"""Current release whole-animal purchase decision for an admitted precision boundary.

Reference: actual original-source native report in scratch/mutation38/
planner-ee-native-exploration/c01d6a46b8274722b269a6c80983216d/original-results.json
SHA256 5d2654eab34d67debd2c3f23fac31875087ae1e7cb0dacd6d569b56c5f4414d4.
No pool threshold, internal iteration count, random generator, or core result
is supplied by this regression. It exercises the native public plan report.
"""

import json

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.planner import SaleTarget, build_plan_report


def test_current_release_retains_whole_animal_purchase_decision_at_valid_cull_boundary() -> None:
    assumptions = SimulationAssumptions.model_validate_json(
        json.dumps(
            {
                "meta": {"start_year_month": "2051-01", "horizon_months": 60},
                "herd": {
                    "does": 0,
                    "bucks": 0,
                    "auto_purchase_bucks": True,
                    "purchased_doe_settling_months": 0,
                    "max_breeding_does": 0,
                    "female_retention_fraction": 0.0,
                },
                "growth": {"sale_age_months": 24},
                "reproduction": {
                    "conception_rate": 0.47449505440769585,
                    "gestation_months": 12,
                    "lactation_months": 12,
                    "months_open_before_breeding": 12,
                    "litter_size": 2.0,
                    "stillbirth_rate": 0.0,
                    "sex_ratio_female": 0.0,
                    "parity_multipliers": {"litter_size": [1.0], "conception_rate": [1.0]},
                },
                "mortality": {
                    "kid_pre_weaning": 0.0,
                    "kid_post_weaning": 0.0,
                    "grower": 0.0,
                    "adult": 0.0,
                },
                "culling": {"doe_cull_rate_annual": 0.9999923706054688, "buck_rotation_years": 10},
                "sales": {"festival_sale_months": []},
            }
        )
    )
    target = SaleTarget.model_validate_json(
        '{"month":60,"animal_class":"male_grower","count":0.7500040809489485}'
    )
    report = build_plan_report(assumptions, [target])
    assert report.gaps_closed
    assert report.after is not None and report.after.targets[0].met
    assert sum(event.count for event in report.recommended_purchases) == 83199.0
