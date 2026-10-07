"""Daily input documents preserve supported animal states and operational policy admission."""

import json

import pytest
from pydantic import BaseModel, ValidationError

from app.models.constants import BUCK_DOE_RATIO, MEAT_SALE_AGE_MONTHS
from app.models.species import GOAT_PROFILE
from app.schemas.common import MAX_ID
from app.schemas.ops_simulation import DailyOpsRunIn
from app.simulation.daily_ops import AnimalStartSpec, DailyOpsInput, DailyOpsParams


def _animal(**updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "tag": "D1",
        "sex": "F",
        "bucket": "FOUNDATION",
        "age_months": 12,
    }
    result.update(updates)
    return result


def _document(animal: dict[str, object]) -> dict[str, object]:
    return {"start_date": "2026-01-01", "animals": [animal]}


def _accept[T: BaseModel](model: type[T], document: object) -> T:
    try:
        return model.model_validate_json(json.dumps(document))
    except (ValidationError, ValueError, TypeError, ArithmeticError) as exc:
        pytest.fail(f"A supported daily operations input must be admitted intact: {exc}")


@pytest.mark.parametrize("length", [1, 50])
def test_animal_tag_supported_endpoints_are_preserved(length: int) -> None:
    document = _animal(tag="D" * length)
    assert _accept(AnimalStartSpec, document).tag == document["tag"]


@pytest.mark.parametrize("length", [0, 51])
def test_blank_or_oversized_animal_tag_is_rejected(length: int) -> None:
    with pytest.raises(ValidationError):
        AnimalStartSpec.model_validate_json(json.dumps(_animal(tag="D" * length)))


@pytest.mark.parametrize("days", [0, 3650])
def test_bucket_residence_supported_endpoints_are_preserved(days: int) -> None:
    document = _animal(days_in_bucket=days)
    assert _accept(AnimalStartSpec, document).days_in_bucket == days


@pytest.mark.parametrize("days", [-1, 3651])
def test_bucket_residence_outside_the_supported_history_is_rejected(days: int) -> None:
    with pytest.raises(ValidationError):
        AnimalStartSpec.model_validate_json(json.dumps(_animal(days_in_bucket=days)))


def test_omitted_animal_history_is_new_residence_and_not_a_dependent_kid() -> None:
    animal = _accept(AnimalStartSpec, _animal())
    assert animal.days_in_bucket == 0
    assert animal.bred_days_ago is None
    assert animal.dependent_kid is False


@pytest.mark.parametrize(
    "sex,bucket,dependent",
    [
        ("F", "FOUNDATION", False),
        ("M", "FOUNDATION", False),
        ("F", "BREEDING", False),
        ("M", "BREEDING", False),
        ("F", "FEMALE_KIDS", False),
        ("M", "MALE_KIDS", False),
        ("F", "RESTING", False),
        ("F", "RECOVERY", False),
        ("F", "RECOVERY", True),
        ("M", "RECOVERY", True),
    ],
)
def test_coherent_animal_sex_and_bucket_state_is_admitted(
    sex: str, bucket: str, dependent: bool
) -> None:
    input_ = _accept(
        DailyOpsInput,
        _document(_animal(sex=sex, bucket=bucket, dependent_kid=dependent)),
    )
    assert input_.animals[0].sex == sex
    assert input_.animals[0].bucket == bucket
    assert input_.animals[0].dependent_kid is dependent


@pytest.mark.parametrize(
    "sex,bucket,dependent",
    [
        ("F", "MALE_KIDS", False),
        ("M", "FEMALE_KIDS", False),
        ("M", "RESTING", False),
        ("M", "RECOVERY", False),
        ("F", "FOUNDATION", True),
        ("M", "MALE_KIDS", True),
    ],
)
def test_incoherent_animal_sex_or_dependent_state_is_rejected(
    sex: str, bucket: str, dependent: bool
) -> None:
    with pytest.raises(ValidationError):
        DailyOpsInput.model_validate_json(
            json.dumps(_document(_animal(sex=sex, bucket=bucket, dependent_kid=dependent)))
        )


