"""Transport schemas must reject misspelled client fields, never ignore them."""

import json
from datetime import date

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.schemas.animals import AnimalCreateIn, MoveIn, StatusChangeIn, WeightIn
from app.schemas.auth import AccountDeleteIn, ChangePasswordIn, FarmCreateIn, LoginIn, RegisterIn
from app.schemas.breeding import BreedingCreateIn, UltrasoundIn
from app.schemas.common import PostgresText
from app.schemas.feeding import DispenseIn, FeedSettingIn, MixIn, StockAddIn
from app.schemas.finance import TransactionCorrectionIn, TransactionIn
from app.schemas.health import HealthEventIn, MovementRestrictionClearIn
from app.schemas.kidding import KiddingCreateIn, KidIn
from app.schemas.purchases import PurchaseBatchIn
from app.schemas.screening import ScreeningBatchCreateIn
from app.schemas.simulation import RunIn, ScenarioCreateIn, ScenarioUpdateIn
from app.schemas.tasks import TaskCreateIn, TaskRejectIn, TaskSkipIn
from app.schemas.team import (
    NotificationPrefsIn,
    PasswordResetIn,
    RoleChangeIn,
    RoleIn,
    WorkerCreateIn,
)
from app.simulation.assumptions import SimulationAssumptions

REQUEST_MODELS: tuple[type[BaseModel], ...] = (
    RegisterIn,
    LoginIn,
    AccountDeleteIn,
    ChangePasswordIn,
    FarmCreateIn,
    WorkerCreateIn,
    RoleChangeIn,
    PasswordResetIn,
    RoleIn,
    AnimalCreateIn,
    WeightIn,
    MoveIn,
    StatusChangeIn,
    BreedingCreateIn,
    UltrasoundIn,
    MovementRestrictionClearIn,
    HealthEventIn,
    KidIn,
    KiddingCreateIn,
    PurchaseBatchIn,
    DispenseIn,
    MixIn,
    StockAddIn,
    FeedSettingIn,
    TransactionIn,
    TransactionCorrectionIn,
    TaskCreateIn,
    TaskRejectIn,
    TaskSkipIn,
    ScenarioCreateIn,
    ScenarioUpdateIn,
    RunIn,
    ScreeningBatchCreateIn,
)


@pytest.mark.parametrize("model", REQUEST_MODELS, ids=lambda model: model.__name__)
def test_every_request_model_forbids_unknown_fields(model: type[BaseModel]) -> None:
    assert model.model_config.get("extra") == "forbid"


def test_unknown_nested_kid_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        KiddingCreateIn.model_validate(
            {
                "breeding_record_id": 1,
                "date": date.today().isoformat(),
                "kids": [{"sex": "F", "birth_weigth": 2.5}],
            }
        )


def test_unknown_health_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        HealthEventIn.model_validate(
            {
                "scope": "animal",
                "animal_id": 1,
                "type": "TREATMENT",
                "withdrawl_until": date.today().isoformat(),
            }
        )


def test_screening_batch_create_rejects_any_field() -> None:
    """The bodyless batch-intake fingerprint accepts exactly zero fields —
    with none declared, extra="forbid" turns any supplied key into the same
    422-class validation error every other request model gives."""
    assert ScreeningBatchCreateIn.model_validate({}) == ScreeningBatchCreateIn()
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ScreeningBatchCreateIn.model_validate({"batch_id": 1})


@pytest.mark.parametrize("bad_id", [True, False, "1", 1.0])
def test_bounded_ids_do_not_coerce_json_types(bad_id: object) -> None:
    with pytest.raises(ValidationError):
        WorkerCreateIn.model_validate(
            {
                "email": "worker@example.com",
                "password": "long-enough-password",
                "role_id": bad_id,
            }
        )


@pytest.mark.parametrize("bad_number", [True, False, "12.5"])
def test_domain_numbers_do_not_coerce_json_types(bad_number: object) -> None:
    with pytest.raises(ValidationError):
        TransactionIn.model_validate(
            {
                "date": date.today().isoformat(),
                "type": "EXPENSE",
                "category": "OTHER",
                "amount": bad_number,
            }
        )
    with pytest.raises(ValidationError):
        WeightIn.model_validate({"weight_kg": bad_number})


def test_strict_numeric_types_still_accept_real_json_numbers_and_ids() -> None:
    worker = WorkerCreateIn.model_validate(
        {
            "email": "worker@example.com",
            "password": "long-enough-password",
            "role_id": 1,
        }
    )
    transaction = TransactionIn.model_validate(
        {
            "date": date.today().isoformat(),
            "type": "EXPENSE",
            "category": "OTHER",
            "amount": 12,
        }
    )
    weight = WeightIn.model_validate({"weight_kg": 12})
    assert worker.role_id == 1
    assert transaction.amount == 12.0
    assert weight.weight_kg == 12.0


@pytest.mark.parametrize("bad_count", [True, "1", 1.0])
def test_integer_counts_do_not_coerce_json_types(bad_count: object) -> None:
    with pytest.raises(ValidationError):
        PurchaseBatchIn.model_validate({"date": date.today().isoformat(), "count": bad_count})
    with pytest.raises(ValidationError):
        TaskCreateIn.model_validate(
            {
                "title": "Daily check",
                "due_date": date.today().isoformat(),
                "recur_days": bad_count,
            }
        )


