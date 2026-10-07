"""Public saved-plan/scenario JSON admission and calendar boundary contracts.

The full native default assumptions document is serialized before validation.
Expected failures are observed from the actual model, never substituted models.
"""

import json

import pytest
from pydantic import BaseModel, ValidationError

from app.main import create_app
from app.schemas.planner import PlannerPlanCreateIn, PlannerPlanUpdateIn
from app.schemas.simulation import ScenarioCreateIn, ScenarioUpdateIn
from app.simulation.assumptions import MetaAssumptions, SimulationAssumptions


def _document(model: type[BaseModel]) -> dict[str, object]:
    if model in (PlannerPlanUpdateIn, ScenarioUpdateIn):
        return {"expected_revision": 1}
    assumptions: object = json.loads(SimulationAssumptions().model_dump_json())
    document: dict[str, object] = {"name": "A", "assumptions": assumptions}
    if model is PlannerPlanCreateIn:
        document.update(
            start_year_month="2026-01",
            targets=[{"year_month": "2027-01", "animal_class": "male_grower", "count": 1.0}],
        )
    return document


def _admitted(model: type[BaseModel], document: dict[str, object]) -> bool:
    try:
        model.model_validate_json(json.dumps(document))
    except (ValidationError, IndexError, AttributeError):
        return False
    return True


@pytest.mark.parametrize(
    "model",
    [PlannerPlanCreateIn, PlannerPlanUpdateIn, ScenarioCreateIn, ScenarioUpdateIn],
    ids=["plan-create", "plan-update", "scenario-create", "scenario-update"],
)
@pytest.mark.parametrize(("length", "expected"), [(0, False), (1, True), (120, True), (121, False)])
def test_saved_document_name_json_admission(
    model: type[BaseModel], length: int, expected: bool
) -> None:
    document = _document(model)
    document["name"] = "n" * length
    assert _admitted(model, document) is expected


@pytest.mark.parametrize(
    "model",
    [PlannerPlanCreateIn, PlannerPlanUpdateIn, ScenarioCreateIn, ScenarioUpdateIn],
    ids=["plan-create", "plan-update", "scenario-create", "scenario-update"],
)
@pytest.mark.parametrize(("length", "expected"), [(2000, True), (2001, False)])
def test_saved_document_notes_json_admission(
    model: type[BaseModel], length: int, expected: bool
) -> None:
    document = _document(model)
    document["notes"] = "n" * length
    assert _admitted(model, document) is expected


@pytest.mark.parametrize(
    "model", [PlannerPlanCreateIn, PlannerPlanUpdateIn], ids=["create", "update"]
)
@pytest.mark.parametrize(("count", "expected"), [(0, False), (50, True), (51, False)])
def test_saved_plan_target_batch_json_admission(
    model: type[BaseModel], count: int, expected: bool
) -> None:
    document = _document(model)
    document["targets"] = [
        {"year_month": "2027-01", "animal_class": "male_grower", "count": 1.0} for _ in range(count)
    ]
    assert _admitted(model, document) is expected


@pytest.mark.parametrize("model", [PlannerPlanUpdateIn, ScenarioUpdateIn], ids=["plan", "scenario"])
def test_saved_document_revision_zero_is_not_a_valid_write_token(model: type[BaseModel]) -> None:
    assert not _admitted(model, {"expected_revision": 0, "notes": "Reviewed update"})


@pytest.mark.parametrize(
    ("horizon", "expected"),
    [(11, False), (12, True), (240, True), (241, False), ("12", False)],
    ids=["below-min", "min", "max", "above-max", "string-is-not-integer"],
)
def test_meta_horizon_json_admission(horizon: int | str, expected: bool) -> None:
    assert (
        _admitted(MetaAssumptions, {"horizon_months": horizon, "start_year_month": "2026-01"})
        is expected
    )


@pytest.mark.parametrize(
    ("month", "expected"),
    [
        ("1900-01", True),
        ("1900-12", True),
        ("2200-01", True),
        ("2200-12", True),
        ("1899-01", False),
        ("2201-12", False),
        ("2026-00", False),
        ("2026-13", False),
        ("2026-1", False),
        ("2026/01", False),
    ],
    ids=[
        "first-year-jan",
        "first-year-dec",
        "last-year-jan",
        "last-year-dec",
        "year-low",
        "year-high",
        "month-low",
        "month-high",
        "unpadded",
        "bad-separator",
    ],
)
def test_meta_real_calendar_json_admission(month: str, expected: bool) -> None:
    assert _admitted(MetaAssumptions, {"horizon_months": 12, "start_year_month": month}) is expected


@pytest.mark.parametrize("schema_name", ["PlannerPlanOut", "ScenarioOut"])
def test_saved_document_openapi_marks_validity_default_for_clients(schema_name: str) -> None:
    schema = create_app().openapi()["components"]["schemas"][schema_name]
    assert schema["properties"]["valid"]["default"] is True
