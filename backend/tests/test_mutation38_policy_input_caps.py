"""Supported schema boundaries preserve the documented procurement/task caps."""

from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.animals import AnimalCreateIn
from app.schemas.purchases import PurchaseBatchIn
from app.schemas.tasks import TaskCreateIn


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """These input-only contracts perform no database I/O."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """These input-only contracts create no persistent objects."""


@pytest.mark.parametrize(("field", "value"), [("count", 1000), ("avg_age_months", 240.0)])
def test_documented_maximum_purchase_inputs_remain_valid(field: str, value: float) -> None:
    try:
        payload = PurchaseBatchIn.model_validate(
            {"date": date(2026, 10, 5), "count": 1, field: value}
        )
    except ValidationError as exc:
        pytest.fail(f"The supported procurement boundary must validate: {exc}")
    assert payload.model_dump()[field] == value


@pytest.mark.parametrize(("field", "value"), [("title", "T" * 200), ("recur_days", 3650)])
def test_documented_maximum_manual_task_inputs_remain_valid(field: str, value: str | int) -> None:
    try:
        payload = TaskCreateIn.model_validate(
            {"title": "Manual duty", "due_date": date(2026, 10, 5), field: value}
        )
    except ValidationError as exc:
        pytest.fail(f"The supported manual-task boundary must validate: {exc}")
    assert payload.model_dump()[field] == value


def test_documented_fifty_character_animal_tag_remains_valid() -> None:
    tag = "T" * 50
    try:
        payload = AnimalCreateIn.model_validate(
            {
                "tag_number": tag,
                "sex": "F",
                "source": "PURCHASED",
                "current_bucket": "FOUNDATION",
                "historical_import_reason": "Audited existing-herd import",
            }
        )
    except ValidationError as exc:
        pytest.fail(f"The supported animal-tag boundary must validate: {exc}")
    assert payload.tag_number == tag
