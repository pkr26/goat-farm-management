"""Metadata/migration parity for the redundant single-column index drops.

The three dropped indexes (2026-09-21 audit, revision b1c3d5e7f9a2) and the
thirteen wave-2 drops (2026-09-28 audit, revision c7d8e9f0a1b2) must stay
dropped on BOTH sides of the schema: absent from the SQLAlchemy metadata a
fresh database is built from, and removed by name — CONCURRENTLY — in the
online migration deployed databases run. A name reappearing on either side
(a regenerated model index, a hand-edited drop list) fails here instead of
silently drifting the deployed schema from the code's.
"""

import re

from app.db import Base

from .conftest import BACKEND_DIR

DROPPED_INDEXES = (
    "ix_transactions_date",
    "ix_health_events_date",
    "ix_tasks_recurring_series_id",
)
MIGRATION_FILE = (
    BACKEND_DIR / "alembic" / "versions" / "b1c3d5e7f9a2_drop_redundant_single_column_indexes.py"
)


def test_dropped_indexes_are_absent_from_the_model_metadata() -> None:
    index_names = {index.name for table in Base.metadata.tables.values() for index in table.indexes}
    for name in DROPPED_INDEXES:
        assert name not in index_names


def test_drop_migration_removes_exactly_the_dropped_set_concurrently() -> None:
    text = MIGRATION_FILE.read_text()
    # The names live in the migration's (index, table, column) drop tuple.
    dropped = set(re.findall(r'\("([a-z0-9_]+)", "[a-z0-9_]+", "[a-z0-9_]+"\)', text))
    # Exactly the three names, no more: an accidental fourth drop would remove
    # an index the composites do not actually cover.
    assert dropped == set(DROPPED_INDEXES)
    # And the drop itself is the online form — a plain DROP INDEX would lock a
    # primary running ingest out of its index.
    assert 'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"' in text


# Wave 2 (2026-09-28 audit, revision c7d8e9f0a1b2): the redundant farm_id /
# animal_id / doe_id singles superseded by full farm- or animal-leading
# composites and candidate keys. farm_memberships' uq_membership_user_farm
# was reviewed and KEPT — it serves the user→farms lookups that carry no
# is_active predicate; do not add it here.
DROPPED_INDEXES_WAVE2 = (
    "ix_transactions_farm_id",
    "ix_insurance_policies_farm_id",
    "ix_insurance_premiums_farm_id",
    "ix_health_events_farm_id",
    "ix_movement_restriction_actions_farm_id",
    "ix_tasks_farm_id",
    "ix_weight_records_animal_id",
    "ix_bucket_moves_animal_id",
    "ix_health_events_animal_id",
    "ix_breeding_records_doe_id",
    "ix_idempotency_records_farm_id",
    "ix_screening_images_farm_id",
    "ix_screening_batches_farm_id",
)
MIGRATION_FILE_WAVE2 = BACKEND_DIR / "alembic" / "versions" / "c7d8e9f0a1b2_index_hygiene_wave2.py"


def test_wave2_dropped_indexes_are_absent_from_the_model_metadata() -> None:
    index_names = {index.name for table in Base.metadata.tables.values() for index in table.indexes}
    for name in DROPPED_INDEXES_WAVE2:
        assert name not in index_names


def test_wave2_drop_migration_removes_exactly_the_dropped_set_concurrently() -> None:
    text = MIGRATION_FILE_WAVE2.read_text()
    dropped = set(re.findall(r'\("([a-z0-9_]+)", "[a-z0-9_]+", "[a-z0-9_]+"\)', text))
    # Exactly the thirteen names, no more.
    assert dropped == set(DROPPED_INDEXES_WAVE2)
    assert 'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"' in text
