"""Published financing inputs retain field attribution and coherent approved cash.

These are native normal JSON contracts for the planner model, not a determination
of grant eligibility. Half-cent examples exercise its declared numerical tolerance.
"""

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from app.simulation.assumptions import (
    CostsAssumptions,
    FinanceAssumptions,
    SimulationAssumptions,
    SubsidyReceipt,
)


def _costs(payload: dict[str, Any]) -> CostsAssumptions:
    try:
        return CostsAssumptions.model_validate_json(json.dumps(payload))
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A published valid cost input must be admitted: {error}")


def _finance(payload: dict[str, Any]) -> FinanceAssumptions:
    try:
        return FinanceAssumptions.model_validate_json(json.dumps(payload))
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A coherent financing document must be admitted: {error}")


def _scenario(payload: dict[str, Any]) -> SimulationAssumptions:
    try:
        return SimulationAssumptions.model_validate_json(json.dumps(payload))
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A coherent qualifying-unit scenario must be admitted: {error}")


def _rejected(parse: Callable[[], object], *, field: str | None = None) -> None:
    try:
        parse()
    except ValidationError as error:
        if field is not None:
            assert any(tuple(item["loc"]) == (field,) for item in error.errors()), (
                "An out-of-editor-range input must identify its actual field before "
                f"any model-level invariant: {error.errors()}"
            )
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"An invalid planner document must produce a typed validation error: {error}")
    else:
        pytest.fail("An incoherent planner document must be rejected")


@pytest.mark.parametrize(
    "field,lower,upper",
    [
        ("labour_per_head_threshold", 1, 10**15),
        ("breeding_stock_useful_life_months", 1, 240),
        ("shed_useful_life_years", 1, 100),
        ("equipment_useful_life_years", 1, 50),
    ],
    ids=["worker-capacity", "breeder-life", "shed-life", "equipment-life"],
)
@pytest.mark.parametrize("upper_endpoint", [False, True], ids=["lower", "upper"])
def test_published_cost_editor_endpoints_are_admitted(
    field: str, lower: int, upper: int, upper_endpoint: bool
) -> None:
    value = upper if upper_endpoint else lower
    assert _costs({field: value}).model_dump()[field] == value


@pytest.mark.parametrize(
    "field,lower,upper",
    [
        ("labour_per_head_threshold", 1, 10**15),
        ("breeding_stock_useful_life_months", 1, 240),
        ("shed_useful_life_years", 1, 100),
        ("equipment_useful_life_years", 1, 50),
    ],
    ids=["worker-capacity", "breeder-life", "shed-life", "equipment-life"],
)
@pytest.mark.parametrize("above", [False, True], ids=["below", "above"])
def test_out_of_range_cost_input_identifies_the_editor_field(
    field: str, lower: int, upper: int, above: bool
) -> None:
    value = upper + 1 if above else lower - 1
    _rejected(lambda: CostsAssumptions.model_validate_json(json.dumps({field: value})), field=field)


@pytest.mark.parametrize("basis", ["projected_peak", "opening_herd"])
def test_unapproved_capacity_is_valid_only_for_observed_capacity_modes(basis: str) -> None:
    assert _costs({"capacity_basis": basis, "planned_capacity_head": 0}).planned_capacity_head == 0


def test_user_approved_capacity_requires_an_actual_positive_supplied_plan() -> None:
    assert (
        _costs({"capacity_basis": "planned", "planned_capacity_head": 1}).planned_capacity_head == 1
    )
    _rejected(lambda: CostsAssumptions.model_validate_json('{"capacity_basis":"planned"}'))
    _rejected(
        lambda: CostsAssumptions.model_validate_json(
            '{"capacity_basis":"planned","planned_capacity_head":0}'
        )
    )


@pytest.mark.parametrize(
    "field,lower,upper",
    [("loan_term_months", 1, 180), ("moratorium_months", 0, 60), ("working_capital_months", 0, 24)],
    ids=["loan-term", "moratorium", "working-reserve"],
)
@pytest.mark.parametrize("upper_endpoint", [False, True], ids=["lower", "upper"])
def test_published_finance_editor_endpoints_are_admitted(
    field: str, lower: int, upper: int, upper_endpoint: bool
) -> None:
    value = upper if upper_endpoint else lower
    payload = {"loan_term_months": 180, "moratorium_months": 0, field: value}
    assert _finance(payload).model_dump()[field] == value


