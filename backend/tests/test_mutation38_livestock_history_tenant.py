"""ORM history tenant derivation without opening a database connection.

The unit session contains an existing detached animal in its identity map.
Local fixture overrides keep this event oracle runnable before any seed flush.
"""

from datetime import date

import pytest
from sqlalchemy.orm import Session, make_transient_to_detached

from app.models import Animal, BucketMove, Role, WeightRecord
from app.models.animals import _derive_history_farm_id


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """These identity-map probes do not need the PostgreSQL schema."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """No database writes or table cleanup occur in this module."""


def test_history_tenant_derivation_resolves_both_histories_and_ignores_other_rows() -> None:
    animal = Animal(id=123, farm_id=71, tag_number="CACHED-HISTORY-ANIMAL")
    make_transient_to_detached(animal)
    weight = WeightRecord(animal_id=123, farm_id=None, date=date(2026, 10, 5), weight_kg=26.0)
    move = BucketMove(animal_id=123, farm_id=None, to_bucket="FOUNDATION")
    # A role may be pending while its new farm's identity is still unassigned.
    unrelated = Role(farm_id=None, name="Pending farm role", permissions=[])
    with Session(autoflush=False) as session:
        session.add(animal)
        session.add_all([unrelated, weight, move])
        try:
            _derive_history_farm_id(session, None, None)
        except Exception as exc:
            pytest.fail(f"Valid pending ORM rows must derive their history tenant: {exc!r}")
        assert weight.farm_id == 71
        assert move.farm_id == 71
        assert unrelated.farm_id is None


def test_history_tenant_derivation_preserves_explicit_tenants_and_unresolved_identity() -> None:
    animal = Animal(id=124, farm_id=72, tag_number="CACHED-EXPLICIT-ANIMAL")
    make_transient_to_detached(animal)
    weight = WeightRecord(animal_id=124, farm_id=72, date=date(2026, 10, 5), weight_kg=26.0)
    move = BucketMove(animal_id=124, farm_id=72, to_bucket="FOUNDATION")
    unresolved_weight = WeightRecord(animal_id=None, farm_id=None, weight_kg=2.5)
    unresolved_move = BucketMove(animal_id=None, farm_id=None, to_bucket="RECOVERY")
    with Session(autoflush=False) as session:
        session.add(animal)
        session.add_all([weight, move, unresolved_weight, unresolved_move])
        try:
            _derive_history_farm_id(session, None, None)
        except Exception as exc:
            pytest.fail(f"A history without an animal identity must stay unresolved: {exc!r}")
        assert weight.farm_id == 72
        assert move.farm_id == 72
        assert unresolved_weight.farm_id is None
        assert unresolved_move.farm_id is None
