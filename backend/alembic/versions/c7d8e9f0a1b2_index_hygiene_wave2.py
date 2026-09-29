"""Drop redundant single-column indexes + subsumed birth-weight CHECK

Redundant-index hygiene, wave 2 (2026-09-28 audit). Thirteen single-column
indexes duplicate FULL farm/animal-leading composites (or candidate keys)
that serve every query the application actually runs — verified against the
model's index map and the routers' query patterns:

* ix_transactions_farm_id — uq_transactions_farm_id_id (farm_id, id) and
  ix_transactions_farm_date_id both lead with farm_id. The 2026-09-21
  review kept this single for FK-enforcement/maintenance scans; the
  candidate key serves those too.
* ix_insurance_policies_farm_id — ix_insurance_policies_farm_status_renewal
  and uq_insurance_policies_farm_id_id lead with farm_id.
* ix_insurance_premiums_farm_id — ix_insurance_premiums_farm_policy,
  ix_insurance_premiums_farm_recorded_on and the farm_id candidate key.
* ix_health_events_farm_id — ix_health_events_farm_date_id and
  uq_health_events_farm_id_id.
* ix_movement_restriction_actions_farm_id —
  uq_movement_restriction_action_episode (farm_id, animal_id,
  restriction_version, action).
* ix_tasks_farm_id — ix_tasks_farm_status_due and
  uq_task_recurring_series_due.
* ix_weight_records_animal_id — ix_weight_records_animal_date_id_desc leads
  with animal_id.
* ix_bucket_moves_animal_id — ix_bucket_moves_animal_moved_id_desc leads
  with animal_id.
* ix_health_events_animal_id — ix_health_events_animal_template_latest
  leads with animal_id.
* ix_breeding_records_doe_id — ix_breeding_records_doe_date_id leads with
  doe_id.
* ix_idempotency_records_farm_id — uq_idempotency_scope_key (farm_id,
  actor_id, operation, key_digest) serves the farm-delete cascade probe.
* ix_screening_images_farm_id — ix_screening_images_farm_status_created and
  uq_screening_images_farm_id_id lead with farm_id.
* ix_screening_batches_farm_id — ix_screening_batches_farm_created and
  uq_screening_batches_farm_id_id lead with farm_id.

NOT dropped, contra the audit's candidate list: farm_memberships'
uq_membership_user_farm — the (farm_id, user_id) twin is the composite-FK
target, but the (user_id, farm_id) order is the only index serving the
user→farms affiliation lookups that carry no is_active predicate
(api/auth.py's membership list, api/team.py's owns-other-farm probe); the
partial ix_farm_memberships_active_user_id_id cannot serve those.

Also dropped: ck_animals_birth_weight_nonneg — subsumed by
ck_animals_birth_weight_bounded (which implies >= 0 alongside the 1000 kg
ceiling and the non-finite guard), a duplicate evaluation on every write.

Index drops run CONCURRENTLY (IF EXISTS) so a primary running ingest is
never locked out of its index; the CHECK drop is metadata-only.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index, table, column) — the column is only needed to rebuild on downgrade.
_DROPS: tuple[tuple[str, str, str], ...] = (
    ("ix_transactions_farm_id", "transactions", "farm_id"),
    ("ix_insurance_policies_farm_id", "insurance_policies", "farm_id"),
    ("ix_insurance_premiums_farm_id", "insurance_premiums", "farm_id"),
    ("ix_health_events_farm_id", "health_events", "farm_id"),
    ("ix_movement_restriction_actions_farm_id", "movement_restriction_actions", "farm_id"),
    ("ix_tasks_farm_id", "tasks", "farm_id"),
    ("ix_weight_records_animal_id", "weight_records", "animal_id"),
    ("ix_bucket_moves_animal_id", "bucket_moves", "animal_id"),
    ("ix_health_events_animal_id", "health_events", "animal_id"),
    ("ix_breeding_records_doe_id", "breeding_records", "doe_id"),
    ("ix_idempotency_records_farm_id", "idempotency_records", "farm_id"),
    ("ix_screening_images_farm_id", "screening_images", "farm_id"),
    ("ix_screening_batches_farm_id", "screening_batches", "farm_id"),
)

_SUBSUMED_CHECK = "ck_animals_birth_weight_nonneg"
_SUBSUMED_CHECK_SQL = "birth_weight IS NULL OR birth_weight >= 0"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for index_name, _table_name, _column in _DROPS:
            op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"')
    op.drop_constraint(_SUBSUMED_CHECK, "animals", type_="check")


def downgrade() -> None:
    # NOT VALID -> VALIDATE (house idiom): the metadata lock stays brief and
    # validation is fail-closed inside this transaction.
    op.create_check_constraint(
        _SUBSUMED_CHECK,
        "animals",
        _SUBSUMED_CHECK_SQL,
        postgresql_not_valid=True,
    )
    op.execute(f'ALTER TABLE "animals" VALIDATE CONSTRAINT "{_SUBSUMED_CHECK}"')
    with op.get_context().autocommit_block():
        for index_name, table_name, column in _DROPS:
            op.execute(
                f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{index_name}" '
                f'ON "{table_name}" ("{column}")'
            )
