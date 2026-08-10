"""Make pregnancy loss and neonatal outcomes auditable and consistent.

Revision ID: b9e1c2d3f4a5
Revises: f7d8c9b0a1e2
Create Date: 2026-08-09 02:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9e1c2d3f4a5"
down_revision: str | Sequence[str] | None = "f7d8c9b0a1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("breeding_records", sa.Column("loss_date", sa.Date(), nullable=True))
    op.add_column("breeding_records", sa.Column("loss_cause", sa.String(length=40), nullable=True))
    op.add_column("breeding_records", sa.Column("loss_notes", sa.Text(), nullable=True))
    op.add_column("breeding_records", sa.Column("loss_recorded_by_id", sa.Integer(), nullable=True))
    op.add_column("breeding_records", sa.Column("loss_recorded_at", sa.DateTime(), nullable=True))
    op.create_foreign_key(
        "fk_breeding_records_loss_recorded_by",
        "breeding_records",
        "users",
        ["loss_recorded_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_breeding_records_loss_date", "breeding_records", ["loss_date"])

    # Older ABORTED rows predate the loss form. Preserve their state while
    # marking the provenance limitation explicitly; never invent a named
    # clinical cause. The latest known pregnancy event is the safest date.
    op.execute(
        sa.text(
            """
            UPDATE breeding_records
            SET loss_date = COALESCE(ultrasound_result_date, breeding_date),
                loss_cause = 'UNKNOWN',
                loss_notes =
                    'Legacy pregnancy-loss row; original date and cause were not captured.',
                loss_recorded_by_id = created_by_id,
                loss_recorded_at = timezone('UTC', now()),
                pregnant = false
            WHERE outcome = 'ABORTED'
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM breeding_records br
                JOIN kidding_records kr ON kr.breeding_record_id = br.id
                WHERE br.outcome = 'ABORTED'
              ) THEN
                RAISE EXCEPTION
                  'ABORTED breeding records with kidding records must be repaired first';
              END IF;
            END $$;
            """
        )
    )

    op.create_check_constraint(
        "ck_breeding_loss_cause",
        "breeding_records",
        "loss_cause IS NULL OR loss_cause IN "
        "('UNKNOWN', 'DISEASE', 'INJURY', 'NUTRITIONAL', 'TRAUMA', "
        "'ANIMAL_STATUS_CHANGE', 'OTHER')",
    )
    op.create_check_constraint(
        "ck_breeding_loss_after_breeding",
        "breeding_records",
        "loss_date IS NULL OR loss_date >= breeding_date",
    )
    op.create_check_constraint(
        "ck_breeding_loss_after_ultrasound",
        "breeding_records",
        "loss_date IS NULL OR ultrasound_result_date IS NULL "
        "OR loss_date >= ultrasound_result_date",
    )
    op.create_check_constraint(
        "ck_breeding_loss_metadata_matches_outcome",
        "breeding_records",
        "(outcome = 'ABORTED' AND pregnant IS FALSE "
        "AND loss_date IS NOT NULL AND loss_cause IS NOT NULL "
        "AND loss_recorded_at IS NOT NULL) OR "
        "(outcome <> 'ABORTED' AND loss_date IS NULL AND loss_cause IS NULL "
        "AND loss_notes IS NULL AND loss_recorded_by_id IS NULL "
        "AND loss_recorded_at IS NULL)",
    )

    # Legacy DIED inputs did not require a date. Use the kidding date as the
    # conservative earliest-known mortality date, then align the linked dead
    # animal. New writes require the explicit date at both schema and DB level.
    op.execute(
        sa.text(
            """
            UPDATE kid_entries ke
            SET mortality_reported_at = kr.date
            FROM kidding_records kr
            WHERE ke.kidding_record_id = kr.id
              AND ke.status = 'DIED'
              AND ke.mortality_reported_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE kid_entries
            SET mortality_reported_at = NULL
            WHERE status <> 'DIED' AND mortality_reported_at IS NOT NULL
            """
        )
    )
    # Align only animals actually recorded DEAD, and never overwrite what the
    # farm already recorded: a DIED birth entry can also belong to an animal
    # that survived the neonatal window and died much later, so the animal's
    # own status_date/mortality_reported_at (the real death date) must win
    # over the kidding-date floor backfilled above. Stamping a non-DEAD animal
    # would additionally violate ck_animals_status_date and
    # ck_animals_mortality_fields when the next revision (c1d2e3f4a5b6)
    # validates them, aborting the release.
    op.execute(
        sa.text(
            """
            UPDATE animals a
            SET status_date =
                    COALESCE(a.status_date, a.mortality_reported_at,
                             ke.mortality_reported_at),
                mortality_reported_at =
                    COALESCE(a.mortality_reported_at, a.status_date,
                             ke.mortality_reported_at)
            FROM kid_entries ke
            WHERE ke.animal_id = a.id
              AND ke.status = 'DIED'
              AND a.status = 'DEAD'
              AND (a.status_date IS NULL OR a.mortality_reported_at IS NULL)
            """
        )
    )
    op.create_check_constraint(
        "ck_kid_entries_status",
        "kid_entries",
        "status IN ('ALIVE', 'STILLBORN', 'DIED')",
    )
    op.create_check_constraint(
        "ck_kid_entries_mortality_matches_status",
        "kid_entries",
        "(status = 'DIED' AND mortality_reported_at IS NOT NULL) OR "
        "(status <> 'DIED' AND mortality_reported_at IS NULL)",
    )

    # Check constraints cannot span tables. These small row triggers close
    # the two remaining holes for imports/direct SQL while taking the same
    # parent-row lock used by the API state machine.
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_reproductive_outcome_integrity()
            RETURNS trigger AS $$
            DECLARE
              parent_outcome text;
            BEGIN
              IF TG_TABLE_NAME = 'kidding_records' THEN
                IF NEW.breeding_record_id IS NOT NULL THEN
                  SELECT outcome INTO parent_outcome
                  FROM breeding_records
                  WHERE id = NEW.breeding_record_id
                  FOR UPDATE;
                  IF parent_outcome = 'ABORTED' THEN
                    RAISE EXCEPTION USING
                      ERRCODE = '23514',
                      MESSAGE = 'an aborted pregnancy cannot have a kidding record';
                  END IF;
                END IF;
                IF TG_OP = 'UPDATE' AND NEW.date IS DISTINCT FROM OLD.date
                   AND EXISTS (
                     SELECT 1 FROM kid_entries
                     WHERE kidding_record_id = NEW.id
                       AND mortality_reported_at < NEW.date
                   ) THEN
                  RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'kidding date cannot follow a recorded kid mortality';
                END IF;
                RETURN NEW;
              END IF;

              IF TG_OP = 'UPDATE' AND OLD.outcome = 'ABORTED' AND (
                   NEW.outcome IS DISTINCT FROM OLD.outcome
                OR NEW.loss_date IS DISTINCT FROM OLD.loss_date
                OR NEW.loss_cause IS DISTINCT FROM OLD.loss_cause
                OR NEW.loss_notes IS DISTINCT FROM OLD.loss_notes
                OR NEW.loss_recorded_at IS DISTINCT FROM OLD.loss_recorded_at
                OR (
                  NEW.loss_recorded_by_id IS DISTINCT FROM OLD.loss_recorded_by_id
                  AND NEW.loss_recorded_by_id IS NOT NULL
                )
              ) THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23514',
                  MESSAGE = 'pregnancy-loss audit metadata is immutable';
              END IF;
              IF NEW.outcome = 'ABORTED' AND EXISTS (
                SELECT 1 FROM kidding_records
                WHERE breeding_record_id = NEW.id
              ) THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23514',
                  MESSAGE = 'a pregnancy with a kidding record cannot be aborted';
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_breeding_reproductive_outcome
            BEFORE UPDATE ON breeding_records
            FOR EACH ROW EXECUTE FUNCTION enforce_reproductive_outcome_integrity()
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_kidding_reproductive_outcome
            BEFORE INSERT OR UPDATE OF breeding_record_id, date ON kidding_records
            FOR EACH ROW EXECUTE FUNCTION enforce_reproductive_outcome_integrity()
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_kid_mortality_chronology()
            RETURNS trigger AS $$
            DECLARE
              delivered_on date;
            BEGIN
              IF NEW.mortality_reported_at IS NOT NULL THEN
                SELECT date INTO delivered_on
                FROM kidding_records
                WHERE id = NEW.kidding_record_id
                FOR KEY SHARE;
                IF delivered_on IS NOT NULL AND NEW.mortality_reported_at < delivered_on THEN
                  RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'kid mortality cannot predate kidding';
                END IF;
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_kid_mortality_chronology
            BEFORE INSERT OR UPDATE OF kidding_record_id, mortality_reported_at
            ON kid_entries
            FOR EACH ROW EXECUTE FUNCTION enforce_kid_mortality_chronology()
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_kid_mortality_chronology ON kid_entries")
    op.execute("DROP FUNCTION IF EXISTS enforce_kid_mortality_chronology()")
    op.execute("DROP TRIGGER IF EXISTS trg_kidding_reproductive_outcome ON kidding_records")
    op.execute("DROP TRIGGER IF EXISTS trg_breeding_reproductive_outcome ON breeding_records")
    op.execute("DROP FUNCTION IF EXISTS enforce_reproductive_outcome_integrity()")
    op.drop_constraint("ck_kid_entries_mortality_matches_status", "kid_entries", type_="check")
    op.drop_constraint("ck_kid_entries_status", "kid_entries", type_="check")
    op.drop_constraint(
        "ck_breeding_loss_metadata_matches_outcome", "breeding_records", type_="check"
    )
    op.drop_constraint("ck_breeding_loss_after_ultrasound", "breeding_records", type_="check")
    op.drop_constraint("ck_breeding_loss_after_breeding", "breeding_records", type_="check")
    op.drop_constraint("ck_breeding_loss_cause", "breeding_records", type_="check")
    op.drop_index("ix_breeding_records_loss_date", table_name="breeding_records")
    op.drop_constraint(
        "fk_breeding_records_loss_recorded_by", "breeding_records", type_="foreignkey"
    )
    for column in (
        "loss_recorded_at",
        "loss_recorded_by_id",
        "loss_notes",
        "loss_cause",
        "loss_date",
    ):
        op.drop_column("breeding_records", column)
