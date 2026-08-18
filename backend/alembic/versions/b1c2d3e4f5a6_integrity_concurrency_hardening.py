"""close lifecycle date gaps and add optimistic-concurrency audit guards

Revision ID: b1c2d3e4f5a6
Revises: e6f8a0b2c4d7
Create Date: 2026-08-17 00:00:00.000000+00:00

The application already had most of these policies at its transport boundary,
but an omitted health-event date and stale full-document editors bypassed them.
This revision makes the durable database schema agree with the write paths and
refuses to silently rewrite historical clinical/accounting facts.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "e6f8a0b2c4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_WITHDRAWAL_DAYS = 730
MAX_GESTATION_DAYS = 200


def _preflight_legacy_rows() -> None:
    """Fail closed with small deterministic samples; never invent corrections."""
    op.execute(
        sa.text(
            f"""
            DO $integrity_preflight$
            DECLARE
                bad_ids text;
            BEGIN
                SELECT string_agg(id::text, ', ' ORDER BY id)
                INTO bad_ids
                FROM (
                    SELECT id
                    FROM health_events
                    WHERE withdrawal_until IS NOT NULL
                      AND withdrawal_until > date + {MAX_WITHDRAWAL_DAYS}
                    ORDER BY id
                    LIMIT 20
                ) AS invalid;
                IF bad_ids IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot install withdrawal ceiling: health_events ids % '
                        'exceed % days. Health events are immutable; '
                        'reconcile each record explicitly before retrying.',
                        bad_ids, {MAX_WITHDRAWAL_DAYS};
                END IF;

                SELECT string_agg(id::text, ', ' ORDER BY id)
                INTO bad_ids
                FROM (
                    SELECT id
                    FROM breeding_records
                    WHERE ultrasound_result_date IS NOT NULL
                      AND ultrasound_result_date > breeding_date + {MAX_GESTATION_DAYS}
                    ORDER BY id
                    LIMIT 20
                ) AS invalid;
                IF bad_ids IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot install ultrasound gestation ceiling: '
                        'breeding_records ids % exceed % days. '
                        'Retain/reconcile historical facts explicitly before retrying.',
                        bad_ids, {MAX_GESTATION_DAYS};
                END IF;

                SELECT string_agg(id::text, ', ' ORDER BY id)
                INTO bad_ids
                FROM (
                    SELECT id
                    FROM breeding_records
                    WHERE loss_date IS NOT NULL
                      AND loss_date > breeding_date + {MAX_GESTATION_DAYS}
                      AND loss_cause IS DISTINCT FROM 'ANIMAL_STATUS_CHANGE'
                    ORDER BY id
                    LIMIT 20
                ) AS invalid;
                IF bad_ids IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot install pregnancy-loss gestation ceiling: '
                        'breeding_records ids % exceed % days. Only retained '
                        'ANIMAL_STATUS_CHANGE administrative closures are exempt; '
                        'reconcile the rest explicitly.',
                        bad_ids, {MAX_GESTATION_DAYS};
                END IF;

                SELECT string_agg(id::text, ', ' ORDER BY id)
                INTO bad_ids
                FROM (
                    SELECT action.id
                    FROM movement_restriction_actions AS action
                    JOIN health_events AS event ON event.id = action.health_event_id
                    WHERE action.health_event_id IS NOT NULL
                      AND (
                        event.farm_id IS DISTINCT FROM action.farm_id
                        OR event.animal_id IS DISTINCT FROM action.animal_id
                      )
                    ORDER BY action.id
                    LIMIT 20
                ) AS invalid;
                IF bad_ids IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Cannot install movement-restriction health-event provenance guard: '
                        'action ids % cite a different animal/event. '
                        'Reconcile them explicitly before retrying.',
                        bad_ids;
                END IF;
            END
            $integrity_preflight$
            """
        )
    )


def _create_restriction_health_event_guard() -> None:
    """A composite farm FK alone cannot prove the cited event is this animal's."""
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_movement_restriction_action_health_event_provenance()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              IF NEW.health_event_id IS NOT NULL
                 AND NOT EXISTS (
                   SELECT 1
                   FROM health_events event
                   WHERE event.id = NEW.health_event_id
                     AND event.farm_id = NEW.farm_id
                     AND event.animal_id = NEW.animal_id
                 ) THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23503',
                  CONSTRAINT = 'fk_movement_restriction_actions_health_event_animal',
                  MESSAGE = 'movement restriction action health event must belong to the same '
                            'animal and farm';
              END IF;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_movement_restriction_action_health_event_provenance
            BEFORE INSERT OR UPDATE OF farm_id, animal_id, health_event_id
            ON movement_restriction_actions
            FOR EACH ROW
            EXECUTE FUNCTION enforce_movement_restriction_action_health_event_provenance()
            """
        )
    )


def upgrade() -> None:
    _preflight_legacy_rows()

    op.create_check_constraint(
        "ck_health_events_withdrawal_within_max",
        "health_events",
        f"withdrawal_until IS NULL OR withdrawal_until <= date + {MAX_WITHDRAWAL_DAYS}",
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE health_events VALIDATE CONSTRAINT ck_health_events_withdrawal_within_max"
    )
    op.create_check_constraint(
        "ck_breeding_records_result_within_max_gestation",
        "breeding_records",
        "ultrasound_result_date IS NULL OR "
        f"ultrasound_result_date <= breeding_date + {MAX_GESTATION_DAYS}",
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE breeding_records VALIDATE CONSTRAINT "
        "ck_breeding_records_result_within_max_gestation"
    )
    # A terminal status can administratively close a stale confirmed pregnancy
    # after the biological window; public pregnancy-loss input cannot select
    # that internal cause.
    op.create_check_constraint(
        "ck_breeding_loss_within_max_gestation",
        "breeding_records",
        "loss_date IS NULL OR loss_cause = 'ANIMAL_STATUS_CHANGE' OR "
        f"loss_date <= breeding_date + {MAX_GESTATION_DAYS}",
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE breeding_records VALIDATE CONSTRAINT "
        "ck_breeding_loss_within_max_gestation"
    )

    op.add_column(
        "roles",
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        "ck_roles_revision_positive",
        "roles",
        "revision >= 1",
        postgresql_not_valid=True,
    )
    op.execute("ALTER TABLE roles VALIDATE CONSTRAINT ck_roles_revision_positive")
    op.add_column(
        "simulation_scenarios",
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        "ck_simulation_scenarios_revision_positive",
        "simulation_scenarios",
        "revision >= 1",
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE simulation_scenarios VALIDATE CONSTRAINT "
        "ck_simulation_scenarios_revision_positive"
    )

    _create_restriction_health_event_guard()


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS "
            "trg_movement_restriction_action_health_event_provenance "
            "ON movement_restriction_actions"
        )
    )
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS "
            "enforce_movement_restriction_action_health_event_provenance()"
        )
    )

    op.drop_constraint(
        "ck_simulation_scenarios_revision_positive",
        "simulation_scenarios",
        type_="check",
    )
    op.drop_column("simulation_scenarios", "revision")
    op.drop_constraint("ck_roles_revision_positive", "roles", type_="check")
    op.drop_column("roles", "revision")

    op.drop_constraint(
        "ck_breeding_loss_within_max_gestation",
        "breeding_records",
        type_="check",
    )
    op.drop_constraint(
        "ck_breeding_records_result_within_max_gestation",
        "breeding_records",
        type_="check",
    )
    op.drop_constraint(
        "ck_health_events_withdrawal_within_max",
        "health_events",
        type_="check",
    )
