"""Public team and daily-operations admission boundaries.

Exercise JSON requests rather than inspecting Pydantic field metadata: accepted
edge values must remain usable, and the next value outside a documented limit
must fail before it reaches persistence or an expensive simulation.
"""

import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.main import create_app
from app.schemas.ops_simulation import DailyOpsRunIn
from app.schemas.team import (
    MembershipOut,
    NotificationPrefsIn,
    PasswordResetIn,
    RoleIn,
    RoleUpdateIn,
    WorkerCreateIn,
    WorkerPinResetIn,
)
from app.services.idempotency import _revalidate_cached_response


def _accepted(model: type[BaseModel], document: dict[str, Any]) -> bool:
    try:
        model.model_validate_json(json.dumps(document))
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize("model", [WorkerCreateIn, WorkerPinResetIn], ids=["new-worker", "reset"])
@pytest.mark.parametrize(
    ("pin", "accepted"),
    [
        ("123", False),
        ("0123", True),
        ("012345678901", True),
        ("0123456789012", False),
        ("12 34", False),
        ("+1234", False),
        ("１２３４", False),
        (1234, False),
    ],
)
def test_tablet_pin_json_is_ascii_digits_with_supported_lengths(
    model: type[BaseModel], pin: object, accepted: bool
) -> None:
    payload: dict[str, Any] = {"pin": pin}
    if model is WorkerCreateIn:
        payload.update(email="tablet@farm.in", role_id=1)
    assert _accepted(model, payload) is accepted


@pytest.mark.parametrize(("length", "accepted"), [(120, True), (121, False)])
def test_worker_display_name_fits_the_stored_profile(length: int, accepted: bool) -> None:
    assert (
        _accepted(WorkerCreateIn, {"email": "worker@farm.in", "role_id": 1, "name": "N" * length})
        is accepted
    )


@pytest.mark.parametrize(("length", "accepted"), [(0, False), (1, True), (128, True), (129, False)])
def test_owner_password_reset_preserves_exact_password_length_boundary(
    length: int, accepted: bool
) -> None:
    assert _accepted(PasswordResetIn, {"password": "p" * length}) is accepted


@pytest.mark.parametrize(("length", "accepted"), [(128, True), (129, False)])
def test_worker_provisioning_rejects_an_oversized_password(length: int, accepted: bool) -> None:
    assert (
        _accepted(
            WorkerCreateIn,
            {"email": "worker@farm.in", "role_id": 1, "password": "p" * length},
        )
        is accepted
    )


@pytest.mark.parametrize("schema", ["WorkerCreateIn", "PasswordResetIn"])
def test_published_worker_password_budget_matches_the_ascii_admission_limit(schema: str) -> None:
    """Client-generated forms need the same bound as the password byte guard."""
    wire = create_app().openapi()["components"]["schemas"][schema]["properties"]["password"]
    variants = wire.get("anyOf", [wire])
    strings = [variant for variant in variants if variant.get("type") == "string"]
    assert len(strings) == 1
    assert strings[0]["maxLength"] == 128


def test_cached_worker_response_without_pin_flag_does_not_advertise_tablet_login() -> None:
    legacy = {
        "id": 1,
        "user_id": 2,
        "email": "worker@farm.in",
        "name": "Worker",
        "role_id": 3,
        "role_name": "Herd reader",
        "is_active": True,
        "can_reset_password": True,
        "reset_password_block_reason": None,
    }
    replay = _revalidate_cached_response(MembershipOut, legacy)
    assert replay.pin_set is False
    assert replay.model_dump()["pin_set"] is False


@pytest.mark.parametrize(
    ("phone", "accepted"),
    [
        ("123456789", False),
        ("1234567890", True),
        ("1" * 19, True),
        ("+" + "1" * 19, True),
        ("1" * 20, False),
        ("+" + "1" * 20, False),
        ("++1234567890", False),
        ("12345 67890", False),
    ],
)
def test_notification_phone_json_has_a_bounded_unambiguous_number(
    phone: str, accepted: bool
) -> None:
    assert _accepted(NotificationPrefsIn, {"phone": phone}) is accepted


