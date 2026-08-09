"""Stable bulk-health provenance and movement-restriction episodes.

Revision ID: d3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-09 06:00:00.000000+00:00

Existing movement holds are retained. Because the old schema kept only the
latest clearance columns, inferred legacy actions are explicitly labelled as
backfill rather than presented as contemporaneously recorded facts.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preflight_next_due_provenance() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE bad_ids text;
            BEGIN
              SELECT string_agg(id::text, ', ' ORDER BY id)
              INTO bad_ids
              FROM (
                SELECT id
                FROM health_events
                WHERE next_due_date IS NOT NULL
                  AND (
                    schedule_template_name IS NULL
                    OR btrim(schedule_template_name) = ''
                    OR next_due_authority IS NULL
                    OR btrim(next_due_authority) = ''
                  )
                ORDER BY id
                LIMIT 10
              ) invalid;
              IF bad_ids IS NOT NULL THEN
                RAISE EXCEPTION
                  'health event next-due provenance is missing for ids: %', bad_ids;
              END IF;
            END $$;
            """
        )
    )


def _backfill_restriction_history() -> None:
    # A currently active hold plus an earlier recorded clearance necessarily
    # represents at least two episodes. Other active/cleared legacy animals
    # have one known episode; unaffected animals remain at version zero.
    op.execute(
        sa.text(
            """
            UPDATE animals
            SET restriction_version = CASE
              WHEN (movement_restricted IS TRUE OR suspected_scheduled_disease IS TRUE)
                   AND restriction_cleared_at IS NOT NULL THEN 2
              WHEN movement_restricted IS TRUE
                   OR suspected_scheduled_disease IS TRUE
                   OR restriction_cleared_at IS NOT NULL THEN 1
              ELSE 0
            END
            """
        )
    )

    # Episode one is known to have existed for every active or cleared legacy
    # animal, but its original placement time/actor/reference were not stored.
    op.execute(
        sa.text(
            """
            INSERT INTO movement_restriction_actions (
              farm_id, animal_id, restriction_version, action, acted_at,
              acted_by_id, action_reference, disease_target, health_event_id
            )
            SELECT
              farm_id,
              id,
              1,
              'PLACED',
              created_at,
              NULL,
              'Legacy restriction placement backfill',
              left(
                coalesce(
                  nullif(btrim(suspected_disease), ''),
                  nullif(btrim(restriction_reason), ''),
                  'Legacy movement restriction'
                ),
                120
              ),
              NULL
            FROM animals
            WHERE restriction_version >= 1
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO movement_restriction_actions (
              farm_id, animal_id, restriction_version, action, acted_at,
              acted_by_id, action_reference, disease_target, health_event_id
            )
            SELECT
              farm_id,
              id,
              1,
              'CLEARED',
              restriction_cleared_at,
              restriction_cleared_by_id,
              restriction_clearance_reference,
              left(nullif(btrim(suspected_disease), ''), 120),
              NULL
            FROM animals
            WHERE restriction_cleared_at IS NOT NULL
            """
        )
    )

    # If a hold is active after a legacy clearance, preserve it as the current
    # second episode. The old schema had no placement timestamp or actor, so
    # the known clearance instant is the most conservative chronological mark.
    op.execute(
        sa.text(
            """
            INSERT INTO movement_restriction_actions (
              farm_id, animal_id, restriction_version, action, acted_at,
              acted_by_id, action_reference, disease_target, health_event_id
            )
            SELECT
              farm_id,
              id,
              2,
              'PLACED',
              restriction_cleared_at,
              NULL,
              'Legacy post-clearance restriction placement backfill',
              left(
                coalesce(
                  nullif(btrim(suspected_disease), ''),
                  nullif(btrim(restriction_reason), ''),
                  'Legacy movement restriction'
                ),
                120
              ),
              NULL
            FROM animals
            WHERE restriction_version = 2
            """
        )
    )