@pytest.mark.parametrize(
    "field,lower,upper",
    [("loan_term_months", 1, 180), ("moratorium_months", 0, 60), ("working_capital_months", 0, 24)],
    ids=["loan-term", "moratorium", "working-reserve"],
)
@pytest.mark.parametrize("above", [False, True], ids=["below", "above"])
def test_out_of_range_finance_input_identifies_its_field(
    field: str, lower: int, upper: int, above: bool
) -> None:
    value = upper + 1 if above else lower - 1
    payload = {"loan_term_months": 180, "moratorium_months": 0, field: value}
    _rejected(lambda: FinanceAssumptions.model_validate_json(json.dumps(payload)), field=field)


@pytest.mark.parametrize("month", [1, 240], ids=["first-installment-month", "last-editor-month"])
def test_receipt_month_editor_endpoints_remain_admitted(month: int) -> None:
    try:
        receipt = SubsidyReceipt.model_validate_json(json.dumps({"month": month, "amount": 1.0}))
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError) as error:
        pytest.fail(f"A published receipt-month endpoint must be admitted: {error}")
    assert receipt.month == month


@pytest.mark.parametrize("month", [0, 241], ids=["prior-financing-month", "after-editor-limit"])
def test_receipt_month_outside_the_editor_range_has_a_field_error(month: int) -> None:
    _rejected(
        lambda: SubsidyReceipt.model_validate_json(json.dumps({"month": month, "amount": 1.0})),
        field="month",
    )


@pytest.mark.parametrize(
    "counts",
    [{}, {"nlm_unit_females": 0, "nlm_unit_males": 0}],
    ids=["unselected-unit", "explicit-empty-unit"],
)
def test_unfunded_unit_configuration_is_not_an_approval(counts: dict[str, int]) -> None:
    model = _finance(counts)
    assert model.nlm_approved_subsidy_amount is None
    assert model.nlm_subsidy_receipts == []


@pytest.mark.parametrize("missing", ["nlm_unit_females", "nlm_unit_males"])
def test_an_explicit_unit_configuration_requires_both_sexes(missing: str) -> None:
    payload = {"nlm_unit_females": 100, "nlm_unit_males": 5}
    payload.pop(missing)
    _rejected(lambda: FinanceAssumptions.model_validate_json(json.dumps(payload)))


def _approved() -> dict[str, Any]:
    return {
        "nlm_subsidy": True,
        "nlm_unit_females": 100,
        "nlm_unit_males": 5,
        "nlm_eligible_capital_cost": 2_000_000.0,
        "nlm_approved_subsidy_amount": 1_000_000.0,
        "nlm_subsidy_receipts": [
            {"month": 1, "amount": 500_000.0},
            {"month": 12, "amount": 500_000.0},
        ],
    }


@pytest.mark.parametrize("missing", ["nlm_subsidy", "nlm_eligible_capital_cost"])
def test_declared_approval_needs_scheme_and_known_eligible_budget(missing: str) -> None:
    payload = _approved()
    payload.pop(missing)
    _rejected(lambda: FinanceAssumptions.model_validate_json(json.dumps(payload)))


def test_approved_award_can_equal_half_the_eligible_budget_and_preserves_installments() -> None:
    payload = _approved()
    model = _finance(payload)
    assert [(row.month, row.amount) for row in model.nlm_subsidy_receipts] == [
        (1, 500_000.0),
        (12, 500_000.0),
    ]
    payload["nlm_approved_subsidy_amount"] = 1_000_001.0
    payload["nlm_subsidy_receipts"] = []
    _rejected(lambda: FinanceAssumptions.model_validate_json(json.dumps(payload)))


@pytest.mark.parametrize("approved", [None, 0.0], ids=["unknown-award", "zero-award"])
def test_receipts_require_a_real_positive_award(approved: float | None) -> None:
    payload = _approved()
    payload["nlm_approved_subsidy_amount"] = approved
    payload["nlm_subsidy_receipts"] = [{"month": 1, "amount": 0.001}]
    _rejected(lambda: FinanceAssumptions.model_validate_json(json.dumps(payload)))