@pytest.mark.parametrize(
    "bucket,service_day",
    [
        ("BREEDING", 0),
        ("BREEDING", GOAT_PROFILE.pregnancy_check_after_service_days - 1),
        ("PREGNANCY_EARLY", GOAT_PROFILE.pregnancy_check_after_service_days),
        ("PREGNANCY_EARLY", GOAT_PROFILE.pregnancy_late_day - 1),
        ("PREGNANCY_LATE", GOAT_PROFILE.pregnancy_late_day),
        (
            "PREGNANCY_LATE",
            GOAT_PROFILE.gestation_days - GOAT_PROFILE.prepartum_move_lead_days - 1,
        ),
        (
            "DELIVERY",
            GOAT_PROFILE.gestation_days - GOAT_PROFILE.prepartum_move_lead_days,
        ),
        ("DELIVERY", GOAT_PROFILE.gestation_days - 1),
    ],
)
def test_real_species_service_phase_endpoints_are_admitted(bucket: str, service_day: int) -> None:
    input_ = _accept(DailyOpsInput, _document(_animal(bucket=bucket, bred_days_ago=service_day)))
    assert input_.animals[0].bred_days_ago == service_day
    assert input_.animals[0].bucket == bucket


@pytest.mark.parametrize(
    "bucket,service_day",
    [
        ("BREEDING", GOAT_PROFILE.pregnancy_check_after_service_days),
        ("PREGNANCY_EARLY", GOAT_PROFILE.pregnancy_check_after_service_days - 1),
        ("PREGNANCY_EARLY", GOAT_PROFILE.pregnancy_late_day),
        ("PREGNANCY_LATE", GOAT_PROFILE.pregnancy_late_day - 1),
        (
            "PREGNANCY_LATE",
            GOAT_PROFILE.gestation_days - GOAT_PROFILE.prepartum_move_lead_days,
        ),
        (
            "DELIVERY",
            GOAT_PROFILE.gestation_days - GOAT_PROFILE.prepartum_move_lead_days - 1,
        ),
        ("DELIVERY", GOAT_PROFILE.gestation_days),
        ("FOUNDATION", 0),
        ("RESTING", 0),
    ],
)
def test_service_day_outside_its_actual_bucket_phase_is_rejected(
    bucket: str, service_day: int
) -> None:
    with pytest.raises(ValidationError):
        DailyOpsInput.model_validate_json(
            json.dumps(_document(_animal(bucket=bucket, bred_days_ago=service_day)))
        )


@pytest.mark.parametrize("bucket", ["PREGNANCY_EARLY", "PREGNANCY_LATE", "DELIVERY"])
def test_pregnant_buckets_require_real_service_history(bucket: str) -> None:
    with pytest.raises(ValidationError):
        DailyOpsInput.model_validate_json(json.dumps(_document(_animal(bucket=bucket))))


def test_service_history_belongs_only_to_a_female_animal() -> None:
    with pytest.raises(ValidationError):
        DailyOpsInput.model_validate_json(
            json.dumps(_document(_animal(sex="M", bucket="BREEDING", bred_days_ago=0)))
        )


def test_last_quarantine_day_before_protocol_release_is_admitted() -> None:
    input_ = _accept(DailyOpsInput, _document(_animal(bucket="QUARANTINE", days_in_bucket=44)))
    assert input_.animals[0].days_in_bucket == 44


def test_quarantine_residence_at_the_released_day_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DailyOpsInput.model_validate_json(
            json.dumps(_document(_animal(bucket="QUARANTINE", days_in_bucket=45)))
        )


def test_unique_animal_tags_allow_distinct_journeys_and_duplicates_reject() -> None:
    document: dict[str, object] = {
        "start_date": "2026-01-01",
        "animals": [_animal(tag="D1"), _animal(tag="D2")],
    }
    assert [a.tag for a in _accept(DailyOpsInput, document).animals] == ["D1", "D2"]
    document["animals"] = [_animal(), _animal()]
    with pytest.raises(ValidationError):
        DailyOpsInput.model_validate_json(json.dumps(document))


_POLICY_BOUNDS = [
    ("failed_services_before_cull", 1, 6),
    ("max_doe_age_months", 12, 240),
    ("male_sale_age_months", 1, 36),
    ("buck_doe_ratio", 1, 100),
]


@pytest.mark.parametrize("field,low,high", _POLICY_BOUNDS)
def test_operational_policy_integer_endpoints_are_preserved(
    field: str, low: int, high: int
) -> None:
    for endpoint in (low, high):
        admitted = _accept(DailyOpsParams, {field: endpoint})
        assert getattr(admitted, field) == endpoint


@pytest.mark.parametrize("field,low,high", _POLICY_BOUNDS)
def test_operational_policy_outside_the_supported_range_is_rejected(
    field: str, low: int, high: int
) -> None:
    for outside in (low - 1, high + 1):
        with pytest.raises(ValidationError):
            DailyOpsParams.model_validate_json(json.dumps({field: outside}))


