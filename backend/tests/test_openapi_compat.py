"""The compatibility gate must detect changes a fresh snapshot cannot."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest

from scripts.openapi_compat import apply_exact_waivers, find_breaking_changes


def _contract() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "components": {
            "securitySchemes": {
                "Bearer": {"type": "http", "scheme": "bearer"},
            },
            "schemas": {
                "Request": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["OPEN", "DONE"]},
                        "note": {"type": "string"},
                    },
                    "required": ["status"],
                },
                "Response": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "minimum": 0},
                        "status": {"type": "string", "enum": ["OPEN", "DONE"]},
                    },
                    "required": ["id", "status"],
                },
            },
        },
        "paths": {
            "/items/{item_id}": {
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "integer"},
                    }
                ],
                "post": {
                    "operationId": "updateItem",
                    "security": [{"Bearer": []}],
                    "parameters": [
                        {
                            "name": "mode",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "enum": ["safe", "fast"]},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/Request"}}
                        },
                    },
                    "responses": {
                        "200": {
                            "headers": {"ETag": {"schema": {"type": "string"}}},
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Response"}
                                }
                            },
                        },
                        "409": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    },
                },
            },
        },
    }


def test_compatibility_gate_accepts_additive_optional_input_and_response_fields() -> None:
    baseline = _contract()
    candidate = deepcopy(baseline)
    operation = candidate["paths"]["/items/{item_id}"]["post"]
    operation["parameters"].append(
        {
            "name": "trace",
            "in": "query",
            "required": False,
            "schema": {"type": "string"},
        }
    )
    response = candidate["components"]["schemas"]["Response"]
    response["properties"]["server_version"] = {"type": "string"}
    response["required"].append("server_version")

    assert find_breaking_changes(baseline, candidate) == []


def test_compatibility_waiver_is_bound_to_exact_documents_and_match_count() -> None:
    baseline_bytes = json.dumps(_contract(), sort_keys=True).encode()
    candidate = _contract()
    candidate["paths"]["/items/{item_id}"]["post"]["responses"].pop("409")
    candidate_bytes = json.dumps(candidate, sort_keys=True).encode()
    changes = find_breaking_changes(_contract(), candidate)
    document = {
        "schema": 1,
        "waivers": [
            {
                "baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
                "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                "change_suffix": ".responses.409: response removed",
                "expected_matches": 1,
                "reason": "A reviewed test-only compatibility transition.",
            }
        ],
    }

    assert apply_exact_waivers(changes, baseline_bytes, candidate_bytes, document) == ([], 1)
    altered_candidate = candidate_bytes + b"\n"
    assert apply_exact_waivers(changes, baseline_bytes, altered_candidate, document) == (
        changes,
        0,
    )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda value: value["paths"]["/items/{item_id}"].pop("post"),
            "operation removed",
        ),
        (
            lambda value: value["components"]["schemas"]["Request"]["required"].append("note"),
            "new required input property 'note'",
        ),
        (
            lambda value: value["components"]["schemas"]["Request"]["properties"]["status"].update(
                {"enum": ["OPEN"]}
            ),
            "enum values removed",
        ),
        (
            lambda value: value["components"]["schemas"]["Response"]["required"].remove("status"),
            "is no longer guaranteed",
        ),
        (
            lambda value: value["paths"]["/items/{item_id}"]["post"]["parameters"][0].update(
                {"required": True}
            ),
            "optional parameter became required",
        ),
        (
            lambda value: value["paths"]["/items/{item_id}"]["post"]["responses"].pop("409"),
            "response removed",
        ),
        (
            lambda value: value["paths"]["/items/{item_id}"]["post"]["responses"]["200"][
                "headers"
            ].pop("ETag"),
            "documented header removed",
        ),
        (
            lambda value: value["paths"]["/items/{item_id}"]["post"].update({"security": []}),
            "security requirements changed",
        ),
        (
            lambda value: value["components"]["schemas"]["Response"]["properties"]["id"].update(
                {"type": "string"}
            ),
            "accepted type set changed incompatibly",
        ),
        (
            lambda value: value["components"]["schemas"]["Request"]["properties"]["note"].update(
                {"maxLength": 10}
            ),
            "maxLength changed incompatibly",
        ),
        (
            lambda value: value["components"]["schemas"]["Request"].update(
                {"additionalProperties": False}
            ),
            "additionalProperties now restricts accepted input properties",
        ),
        (
            lambda value: value["components"]["schemas"]["Response"]["properties"]["status"].update(
                {"enum": ["OPEN", "DONE", "PAUSED"]}
            ),
            "new response enum values",
        ),
        (
            lambda value: value["components"]["schemas"]["Response"]["properties"]["id"].update(
                {"minimum": -1}
            ),
            "lower bound changed incompatibly",
        ),
    ],
)
def test_compatibility_gate_rejects_breaking_contract_changes(
    mutate: Any,
    expected: str,
) -> None:
    baseline = _contract()
    candidate = deepcopy(baseline)
    mutate(candidate)

    assert any(expected in change for change in find_breaking_changes(baseline, candidate))
