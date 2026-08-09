"""Enforce farm ownership across tenant-scoped relationships.

Revision ID: f7d8c9b0a1e2
Revises: e2c4f6a8b0d1
Create Date: 2026-08-09 00:30:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7d8c9b0a1e2"
down_revision: str | Sequence[str] | None = "e2c4f6a8b0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TENANT_FOREIGN_KEYS: tuple[
    tuple[str, str, str, tuple[str, ...], tuple[str, ...], str | None], ...
] = (
    # These supplement rather than replace the existing single-column FKs.
    # In particular, the dam/sire composites intentionally have no ON DELETE
    # action: the original single-column SET NULL constraints clear the animal
    # id, after which MATCH SIMPLE makes the composite constraint satisfied.
    (
        "fk_farm_memberships_farm_role",
        "farm_memberships",
        "roles",
        ("farm_id", "role_id"),
        ("farm_id", "id"),
        "RESTRICT",
    ),
    (
        "fk_animals_farm_purchase_batch",
        "animals",
        "purchase_batches",
        ("farm_id", "purchase_batch_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_animals_farm_dam",
        "animals",
        "animals",
        ("farm_id", "dam_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_animals_farm_sire",
        "animals",
        "animals",
        ("farm_id", "sire_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_health_events_farm_animal",
        "health_events",
        "animals",
        ("farm_id", "animal_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_health_events_farm_purchase_batch",
        "health_events",
        "purchase_batches",
        ("farm_id", "purchase_batch_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_breeding_records_farm_doe",
        "breeding_records",
        "animals",
        ("farm_id", "doe_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_breeding_records_farm_buck",
        "breeding_records",
        "animals",
        ("farm_id", "buck_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_kidding_records_farm_doe",
        "kidding_records",
        "animals",
        ("farm_id", "doe_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_kidding_records_farm_breeding_record",
        "kidding_records",
        "breeding_records",
        ("farm_id", "breeding_record_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_tasks_farm_animal",
        "tasks",
        "animals",
        ("farm_id", "animal_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_tasks_farm_purchase_batch",
        "tasks",
        "purchase_batches",
        ("farm_id", "purchase_batch_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_tasks_farm_breeding_record",
        "tasks",
        "breeding_records",
        ("farm_id", "breeding_record_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_tasks_farm_assigned_role",
        "tasks",
        "roles",
        ("farm_id", "assigned_role_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_tasks_farm_assigned_membership",
        "tasks",
        "farm_memberships",
        ("farm_id", "assigned_user_id"),
        ("farm_id", "user_id"),
        None,
    ),
    (
        "fk_transactions_farm_related_animal",
        "transactions",
        "animals",
        ("farm_id", "related_animal_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_transactions_farm_correction",
        "transactions",
        "transactions",
        ("farm_id", "correction_of_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_kid_entries_farm_kidding_record",
        "kid_entries",
        "kidding_records",
        ("farm_id", "kidding_record_id"),
        ("farm_id", "id"),
        None,
    ),
    (
        "fk_kid_entries_farm_animal",
        "kid_entries",
        "animals",
        ("farm_id", "animal_id"),
        ("farm_id", "id"),
        None,
    ),
)


def _fail_on_legacy_cross_tenant_rows() -> None:
    """Abort the migration before DDL if any current relationship is unsafe."""
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM farm_memberships child
                JOIN roles parent ON parent.id = child.role_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm farm_memberships.role_id rows exist'; END IF;

              IF EXISTS (
                SELECT 1 FROM animals child
                JOIN purchase_batches parent ON parent.id = child.purchase_batch_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm animals.purchase_batch_id rows exist'; END IF;
              IF EXISTS (
                SELECT 1 FROM animals child JOIN animals parent ON parent.id = child.dam_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm animals.dam_id rows exist'; END IF;
              IF EXISTS (
                SELECT 1 FROM animals child JOIN animals parent ON parent.id = child.sire_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm animals.sire_id rows exist'; END IF;

              IF EXISTS (
                SELECT 1 FROM health_events child JOIN animals parent ON parent.id = child.animal_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm health_events.animal_id rows exist'; END IF;
              IF EXISTS (
                SELECT 1 FROM health_events child
                JOIN purchase_batches parent ON parent.id = child.purchase_batch_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN
                RAISE EXCEPTION 'cross-farm health_events.purchase_batch_id rows exist';
              END IF;

              IF EXISTS (
                SELECT 1 FROM breeding_records child JOIN animals parent ON parent.id = child.doe_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm breeding_records.doe_id rows exist'; END IF;
              IF EXISTS (
                SELECT 1 FROM breeding_records child
                JOIN animals parent ON parent.id = child.buck_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm breeding_records.buck_id rows exist'; END IF;

              IF EXISTS (
                SELECT 1 FROM kidding_records child JOIN animals parent ON parent.id = child.doe_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm kidding_records.doe_id rows exist'; END IF;
              IF EXISTS (
                SELECT 1 FROM kidding_records child
                JOIN breeding_records parent ON parent.id = child.breeding_record_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN
                RAISE EXCEPTION 'cross-farm kidding_records.breeding_record_id rows exist';
              END IF;

              IF EXISTS (
                SELECT 1 FROM tasks child JOIN animals parent ON parent.id = child.animal_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm tasks.animal_id rows exist'; END IF;
              IF EXISTS (
                SELECT 1 FROM tasks child
                JOIN purchase_batches parent ON parent.id = child.purchase_batch_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm tasks.purchase_batch_id rows exist'; END IF;
              IF EXISTS (
                SELECT 1 FROM tasks child
                JOIN breeding_records parent ON parent.id = child.breeding_record_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm tasks.breeding_record_id rows exist'; END IF;
              IF EXISTS (
                SELECT 1 FROM tasks child JOIN roles parent ON parent.id = child.assigned_role_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm tasks.assigned_role_id rows exist'; END IF;
              IF EXISTS (
                SELECT 1 FROM tasks child
                LEFT JOIN farm_memberships parent
                  ON parent.farm_id = child.farm_id
                 AND parent.user_id = child.assigned_user_id
                WHERE child.assigned_user_id IS NOT NULL AND parent.id IS NULL
              ) THEN
                RAISE EXCEPTION 'tasks.assigned_user_id without same-farm membership exists';
              END IF;

              IF EXISTS (
                SELECT 1 FROM transactions child
                JOIN animals parent ON parent.id = child.related_animal_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm transactions.related_animal_id rows exist'; END IF;
              IF EXISTS (
                SELECT 1 FROM transactions child
                JOIN transactions parent ON parent.id = child.correction_of_id
                WHERE child.farm_id <> parent.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm transactions.correction_of_id rows exist'; END IF;

              IF EXISTS (
                SELECT 1 FROM kid_entries child
                JOIN kidding_records birth ON birth.id = child.kidding_record_id
                JOIN animals animal ON animal.id = child.animal_id
                WHERE birth.farm_id <> animal.farm_id
              ) THEN RAISE EXCEPTION 'cross-farm kid_entries.animal_id rows exist'; END IF;
            END $$;
            """
        )
    )


