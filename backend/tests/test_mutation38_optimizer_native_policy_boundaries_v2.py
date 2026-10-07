"""Full native JSON optimizer decisions at the admitted policy boundaries."""

import json

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import SimulationAssumptions
from app.simulation.optimization import run_optimization
from app.simulation.results import OptimizationCandidate, OptimizationResult


def _document() -> dict[str, object]:
    document: object = json.loads(SimulationAssumptions().model_dump_json())
    assert isinstance(document, dict)
    return document


def _run_document(document: dict[str, object]) -> OptimizationResult | None:
    assumptions = SimulationAssumptions.model_validate_json(json.dumps(document))
    try:
        return run_optimization(assumptions)
    except ValidationError:
        # A fully admitted document must not generate an inadmissible candidate.
        # This observes the real revalidation result; no core/report is replaced.
        return None


def _candidates(result: OptimizationResult) -> list[OptimizationCandidate]:
    return [
        result.baseline,
        *([result.recommended] if result.recommended else []),
        *result.alternatives,
    ]


def _narrow_policy(document: dict[str, object]) -> None:
    document["meta"] = {"horizon_months": 12, "start_year_month": "2026-01"}
    # Work with the genuinely serialized, complete native defaults document.
    herd = document["herd"]
    policy = document["optimization"]
    assert isinstance(herd, dict) and isinstance(policy, dict)
    herd.update(does=10, bucks=1, max_breeding_does=10)
    policy.update(
        doe_scale_low=0.8,
        doe_scale_high=1.2,
        doe_scale_steps=2,
        sale_age_radius_months=0,
        retention_step=0.0,
        loan_fraction_step=0.0,
        festival_hold_radius_months=0,
        service_cull_radius_months=0,
        max_candidates=7,
    )


@pytest.mark.parametrize("axis", ["festival", "service"])
@pytest.mark.parametrize("anchor", [0, 12])
def test_optimizer_covers_legal_adjacent_policy_boundaries_for_both_scaled_herds(
    axis: str, anchor: int
) -> None:
    document = _document()
    _narrow_policy(document)
    policy = document["optimization"]
    assert isinstance(policy, dict)
    group_name, field, radius, candidate_field = (
        ("sales", "festival_hold_months", "festival_hold_radius_months", "festival_hold_months")
        if axis == "festival"
        else (
            "reproduction",
            "max_services_before_cull",
            "service_cull_radius_months",
            "max_services_before_cull",
        )
    )
    group = document[group_name]
    assert isinstance(group, dict)
    group[field] = anchor
    policy[radius] = 1
    result = _run_document(document)
    assert result is not None, "An admitted policy generated an out-of-schema search candidate"
    actual = {
        (candidate.starting_does, getattr(candidate, candidate_field))
        for candidate in _candidates(result)
        if candidate.starting_does != 10
    }
    legal_neighbor = 1 if anchor == 0 else 11
    # The small complete neighborhood fits the configured work budget. Both
    # operational policies must remain available at both real herd sizes.
    assert {(8, anchor), (8, legal_neighbor), (12, anchor), (12, legal_neighbor)} <= actual
    assert all(0 <= value <= 12 for _does, value in actual)


def test_zero_doe_optimizer_does_not_recommend_an_unneeded_foundation_buck() -> None:
    document = _document()
    _narrow_policy(document)
    herd = document["herd"]
    policy = document["optimization"]
    assert isinstance(herd, dict) and isinstance(policy, dict)
    herd.update(does=0, bucks=0, max_breeding_does=0, auto_purchase_bucks=True)
    policy.update(
        doe_scale_low=1.0, doe_scale_high=1.0, doe_scale_steps=1, festival_hold_radius_months=1
    )
    result = _run_document(document)
    assert result is not None
    assert all(
        candidate.starting_does == 0 and candidate.starting_bucks == 0
        for candidate in _candidates(result)
    )


def test_sale_age_ceiling_does_not_generate_invalid_optimizer_candidates() -> None:
    document = _document()
    _narrow_policy(document)
    growth = document["growth"]
    policy = document["optimization"]
    assert isinstance(growth, dict) and isinstance(policy, dict)
    growth["sale_age_months"] = 24
    policy["sale_age_radius_months"] = 1
    result = _run_document(document)
    assert result is not None
    actual = {
        (candidate.starting_does, candidate.sale_age_months) for candidate in _candidates(result)
    }
    assert {(8, 23), (8, 24), (12, 23), (12, 24)} <= actual


def test_optimizer_debt_search_preserves_valid_total_financing_shares() -> None:
    document = _document()
    _narrow_policy(document)
    finance = document["finance"]
    policy = document["optimization"]
    assert isinstance(finance, dict) and isinstance(policy, dict)
    finance.update(subsidy_fraction=0.7, loan_fraction_of_project_cost=0.3)
    policy["loan_fraction_step"] = 0.1
    result = _run_document(document)
    assert result is not None
    assert all(candidate.loan_fraction + 0.7 <= 1.0 + 1e-12 for candidate in _candidates(result))