def test_omitted_operational_policy_matches_live_species_and_meat_sale_rules() -> None:
    policy = _accept(DailyOpsParams, {})
    assert policy.failed_services_before_cull == GOAT_PROFILE.failed_services_before_cull
    assert policy.max_doe_age_months == 72
    assert policy.male_sale_age_months == MEAT_SALE_AGE_MONTHS[0]
    assert policy.buck_doe_ratio == BUCK_DOE_RATIO


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_animal_and_policy_integers_reject_json_coercion(value: object) -> None:
    for model, document in (
        (AnimalStartSpec, _animal(age_months=value)),
        (DailyOpsParams, {"buck_doe_ratio": value}),
        (DailyOpsInput, {**_document(_animal()), "seed": value}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(document))


def _window_document(**updates: object) -> dict[str, object]:
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
    raw = json.dumps(_window_document(horizon_days=days))
    assert _admitted(DailyOpsRunIn, raw).horizon_days == days
    assert _admitted(DailyOpsInput, raw).horizon_days == days


@pytest.mark.parametrize("days", [6, 366])
def test_run_and_engine_reject_days_outside_the_supported_daily_window(days: int) -> None:
    for model in (DailyOpsRunIn, DailyOpsInput):
        with pytest.raises(ValidationError) as rejected:
            model.model_validate_json(json.dumps(_window_document(horizon_days=days)))
        assert any(error["loc"] == ("horizon_days",) for error in rejected.value.errors())


def test_omitted_daily_window_and_seed_match_the_operator_form() -> None:
    raw = json.dumps(_window_document())
    for model in (DailyOpsRunIn, DailyOpsInput):
        admitted = _admitted(model, raw)
        assert admitted.horizon_days == 90
        assert admitted.seed == 2026


@pytest.mark.parametrize("seed", [-MAX_ID, MAX_ID])
def test_seed_integer_endpoints_are_echoable_without_coercion(seed: int) -> None:
    raw = json.dumps(_window_document(seed=seed))
    assert _admitted(DailyOpsRunIn, raw).seed == seed
    assert _admitted(DailyOpsInput, raw).seed == seed


@pytest.mark.parametrize("seed", [-MAX_ID - 1, MAX_ID + 1, True, 1.0])
def test_daily_seed_rejects_values_outside_its_shared_integer_contract(seed: object) -> None:
    for model in (DailyOpsRunIn, DailyOpsInput):
        with pytest.raises(ValidationError):
            model.model_validate_json(json.dumps(_window_document(seed=seed)))


@pytest.mark.parametrize("heads", [1, 500])
def test_starting_herd_endpoint_keeps_every_unique_animal(heads: int) -> None:
    animals = [
        {"tag": f"D{index}", "sex": "F", "bucket": "FOUNDATION", "age_months": 12}
        for index in range(heads)
    ]
    raw = json.dumps(_window_document(animals=animals))
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
            model.model_validate_json(json.dumps(_window_document(animals=animals)))


@pytest.mark.parametrize("calendar", ["2000-01-01", "2099-12-31"])
def test_engine_accepts_its_calendar_endpoints(calendar: str) -> None:
    assert (
        _admitted(
            DailyOpsInput, json.dumps(_window_document(start_date=calendar))
        ).start_date.isoformat()
        == calendar
    )


@pytest.mark.parametrize("calendar", ["1999-12-31", "2100-01-01"])
def test_engine_rejects_a_calendar_outside_its_window_documented_range(calendar: str) -> None:
    with pytest.raises(ValidationError):
        DailyOpsInput.model_validate_json(json.dumps(_window_document(start_date=calendar)))


@pytest.mark.parametrize("age", [0, 240])
def test_starting_animal_age_endpoints_preserve_the_reported_age(age: int) -> None:
    raw = json.dumps(
        _window_document(
            animals=[{"tag": "M1", "sex": "M", "bucket": "MALE_KIDS", "age_months": age}]
        )
    )
    assert _admitted(DailyOpsInput, raw).animals[0].age_months == age
    assert _admitted(DailyOpsRunIn, raw).animals[0].age_months == age


@pytest.mark.parametrize("age", [-1, 241])
def test_starting_animal_age_outside_the_shared_240_month_ceiling_is_rejected(age: int) -> None:
    raw = json.dumps(
        _window_document(
            animals=[{"tag": "M1", "sex": "M", "bucket": "MALE_KIDS", "age_months": age}]
        )
    )
    for model in (DailyOpsRunIn, DailyOpsInput):
        with pytest.raises(ValidationError):
            model.model_validate_json(raw)