def test_all_worker_notifications_require_an_explicit_opt_in() -> None:
    preferences = NotificationPrefsIn.model_validate_json('{"phone":"1234567890"}')
    assert preferences.model_dump() == {
        "phone": "1234567890",
        "daily_digest": False,
        "screening_flags": False,
        "kidding_watch": False,
        "overdue_critical": False,
        "feed_reorder": False,
        "movement_restriction": False,
        "verified": False,
    }


@pytest.mark.parametrize(
    "field",
    [
        "daily_digest",
        "screening_flags",
        "kidding_watch",
        "overdue_critical",
        "feed_reorder",
        "movement_restriction",
        "verified",
    ],
)
def test_one_notification_opt_in_does_not_enable_other_channels(field: str) -> None:
    preferences = NotificationPrefsIn.model_validate_json(
        json.dumps({"phone": "1234567890", field: True})
    ).model_dump()
    assert preferences.pop("phone") == "1234567890"
    assert preferences.pop(field) is True
    assert all(value is False for value in preferences.values())


@pytest.mark.parametrize(("length", "accepted"), [(0, False), (1, True), (80, True), (81, False)])
def test_role_name_json_matches_the_team_editor_storage_limit(length: int, accepted: bool) -> None:
    assert _accepted(RoleIn, {"name": "R" * length}) is accepted


@pytest.mark.parametrize(("length", "accepted"), [(255, True), (256, False)])
def test_role_description_json_is_bounded(length: int, accepted: bool) -> None:
    assert _accepted(RoleIn, {"name": "Herd reader", "description": "D" * length}) is accepted


@pytest.mark.parametrize(("count", "accepted"), [(100, True), (101, False)])
def test_role_permission_document_cannot_grow_without_bound(count: int, accepted: bool) -> None:
    assert (
        _accepted(RoleIn, {"name": "Herd reader", "permissions": ["animals.read"] * count})
        is accepted
    )


@pytest.mark.parametrize(
    ("revision", "accepted"), [(0, False), (1, True), (True, False), ("1", False)]
)
def test_role_revision_token_is_a_positive_json_integer(revision: object, accepted: bool) -> None:
    assert (
        _accepted(RoleUpdateIn, {"name": "Herd reader", "expected_revision": revision}) is accepted
    )


def test_legacy_role_editor_gets_only_the_first_revision_token() -> None:
    assert RoleUpdateIn.model_validate_json('{"name":"Herd reader"}').expected_revision == 1


def _daily_document(**changes: object) -> dict[str, Any]:
    return {
        "start_date": "2026-11-15",
        "animals": [{"tag": "DOE-1", "sex": "F", "bucket": "FOUNDATION", "age_months": 24}],
        **changes,
    }


@pytest.mark.parametrize(
    ("days", "accepted"),
    [(6, False), (7, True), (365, True), (366, False), (True, False), ("7", False)],
)
def test_daily_operations_horizon_is_a_bounded_json_integer(days: object, accepted: bool) -> None:
    assert _accepted(DailyOpsRunIn, _daily_document(horizon_days=days)) is accepted


@pytest.mark.parametrize(
    ("seed", "accepted"),
    [
        (-(2**62) - 1, False),
        (-(2**62), True),
        (2**62, True),
        (2**62 + 1, False),
        (True, False),
        ("2026", False),
    ],
)
def test_daily_operations_seed_is_bounded_before_it_is_echoed(seed: object, accepted: bool) -> None:
    assert _accepted(DailyOpsRunIn, _daily_document(seed=seed)) is accepted


@pytest.mark.parametrize(("count", "accepted"), [(0, False), (1, True), (500, True), (501, False)])
def test_daily_operations_initial_herd_has_a_finite_wire_budget(count: int, accepted: bool) -> None:
    animals = [
        {"tag": f"DOE-{index}", "sex": "F", "bucket": "FOUNDATION", "age_months": 24}
        for index in range(count)
    ]
    assert _accepted(DailyOpsRunIn, _daily_document(animals=animals)) is accepted


def test_daily_operations_default_request_is_bounded_and_ledger_is_opt_in() -> None:
    document = DailyOpsRunIn.model_validate_json(json.dumps(_daily_document()))
    assert document.horizon_days == 90
    assert document.seed == 2026
    assert document.include_ledger is False