def _create_and_validate_fk(
    name: str,
    source: str,
    referent: str,
    local_columns: tuple[str, ...],
    remote_columns: tuple[str, ...],
    ondelete: str | None,
) -> None:
    # NOT VALID keeps the initial metadata lock short; explicit validation is
    # still fail-closed and runs inside this migration's transaction.
    op.create_foreign_key(
        name,
        source,
        referent,
        list(local_columns),
        list(remote_columns),
        ondelete=ondelete,
        postgresql_not_valid=True,
    )
    op.execute(sa.text(f'ALTER TABLE "{source}" VALIDATE CONSTRAINT "{name}"'))


def upgrade() -> None:
    _fail_on_legacy_cross_tenant_rows()

    op.create_unique_constraint("uq_roles_farm_id_id", "roles", ["farm_id", "id"])
    op.create_unique_constraint(
        "uq_farm_memberships_farm_user", "farm_memberships", ["farm_id", "user_id"]
    )
    op.create_unique_constraint(
        "uq_purchase_batches_farm_id_id", "purchase_batches", ["farm_id", "id"]
    )
    op.create_unique_constraint("uq_animals_farm_id_id", "animals", ["farm_id", "id"])
    op.create_unique_constraint(
        "uq_breeding_records_farm_id_id", "breeding_records", ["farm_id", "id"]
    )
    op.create_unique_constraint(
        "uq_kidding_records_farm_id_id", "kidding_records", ["farm_id", "id"]
    )
    op.create_unique_constraint("uq_transactions_farm_id_id", "transactions", ["farm_id", "id"])

    op.add_column("kid_entries", sa.Column("farm_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE kid_entries child SET farm_id = parent.farm_id "
        "FROM kidding_records parent WHERE parent.id = child.kidding_record_id"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM kid_entries WHERE farm_id IS NULL) "
        "THEN RAISE EXCEPTION 'kid_entries farm backfill failed'; END IF; END $$;"
    )
    op.alter_column("kid_entries", "farm_id", nullable=False)
    op.create_foreign_key(
        "kid_entries_farm_id_fkey",
        "kid_entries",
        "farms",
        ["farm_id"],
        ["id"],
    )
    op.create_index("ix_kid_entries_farm_id", "kid_entries", ["farm_id"], unique=False)

    for args in TENANT_FOREIGN_KEYS:
        _create_and_validate_fk(*args)


def downgrade() -> None:
    for name, source, _referent, _local, _remote, _ondelete in reversed(TENANT_FOREIGN_KEYS):
        op.drop_constraint(name, source, type_="foreignkey")

    op.drop_index("ix_kid_entries_farm_id", table_name="kid_entries")
    op.drop_constraint("kid_entries_farm_id_fkey", "kid_entries", type_="foreignkey")
    op.drop_column("kid_entries", "farm_id")

    op.drop_constraint("uq_transactions_farm_id_id", "transactions", type_="unique")
    op.drop_constraint("uq_kidding_records_farm_id_id", "kidding_records", type_="unique")
    op.drop_constraint("uq_breeding_records_farm_id_id", "breeding_records", type_="unique")
    op.drop_constraint("uq_animals_farm_id_id", "animals", type_="unique")
    op.drop_constraint("uq_purchase_batches_farm_id_id", "purchase_batches", type_="unique")
    op.drop_constraint("uq_farm_memberships_farm_user", "farm_memberships", type_="unique")
    op.drop_constraint("uq_roles_farm_id_id", "roles", type_="unique")
