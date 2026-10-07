"""Released growth inputs and actual purchased-animal forecast calendars."""

import json
from itertools import pairwise

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import GrowthAssumptions, SimulationAssumptions
from app.simulation.defaults import get_preset
from app.simulation.engine import run_simulation
from app.simulation.snapshot import herd_cohorts


def test_growth_editor_thirteen_age_facts_admit_plateaus_and_reject_missing_ages() -> None:
    # The shipped editor describes exactly ages zero through twelve, permits
    # equal consecutive weights and keeps the birth fact in sync with age zero.
    for curve in ([2.5] * 13, [2.5, 3.0] + [4.0] * 11):
        try:
            result = GrowthAssumptions.model_validate_json(
                json.dumps({"weight_by_age_months": curve})
            )
        except ValidationError as exc:
            pytest.fail(f"A complete editor growth fact must admit: {exc}")
        assert result.weight_by_age_months == curve
        assert result.birth_weight_kg == curve[0]
    for curve in ([2.5] * 12, [2.5] * 14, [3.0] * 13):
        with pytest.raises(ValidationError):
            GrowthAssumptions.model_validate_json(json.dumps({"weight_by_age_months": curve}))


def test_latest_supported_first_breeding_precedes_the_shortest_supported_doe_lifespan() -> None:
    # The JSON editor caps first service at 30 months and starts the culling
    # policy at 36 months, so the supported endpoints leave a real lifespan.
    payload = {
        "reproduction": {"age_at_first_breeding_months": 30},
        "culling": {"max_doe_age_months": 36},
    }
    try:
        result = SimulationAssumptions.model_validate_json(json.dumps(payload))
    except ValidationError as exc:
        pytest.fail(f"Supported breed and lifespan endpoints must admit: {exc}")
    assert result.reproduction.age_at_first_breeding_months == 30
    assert result.culling.max_doe_age_months == 36


@pytest.mark.parametrize("breed", ["osmanabadi", "sirohi", "barbari", "jamunapari", "boer_cross"])
def test_released_breed_factories_return_complete_fresh_system_variants(breed: str) -> None:
    try:
        stall = get_preset(breed, "stall_fed")
        field = get_preset(breed, "semi_intensive")
        another = get_preset(breed)
        SimulationAssumptions.model_validate(stall.model_dump())
        SimulationAssumptions.model_validate(field.model_dump())
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A released breed and production system must be usable: {exc}")
    assert stall is not another and stall.growth is not another.growth
    assert stall.model_dump() == another.model_dump()
    for preset in (stall, field):
        curve = preset.growth.weight_by_age_months
        assert len(curve) == 13 and curve[0] == preset.growth.birth_weight_kg
        assert all(left <= right for left, right in pairwise(curve))
        assert curve[-1] <= min(
            preset.growth.adult_weight_doe_kg, preset.growth.adult_weight_buck_kg
        )
    assert field.growth.weight_by_age_months[0] == stall.growth.weight_by_age_months[0]
    assert field.growth.weight_by_age_months[3] < stall.growth.weight_by_age_months[3]
    assert field.feed.grazing_dm_fraction > stall.feed.grazing_dm_fraction
    assert field.mortality.adult > stall.mortality.adult
    assert field.mortality.kid_pre_weaning > stall.mortality.kid_pre_weaning


def test_herd_snapshot_unknown_ages_and_the_omitted_sire_boundary_retain_each_actual_head() -> None:
    rows: list[tuple[str, int | None]] = [
        (sex, age) for sex in ("F", "M") for age in (None, 0, 2, 3, 5, 6, 11, 12)
    ]
    try:
        grouped = herd_cohorts(iter(rows), doe_adult_age=12)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A complete current herd snapshot must group every live animal: {exc}")
    assert grouped == {
        "does": 2,
        "bucks": 2,
        "f_kids": 2,
        "f_weaners": 2,
        "f_growers": 2,
        "m_kids": 2,
        "m_weaners": 2,
        "m_growers": 2,
    }
    assert sum(grouped.values()) == len(rows)


def _forecast_payload() -> dict[str, object]:
    return {
        "meta": {"horizon_months": 12},
        "herd": {
            "does": 0,
            "bucks": 1,
            "auto_purchase_bucks": False,
            "foundation_flock_state": "open",
            "foundation_doe_age_min_months": 18,
            "foundation_doe_age_max_months": 18,
        },
        "reproduction": {
            "conception_rate": 1.0,
            "parity_multipliers": {"litter_size": [1.0], "conception_rate": [1.0]},
        },
        "mortality": {"kid_pre_weaning": 0.0, "kid_post_weaning": 0.0, "grower": 0.0, "adult": 0.0},
        "culling": {"doe_cull_rate_annual": 0.0, "buck_rotation_years": 10},
        "events": [{"month": 1, "kind": "purchase", "animal_class": "doe", "count": 4.0}],
    }


@pytest.mark.parametrize("settling_months", [0, 1, 3, 6])
def test_real_purchased_does_remain_present_and_first_conceive_after_the_whole_settling_period(
    settling_months: int,
) -> None:
    payload = _forecast_payload()
    herd = payload["herd"]
    assert isinstance(herd, dict)
    herd["purchased_doe_settling_months"] = settling_months
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"A complete purchased-doe acclimatisation forecast must return: {exc}")
    assert len(result.months) == 12
    for row in result.months[:settling_months]:
        assert row.pregnant_does == 0.0
        assert row.open_does == pytest.approx(4.0)
        assert row.total_herd == pytest.approx(5.0)
    assert result.months[settling_months].pregnant_does == pytest.approx(4.0)
    previous = 1.0
    for row in result.months:
        assert row.deaths == 0.0
        assert row.total_herd == pytest.approx(
            previous + row.births + row.purchases_head - row.sales_head - row.culls_head
        )
        previous = row.total_herd


@pytest.mark.parametrize("service_limit", [2, 3])
def test_real_repeat_breeders_conserve_heads_and_cull_after_their_last_permitted_attempt(
    service_limit: int,
) -> None:
    payload = _forecast_payload()
    payload["events"] = []
    herd = payload["herd"]
    reproduction = payload["reproduction"]
    assert isinstance(herd, dict) and isinstance(reproduction, dict)
    herd["does"] = 4
    reproduction["conception_rate"] = 0.0
    reproduction["max_services_before_cull"] = service_limit
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(f"Supported native repeat-service policy must finish: {exc}")
    assert all(row.births == 0.0 and row.purchases_head == 0.0 for row in result.months)
    assert all(row.culls_head == 0.0 for row in result.months[: service_limit - 1])
    assert result.months[service_limit - 1].culls_head == pytest.approx(4.0)
    assert result.months[-1].total_herd == pytest.approx(1.0)


def test_real_scheduled_adult_purchases_with_the_latest_supported_age_finish_and_reconcile() -> (
    None
):
    payload = _forecast_payload()
    herd = payload["herd"]
    assert isinstance(herd, dict)
    herd["foundation_doe_age_min_months"] = 180
    herd["foundation_doe_age_max_months"] = 180
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(payload))
    try:
        result = run_simulation(assumptions, with_break_even=False)
        json.dumps(result.model_dump(mode="json"), allow_nan=False)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as exc:
        pytest.fail(
            f"Schema-valid adult purchase ages must complete with bounded stock life: {exc}"
        )
    previous = 1.0
    for row in result.months:
        assert row.total_herd == pytest.approx(
            previous + row.births + row.purchases_head - row.sales_head - row.culls_head
        )
        previous = row.total_herd
    assert sum(row.purchases_head for row in result.months) == pytest.approx(4.0)
