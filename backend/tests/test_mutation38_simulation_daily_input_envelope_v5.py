"""Normal JSON daily-operations windows, result sizes and state boundaries."""

import json

import pytest
from pydantic import ValidationError

from app.schemas.common import MAX_ID
from app.schemas.ops_simulation import DailyOpsRunIn
from app.simulation.daily_ops import DailyOpsInput


def _document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "start_date": "2026-01-01",
        "animals": [{"tag": "D1", "sex": "F", "bucket": "FOUNDATION", "age_months": 12}],
    }
    document.update(updates)
    return document


def _admitted(
    model: type[DailyOpsRunIn | DailyOpsInput], raw: str
) -> DailyOpsRunIn | DailyOpsInput:
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        pytest.fail(f"A supported ordinary JSON daily-operation document must be admitted: {exc}")


@pytest.mark.parametrize("days", [7, 365])
def test_run_and_engine_accept_the_supported_daily_window_endpoints(days: int) -> None:
    raw = json.dumps(_document(horizon_days=days))
    assert _admitted(DailyOpsRunIn, raw).horizon_days == days
    assert _admitted(DailyOpsInput, raw).horizon_days == days


@pytest.mark.parametrize("days", [6, 366])
def test_run_and_engine_reject_days_outside_the_supported_daily_window(days: int) -> None:
    for model in (DailyOpsRunIn, DailyOpsInput):
        with pytest.raises(ValidationError) as rejected:
            model.model_validate_json(json.dumps(_document(horizon_days=days)))
        assert any(error["loc"] == ("horizon_days",) for error in rejected.value.errors())


def test_omitted_daily_window_and_seed_match_the_operator_form() -> None:
    raw = json.dumps(_document())
    for model in (DailyOpsRunIn, DailyOpsInput):
        admitted = _admitted(model, raw)
        assert admitted.horizon_days == 90
        assert admitted.seed == 2026


@pytest.mark.parametrize("seed", [-MAX_ID, MAX_ID])
def test_seed_integer_endpoints_are_echoable_without_coercion(seed: int) -> None:
    raw = json.dumps(_document(seed=seed))
    assert _admitted(DailyOpsRunIn, raw).seed == seed
    assert _admitted(DailyOpsInput, raw).seed == seed


@pytest.mark.parametrize("seed", [-MAX_ID - 1, MAX_ID + 1, True, 1.0])
def test_daily_seed_rejects_values_outside_its_shared_integer_contract(seed: object) -> None:
    for model in (DailyOpsRunIn, DailyOpsInput):
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(_document(seed=seed)))


@pytest.mark.parametrize("heads", [1, 500])
def test_starting_herd_endpoint_keeps_every_unique_animal(heads: int) -> None:
    animals = [
        {"tag": f"D{index}", "sex": "F", "bucket": "FOUNDATION", "age_months": 12}
        for index in range(heads)
    ]
    raw = json.dumps(_document(animals=animals))
    assert len(_admitted(DailyOpsRunIn, raw).animals) == heads
    assert len(_admitted(DailyOpsInput, raw).animals) == heads


@pytest.mark.parametrize("heads", [0, 501])
def test_empty_or_oversized_starting_herd_is_rejected(heads: int) -> None:
    animals = [
        {"tag": f"D{index}", "sex": "F", "bucket": "FOUNDATION", "age_months": 12}
        for index in range(heads)
    ]
    for model in (DailyOpsRunIn, DailyOpsInput):
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(_document(animals=animals)))


@pytest.mark.parametrize("calendar", ["2000-01-01", "2099-12-31"])
def test_engine_accepts_its_calendar_endpoints(calendar: str) -> None:
    assert (
        _admitted(DailyOpsInput, json.dumps(_document(start_date=calendar))).start_date.isoformat()
        == calendar
    )


@pytest.mark.parametrize("calendar", ["1999-12-31", "2100-01-01"])
def test_engine_rejects_a_calendar_outside_its_documented_range(calendar: str) -> None:
    with pytest.raises(ValidationError):
        DailyOpsInput.model_validate_json(json.dumps(_document(start_date=calendar)))


@pytest.mark.parametrize("age", [0, 240])
def test_starting_animal_age_endpoints_preserve_the_reported_age(age: int) -> None:
    raw = json.dumps(
        _document(animals=[{"tag": "M1", "sex": "M", "bucket": "MALE_KIDS", "age_months": age}])
    )
    assert _admitted(DailyOpsInput, raw).animals[0].age_months == age
    assert _admitted(DailyOpsRunIn, raw).animals[0].age_months == age


@pytest.mark.parametrize("age", [-1, 241])
def test_starting_animal_age_outside_the_shared_240_month_ceiling_is_rejected(age: int) -> None:
    raw = json.dumps(
        _document(animals=[{"tag": "M1", "sex": "M", "bucket": "MALE_KIDS", "age_months": age}])
    )
    for model in (DailyOpsRunIn, DailyOpsInput):
        with pytest.raises(ValidationError):
            model.model_validate_json(raw)