@pytest.mark.parametrize("bad_bool", [0, 1, "true", "false"])
def test_boolean_flags_do_not_coerce_json_types(bad_bool: object) -> None:
    with pytest.raises(ValidationError):
        PurchaseBatchIn.model_validate(
            {
                "date": date.today().isoformat(),
                "count": 1,
                "create_animals": bad_bool,
            }
        )
    with pytest.raises(ValidationError):
        MoveIn.model_validate({"to_bucket": "FOUNDATION", "history_override": bad_bool})
    with pytest.raises(ValidationError):
        RunIn.model_validate(
            {
                "assumptions": SimulationAssumptions().model_dump(),
                "monte_carlo": bad_bool,
            }
        )


@pytest.mark.parametrize("bad_bool", [0, 1, "true", "false"])
def test_kidding_care_booleans_do_not_coerce_json_types(bad_bool: object) -> None:
    """Kidding care facts used plain bool and admitted 1/"true" payloads that
    every sibling strict endpoint 422s (2026-10-01 audit, 04-3)."""
    with pytest.raises(ValidationError):
        KidIn.model_validate({"sex": "F", "dam_rejected": bad_bool})
    with pytest.raises(ValidationError):
        KidIn.model_validate({"sex": "F", "colostrum_within_2h": bad_bool})
    with pytest.raises(ValidationError):
        KidIn.model_validate({"sex": "F", "navel_dipped": bad_bool})
    with pytest.raises(ValidationError):
        KiddingCreateIn.model_validate(
            {
                "breeding_record_id": 1,
                "date": date.today().isoformat(),
                "placenta_passed": bad_bool,
                "kids": [{"sex": "F"}],
            }
        )
    with pytest.raises(ValidationError):
        KiddingCreateIn.model_validate(
            {
                "breeding_record_id": 1,
                "date": date.today().isoformat(),
                "mastitis_suspected": bad_bool,
                "kids": [{"sex": "F"}],
            }
        )


@pytest.mark.parametrize("bad_bool", [0, 1, "true", "false"])
def test_notification_opt_in_booleans_do_not_coerce_json_types(bad_bool: object) -> None:
    """NotificationPrefsIn opt-ins used plain bool and admitted 1/"true"
    payloads (2026-10-01 audit, 04-3)."""
    for field in (
        "daily_digest",
        "screening_flags",
        "kidding_watch",
        "overdue_critical",
        "feed_reorder",
        "movement_restriction",
        "verified",
    ):
        with pytest.raises(ValidationError):
            NotificationPrefsIn.model_validate({"phone": "+919888877777", field: bad_bool})


def test_care_and_opt_in_booleans_still_accept_real_booleans() -> None:
    """The strictness added for 2026-10-01 audit 04-3 tightens coercion only:
    genuine JSON booleans (and the explicit None for "not recorded") still
    validate exactly as before."""
    kid = KidIn.model_validate(
        {"sex": "F", "colostrum_within_2h": True, "navel_dipped": False, "dam_rejected": False}
    )
    assert (kid.colostrum_within_2h, kid.navel_dipped, kid.dam_rejected) == (True, False, False)
    unrecorded = KidIn.model_validate({"sex": "M"})
    assert unrecorded.colostrum_within_2h is None
    assert unrecorded.dam_rejected is False
    kidding = KiddingCreateIn.model_validate(
        {
            "breeding_record_id": 1,
            "date": date.today().isoformat(),
            "placenta_passed": None,
            "mastitis_suspected": True,
            "kids": [{"sex": "F", "dam_rejected": True}],
        }
    )
    assert kidding.placenta_passed is None
    assert kidding.mastitis_suspected is True
    assert kidding.kids[0].dam_rejected is True
    prefs = NotificationPrefsIn.model_validate(
        {"phone": "+919888877777", "daily_digest": True, "verified": True}
    )
    assert prefs.daily_digest is True
    assert prefs.verified is True
    assert prefs.kidding_watch is False


@pytest.mark.parametrize("bad_number", [True, "50"])
def test_nested_simulation_assumptions_do_not_coerce_numbers(bad_number: object) -> None:
    assumptions = SimulationAssumptions().model_dump()
    assumptions["herd"]["does"] = bad_number
    with pytest.raises(ValidationError):
        RunIn.model_validate({"assumptions": assumptions})


def test_postgres_text_rejects_lone_surrogates_that_cannot_reach_postgres() -> None:
    """A lone UTF-16 surrogate clears the control-character rule but explodes
    in asyncpg's text codec (``str.encode('utf-8')``). That is not a DBAPI
    error, so SQLAlchemy never wraps it and no router catches it — it reached
    the catch-all handler as the opaque 500 this validator exists to prevent.
    """
    adapter = TypeAdapter(PostgresText)

    # This is exactly what a client body containing "\ud800" decodes to.
    lone_surrogate = json.loads('"A\\ud800"')
    assert lone_surrogate == "A\ud800"
    with pytest.raises(ValidationError, match="surrogate"):
        adapter.validate_python(lone_surrogate)

    for hostile in ("\ud800", "\udfff", "tail\udc00", "\ud83d"):  # incl. a split emoji pair
        with pytest.raises(ValidationError):
            adapter.validate_python(hostile)

    # Ordinary text — including astral characters and the allowed whitespace —
    # is untouched.
    for benign in ("Ravi's herd", "गोट", "🐐", "line\nbreak\ttab\r", "A"):
        assert adapter.validate_python(benign) == benign
        assert adapter.validate_python(benign).encode("utf-8")
