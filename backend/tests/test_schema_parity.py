"""Schema/model parity guards.

The Pydantic ``Literal`` aliases in ``app/schemas/`` re-declare the string
enums in ``app/models.py`` (Pydantic v2 can't derive a JSON-schema enum from
a str-Enum Literal without losing the wire names), and the input sanity caps
are shared constants. A new bucket/category/status added to models but not
mirrored here fails these tests instead of silently rejecting API input.
"""

from typing import get_args

from app import models
from app.permissions import ROLE_PRESET_CODES, ROLE_PRESETS
from app.schemas import animals, finance, health, kidding, purchases, tasks


def _literal_values(alias: object) -> set[str]:
    return set(get_args(alias))  # type: ignore[arg-type]


def test_bucket_str_matches_bucket_enum() -> None:
    assert _literal_values(animals.BucketStr) == {b.value for b in models.Bucket}


def test_task_category_str_matches_enum() -> None:
    assert _literal_values(tasks.TaskCategoryStr) == {c.value for c in models.TaskCategory}


def test_health_event_type_str_matches_enum() -> None:
    assert _literal_values(health.HealthEventTypeStr) == {t.value for t in models.HealthEventType}


def test_transaction_category_str_matches_enum() -> None:
    assert _literal_values(finance.TransactionCategoryStr) == {
        c.value for c in models.TransactionCategory
    }


def test_kid_status_and_ease_strs_match_enums() -> None:
    assert _literal_values(kidding.KidStatusStr) == {s.value for s in models.KidStatus}
    assert _literal_values(kidding.KiddingEaseStr) == {e.value for e in models.KiddingEase}


def test_sanity_caps_are_shared_from_models() -> None:
    assert purchases.MAX_BATCH_COUNT is models.MAX_BATCH_COUNT
    assert purchases.MAX_AGE_MONTHS is models.MAX_AGE_MONTHS
    assert tasks.MAX_RECUR_DAYS is models.MAX_RECUR_DAYS
    # services must import the same objects, not re-define them.
    from app import services

    assert services.MAX_BATCH_COUNT is models.MAX_BATCH_COUNT
    assert services.MAX_AGE_MONTHS is models.MAX_AGE_MONTHS
    assert services.MAX_RECUR_DAYS is models.MAX_RECUR_DAYS


def test_preset_role_code_catalog_matches_seed_definitions() -> None:
    assert ROLE_PRESET_CODES == {preset["code"] for preset in ROLE_PRESETS}