@pytest.mark.parametrize(
    "fault", ["over-budget", "unequal-half", "reversed-dates", "third-installment"]
)
def test_supplied_installments_conserve_award_halves_and_chronology(fault: str) -> None:
    payload = _approved()
    if fault == "over-budget":
        payload["nlm_subsidy_receipts"][1]["amount"] += 1.0
    elif fault == "unequal-half":
        payload["nlm_subsidy_receipts"] = [
            {"month": 1, "amount": 499_999.0},
            {"month": 12, "amount": 500_001.0},
        ]
    elif fault == "reversed-dates":
        payload["nlm_subsidy_receipts"].reverse()
    else:
        payload["nlm_subsidy_receipts"] = [
            {"month": month, "amount": 500_000.0} for month in [1, 6, 12]
        ]
    _rejected(
        lambda: FinanceAssumptions.model_validate_json(json.dumps(payload)),
        field="nlm_subsidy_receipts" if fault == "third-installment" else None,
    )


def test_half_cent_receipt_tolerance_admits_its_exact_boundary() -> None:
    payload = {
        "nlm_subsidy": True,
        "nlm_eligible_capital_cost": 0.02,
        "nlm_approved_subsidy_amount": 0.01,
        "nlm_subsidy_receipts": [{"month": 1, "amount": 0.01}, {"month": 2, "amount": 0.005}],
    }
    model = _finance(payload)
    assert sum(row.amount for row in model.nlm_subsidy_receipts) == 0.015
    assert abs(model.nlm_subsidy_receipts[0].amount - 0.005) == 0.005


@pytest.mark.parametrize("explicit", [False, True], ids=["opening-unit", "explicit-unit"])
def test_qualifying_unit_admits_its_published_cap_without_inventing_a_larger_unit(
    explicit: bool,
) -> None:
    payload = _approved()
    if not explicit:
        payload.pop("nlm_unit_females")
        payload.pop("nlm_unit_males")
    model = _scenario({"herd": {"does": 100, "bucks": 5}, "finance": payload})
    assert model.finance.nlm_approved_subsidy_amount == 1_000_000.0


@pytest.mark.parametrize("unit", [(50, 2), (150, 8)], ids=["below-minimum", "unlisted-unit"])
def test_nonqualifying_unit_cannot_receive_a_positive_approval(unit: tuple[int, int]) -> None:
    payload = _approved()
    payload.update(
        {
            "nlm_unit_females": unit[0],
            "nlm_unit_males": unit[1],
            "nlm_approved_subsidy_amount": 100_000.0,
            "nlm_subsidy_receipts": [],
        }
    )
    _rejected(lambda: SimulationAssumptions.model_validate_json(json.dumps({"finance": payload})))


def test_explicit_approved_unit_takes_precedence_over_the_larger_opening_herd() -> None:
    payload = _approved()
    payload.update(
        {
            "nlm_eligible_capital_cost": 3_000_000.0,
            "nlm_approved_subsidy_amount": 1_500_000.0,
            "nlm_subsidy_receipts": [],
        }
    )
    _rejected(
        lambda: SimulationAssumptions.model_validate_json(
            json.dumps({"herd": {"does": 200, "bucks": 10}, "finance": payload})
        )
    )


def test_full_project_funding_is_admitted_without_negative_promoter_equity() -> None:
    model = _finance({"loan_fraction_of_project_cost": 1.0, "subsidy_fraction": 0.0})
    assert model.loan_fraction_of_project_cost + model.subsidy_fraction == 1.0
    _rejected(
        lambda: FinanceAssumptions.model_validate_json(
            '{"loan_fraction_of_project_cost":1.0,"subsidy_fraction":0.01}'
        )
    )


def test_explicit_zero_award_books_no_funding_for_a_nonqualifying_unit() -> None:
    model = _scenario(
        {
            "herd": {"does": 0, "bucks": 0, "auto_purchase_bucks": False},
            "finance": {
                "nlm_subsidy": True,
                "nlm_eligible_capital_cost": 0.0,
                "nlm_approved_subsidy_amount": 0.0,
            },
        }
    )
    assert model.finance.nlm_approved_subsidy_amount == 0.0
    assert model.finance.nlm_subsidy_receipts == []