def upgrade() -> None:
    _preflight_next_due_provenance()

    op.add_column(
        "animals",
        sa.Column(
            "restriction_version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_animals_restriction_version",
        "animals",
        "restriction_version >= 0",
        postgresql_not_valid=True,
    )
    op.execute(sa.text("ALTER TABLE animals VALIDATE CONSTRAINT ck_animals_restriction_version"))

    # Composite tenant references need a matching parent candidate key.
    op.create_unique_constraint(
        "uq_health_events_farm_id_id",
        "health_events",
        ["farm_id", "id"],
    )
    op.create_check_constraint(
        "ck_health_events_next_due_provenance",
        "health_events",
        "next_due_date IS NULL OR "
        "(schedule_template_name IS NOT NULL "
        "AND btrim(schedule_template_name) <> '' "
        "AND next_due_authority IS NOT NULL "
        "AND btrim(next_due_authority) <> '')",
        postgresql_not_valid=True,
    )
    op.execute(
        sa.text(
            "ALTER TABLE health_events VALIDATE CONSTRAINT ck_health_events_next_due_provenance"
        )
    )

    op.create_table(
        "movement_restriction_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("animal_id", sa.Integer(), nullable=False),
        sa.Column("restriction_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("acted_at", sa.DateTime(), nullable=False),
        sa.Column("acted_by_id", sa.Integer(), nullable=True),
        sa.Column("action_reference", sa.String(length=255), nullable=False),
        sa.Column("disease_target", sa.String(length=120), nullable=True),
        sa.Column("health_event_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "action IN ('PLACED', 'CLEARED')",
            name="ck_movement_restriction_actions_action",
        ),
        sa.CheckConstraint(
            "action <> 'PLACED' OR (disease_target IS NOT NULL AND btrim(disease_target) <> '')",
            name="ck_movement_restriction_actions_placement_disease",
        ),
        sa.CheckConstraint(
            "btrim(action_reference) <> ''",
            name="ck_movement_restriction_actions_reference",
        ),
        sa.CheckConstraint(
            "restriction_version >= 1",
            name="ck_movement_restriction_actions_version",
        ),
        sa.ForeignKeyConstraint(["acted_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["animal_id"], ["animals.id"]),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"]),
        sa.ForeignKeyConstraint(["health_event_id"], ["health_events.id"]),
        sa.ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_movement_restriction_actions_farm_animal",
        ),
        sa.ForeignKeyConstraint(
            ["farm_id", "health_event_id"],
            ["health_events.farm_id", "health_events.id"],
            name="fk_movement_restriction_actions_farm_health_event",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "farm_id",
            "animal_id",
            "restriction_version",
            "action",
            name="uq_movement_restriction_action_episode",
        ),
    )
    op.create_index(
        "ix_movement_restriction_actions_acted_by_id",
        "movement_restriction_actions",
        ["acted_by_id"],
    )
    op.create_index(
        "ix_movement_restriction_actions_animal_id",
        "movement_restriction_actions",
        ["animal_id"],
    )
    op.create_index(
        "ix_movement_restriction_actions_animal_version",
        "movement_restriction_actions",
        ["animal_id", "restriction_version"],
    )
    op.create_index(
        "ix_movement_restriction_actions_farm_id",
        "movement_restriction_actions",
        ["farm_id"],
    )

    _backfill_restriction_history()

    # Audit facts are append-only. User account removal is implemented as a
    # tombstone, so the nullable actor FK remains RESTRICT for physical deletes.
    op.execute(
        sa.text(
            """
            CREATE FUNCTION guard_movement_restriction_action_immutability()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              RAISE EXCEPTION 'movement restriction audit actions are immutable';
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_movement_restriction_actions_immutable
            BEFORE UPDATE OR DELETE ON movement_restriction_actions
            FOR EACH ROW
            EXECUTE FUNCTION guard_movement_restriction_action_immutability()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_movement_restriction_actions_immutable "
            "ON movement_restriction_actions"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS guard_movement_restriction_action_immutability"))
    op.drop_index(
        "ix_movement_restriction_actions_farm_id",
        table_name="movement_restriction_actions",
    )
    op.drop_index(
        "ix_movement_restriction_actions_animal_version",
        table_name="movement_restriction_actions",
    )
    op.drop_index(
        "ix_movement_restriction_actions_animal_id",
        table_name="movement_restriction_actions",
    )
    op.drop_index(
        "ix_movement_restriction_actions_acted_by_id",
        table_name="movement_restriction_actions",
    )
    op.drop_table("movement_restriction_actions")
    op.drop_constraint(
        "ck_health_events_next_due_provenance",
        "health_events",
        type_="check",
    )
    op.drop_constraint(
        "uq_health_events_farm_id_id",
        "health_events",
        type_="unique",
    )
    op.drop_constraint(
        "ck_animals_restriction_version",
        "animals",
        type_="check",
    )
    op.drop_column("animals", "restriction_version")
