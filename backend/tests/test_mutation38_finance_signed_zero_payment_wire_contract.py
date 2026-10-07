"""A zero-loan sign must preserve the scheduled instalment's public wire value.

This is a narrow serialization-compatibility contract for the pre-final EMI
payment. Both loan fractions are valid monetary zero. Existing signed-zero
opening/interest/final-settlement fields are recorded without asserting full
result invariance; monetary magnitudes are identical.
"""

import copy
import json
import os
from pathlib import Path

import httpx

from .conftest import owner_with_farm
from .test_simulation_api import default_assumptions


async def test_public_zero_loan_sign_preserves_scheduled_payment_wire_value(
    client: httpx.AsyncClient,
) -> None:
    owner = await owner_with_farm(client, email="native-zero-loan-wire-owner@farm.in")
    assumptions = await default_assumptions(client, owner)
    assumptions["meta"]["horizon_months"] = 12
    assumptions["finance"]["loan_term_months"] = 2
    assumptions["finance"]["moratorium_months"] = 0
    assumptions["finance"]["interest_rate_annual"] = 0.11
    responses: list[httpx.Response] = []
    requests: list[dict[str, object]] = []
    for fraction in (0.0, -0.0):
        payload = copy.deepcopy(assumptions)
        payload["finance"]["loan_fraction_of_project_cost"] = fraction
        request: dict[str, object] = {"assumptions": payload}
        response = await client.post("/api/simulation/run", json=request, headers=owner)
        assert response.status_code == 200, response.text
        requests.append(request)
        responses.append(response)
    positive, negative = (response.json() for response in responses)
    assert positive["metrics"]["project_cost"] > 0.0
    assert negative["metrics"]["project_cost"] == positive["metrics"]["project_cost"]
    assert len(positive["amortization"]) == len(negative["amortization"]) == 2
    positive_payment = positive["amortization"][0]["payment"]
    negative_payment = negative["amortization"][0]["payment"]
    assert positive_payment == negative_payment == 0.0
    positive_wire, negative_wire = json.dumps(positive_payment), json.dumps(negative_payment)
    witness = {
        "scope": "public API +/-0 loan fraction; scheduled first EMI wire compatibility only",
        "requests": requests,
        "actual_response_wire": [response.text for response in responses],
        "actual_finance_and_amortization": [
            {"metrics": body["metrics"], "amortization": body["amortization"]}
            for body in (positive, negative)
        ],
        "first_scheduled_payment_wire": [positive_wire, negative_wire],
        "monetary_magnitudes_equal": positive_payment == negative_payment,
        "entire_signed_zero_response_invariance_claimed": False,
    }
    receipt = os.environ.get("MUTATION_RECEIPT_PATH")
    if receipt:
        Path(receipt).with_suffix(".signed-zero-payment-wire.json").write_text(
            json.dumps(witness, indent=2) + "\n"
        )
    print(json.dumps({"first_scheduled_payment_wire": [positive_wire, negative_wire]}))
    assert negative_wire == positive_wire, (
        "A valid monetary-zero loan sign must preserve the scheduled instalment wire value"
    )
