"""repair lifecycle dates, reproductive links, schedule links and task state

Revision ID: e3f4a5b6c7d9
Revises: d1c2b3a4e5f6
Create Date: 2026-08-09 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3f4a5b6c7d9"
down_revision: str | Sequence[str] | None = "d1c2b3a4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _repair_schedule_links() -> None:
    """Undo unsafe inferred substring links, then relink unique word matches."""
    op.execute(
        "DROP TRIGGER IF EXISTS trg_health_event_schedule_template_id_immutable ON health_events"
    )
    op.execute(
        sa.text(
            r"""
            WITH text_facts AS (
              SELECT
                event.id,
                event.schedule_template_id,
                template.name AS template_name,
                ' ' || btrim(regexp_replace(lower(concat(
                  coalesce(event.product_name, ''), ' ', coalesce(event.disease_target, '')
                )), '[^a-z0-9]+', ' ', 'g')) || ' ' AS words,
                btrim(regexp_replace(
                  lower(split_part(template.name, '(', 1)), '[^a-z0-9]+', ' ', 'g'
                )) AS template_words,
                lower(substring(template.name from '\(([^)]+)\)')) AS abbreviation
              FROM health_events event
              JOIN vaccine_templates template ON template.id = event.schedule_template_id
              WHERE event.type = 'VACCINE'
                AND (event.schedule_template_name IS NULL
                     OR btrim(event.schedule_template_name) = '')
            ), unsafe AS (
              SELECT id
              FROM text_facts fact
              WHERE NOT (
                position(' ' || fact.template_words || ' ' in fact.words) > 0
                OR (fact.abbreviation IS NOT NULL
                    AND position(' ' || fact.abbreviation || ' ' in fact.words) > 0)
                OR (fact.template_name = 'FMD' AND (
                    position(' fmd ' in fact.words) > 0
                    OR position(' foot and mouth ' in fact.words) > 0
                    OR position(' raksha triovac ' in fact.words) > 0
                    OR position(' triovac ' in fact.words) > 0
                    OR position(' raksha ovac ' in fact.words) > 0))
                OR (fact.template_name = 'PPR' AND (
                    position(' ppr ' in fact.words) > 0
                    OR position(' peste des petits ' in fact.words) > 0
                    OR position(' raksha ppr ' in fact.words) > 0))
                OR (fact.template_name = 'Goat Pox' AND (
                    position(' goat pox ' in fact.words) > 0
                    OR position(' goatpox ' in fact.words) > 0
                    OR position(' raksha gp ' in fact.words) > 0))
                OR (fact.template_name = 'Enterotoxaemia (ET)' AND (
                    position(' enterotoxaemia ' in fact.words) > 0
                    OR position(' enterotoxemia ' in fact.words) > 0
                    OR position(' entero ' in fact.words) > 0
                    OR position(' raksha et ' in fact.words) > 0))
                OR (fact.template_name = 'Haemorrhagic Septicaemia (HS)' AND (
                    position(' haemorrhagic septicaemia ' in fact.words) > 0
                    OR position(' hemorrhagic ' in fact.words) > 0
                    OR position(' raksha hs ' in fact.words) > 0))
              )
            )
            UPDATE health_events event
            SET schedule_template_id = NULL
            FROM unsafe
            WHERE event.id = unsafe.id
            """
        )
    )
    op.execute(
        sa.text(
            r"""
            WITH eligible AS (
              SELECT
                event.id,
                event.type,
                ' ' || btrim(regexp_replace(lower(concat(
                  coalesce(event.product_name, ''), ' ', coalesce(event.disease_target, '')
                )), '[^a-z0-9]+', ' ', 'g')) || ' ' AS words
              FROM health_events event
              WHERE event.schedule_template_id IS NULL
                AND (event.schedule_template_name IS NULL
                     OR btrim(event.schedule_template_name) = '')
                AND event.type IN ('VACCINE', 'DEWORMING')
            ), candidates AS (
              SELECT event.id AS event_id, template.id AS template_id
              FROM eligible event
              CROSS JOIN vaccine_templates template
              WHERE
                (event.type = 'DEWORMING' AND template.name = 'Deworming')
                OR (
                  event.type = 'VACCINE'
                  AND template.name <> 'Deworming'
                  AND (template.first_dose_age_months IS NOT NULL
                       OR template.repeat_months IS NOT NULL)
                  AND (
                    position(
                      ' ' || btrim(regexp_replace(lower(split_part(template.name, '(', 1)),
                        '[^a-z0-9]+', ' ', 'g')) || ' '
                      in event.words
                    ) > 0
                    OR (
                      substring(template.name from '\(([^)]+)\)') IS NOT NULL
                      AND position(
                        ' ' || lower(substring(template.name from '\(([^)]+)\)')) || ' '
                        in event.words
                      ) > 0
                    )
                    OR (template.name = 'FMD' AND (
                      position(' foot and mouth ' in event.words) > 0
                      OR position(' raksha triovac ' in event.words) > 0
                      OR position(' triovac ' in event.words) > 0
                      OR position(' raksha ovac ' in event.words) > 0))
                    OR (template.name = 'PPR' AND (
                      position(' peste des petits ' in event.words) > 0
                      OR position(' raksha ppr ' in event.words) > 0))
                    OR (template.name = 'Goat Pox' AND (
                      position(' goatpox ' in event.words) > 0
                      OR position(' raksha gp ' in event.words) > 0))
                    OR (template.name = 'Enterotoxaemia (ET)' AND (
                      position(' enterotoxemia ' in event.words) > 0
                      OR position(' entero ' in event.words) > 0
                      OR position(' raksha et ' in event.words) > 0))
                    OR (template.name = 'Haemorrhagic Septicaemia (HS)' AND (
                      position(' hemorrhagic ' in event.words) > 0
                      OR position(' raksha hs ' in event.words) > 0))
                  )
                )
            ), unique_candidates AS (
              SELECT event_id, min(template_id) AS template_id
              FROM candidates
              GROUP BY event_id
              HAVING count(*) = 1
            )
            UPDATE health_events event
            SET schedule_template_id = candidate.template_id
            FROM unique_candidates candidate
            WHERE event.id = candidate.event_id
            """
        )
    )
    op.execute(
        """
        CREATE TRIGGER trg_health_event_schedule_template_id_immutable
        BEFORE UPDATE OF schedule_template_id ON health_events
        FOR EACH ROW EXECUTE FUNCTION guard_health_event_schedule_template_id()
        """
    )


def _replace_reproductive_trigger(*, strict: bool) -> None:
    strict_guard = (
        """
                  -- Let the named tenant FKs report a missing/cross-farm
                  -- parent or doe. Once both references are valid inside the
                  -- row's farm, this trigger enforces the stronger
                  -- reproductive relationship between them.
                  IF FOUND
                     AND parent_farm_id IS NOT DISTINCT FROM NEW.farm_id
                     AND EXISTS (
                       SELECT 1 FROM animals doe
                       WHERE doe.id = NEW.doe_id AND doe.farm_id = NEW.farm_id
                     )
                     AND (
                          parent_outcome IS DISTINCT FROM 'CONFIRMED_PREGNANT'
                       OR parent_doe_id IS DISTINCT FROM NEW.doe_id
                     ) THEN
                    RAISE EXCEPTION USING
                      ERRCODE = '23514',
                      MESSAGE = 'kidding requires a confirmed pregnancy for the same doe and farm';
                  END IF;
    """
        if strict
        else """
                  IF parent_outcome = 'ABORTED' THEN
                    RAISE EXCEPTION USING
                      ERRCODE = '23514',
                      MESSAGE = 'an aborted pregnancy cannot have a kidding record';
                  END IF;
    """
    )
    referenced_guard = (
        """
              -- Cross-farm/missing doe changes are rejected by the named
              -- composite FK. Avoid masking that tenant-boundary error with
              -- this same-farm reproductive invariant.
              IF NEW.farm_id IS NOT DISTINCT FROM OLD.farm_id
                 AND EXISTS (
                   SELECT 1 FROM animals doe
                   WHERE doe.id = NEW.doe_id AND doe.farm_id = NEW.farm_id
                 )
                 AND EXISTS (
                   SELECT 1
                   FROM kidding_records kidding
                   WHERE kidding.breeding_record_id = NEW.id
                     AND (
                          NEW.outcome IS DISTINCT FROM 'CONFIRMED_PREGNANT'
                       OR kidding.doe_id IS DISTINCT FROM NEW.doe_id
                       OR kidding.farm_id IS DISTINCT FROM NEW.farm_id
                     )
                 ) THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23514',
                  MESSAGE = 'linked kidding must remain confirmed and match doe/farm';
              END IF;
    """
        if strict
        else ""
    )
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION enforce_reproductive_outcome_integrity()
            RETURNS trigger AS $$
            DECLARE
              parent_outcome text;
              parent_doe_id integer;
              parent_farm_id integer;
            BEGIN
              IF TG_TABLE_NAME = 'kidding_records' THEN
                IF NEW.breeding_record_id IS NOT NULL THEN
                  SELECT outcome, doe_id, farm_id
                  INTO parent_outcome, parent_doe_id, parent_farm_id
                  FROM breeding_records
                  WHERE id = NEW.breeding_record_id
                  FOR UPDATE;
                  {strict_guard}
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
                OR (NEW.loss_recorded_by_id IS DISTINCT FROM OLD.loss_recorded_by_id
                    AND NEW.loss_recorded_by_id IS NOT NULL)
              ) THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23514',
                  MESSAGE = 'pregnancy-loss audit metadata is immutable';
              END IF;
              IF NEW.outcome = 'ABORTED' AND EXISTS (
                SELECT 1 FROM kidding_records WHERE breeding_record_id = NEW.id
              ) THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23514',
                  MESSAGE = 'a pregnancy with a kidding record cannot be aborted';
              END IF;
              {referenced_guard}
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    # CREATE OR REPLACE FUNCTION does not change an existing trigger's UPDATE
    # column list. Recreate it so post-insert doe/farm mutations are guarded,
    # and restore the historical list during downgrade.
    op.execute("DROP TRIGGER IF EXISTS trg_kidding_reproductive_outcome ON kidding_records")
    update_columns = (
        "breeding_record_id, date, doe_id, farm_id" if strict else "breeding_record_id, date"
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_kidding_reproductive_outcome
            BEFORE INSERT OR UPDATE OF {update_columns} ON kidding_records
            FOR EACH ROW EXECUTE FUNCTION enforce_reproductive_outcome_integrity()
            """
        )
    )


def _create_stillborn_tag_namespace_triggers() -> None:
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_stillborn_tag_namespace()
            RETURNS trigger AS $$
            BEGIN
              IF NEW.tag IS NULL OR btrim(NEW.tag) = '' OR NEW.status <> 'STILLBORN' THEN
                RETURN NEW;
              END IF;
              PERFORM pg_advisory_xact_lock(NEW.farm_id, hashtext(NEW.tag));
              IF EXISTS (
                SELECT 1 FROM animals
                WHERE farm_id = NEW.farm_id AND tag_number = NEW.tag
              ) THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23505',
                  CONSTRAINT = 'uq_stillborn_tag_farm_namespace',
                  MESSAGE = 'stillborn tag conflicts with an animal tag in this farm';
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
            CREATE FUNCTION enforce_animal_tag_against_stillborns()
            RETURNS trigger AS $$
            BEGIN
              PERFORM pg_advisory_xact_lock(NEW.farm_id, hashtext(NEW.tag_number));
              IF EXISTS (
                SELECT 1 FROM kid_entries
                WHERE farm_id = NEW.farm_id
                  AND tag = NEW.tag_number
                  AND status = 'STILLBORN'
              ) THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23505',
                  CONSTRAINT = 'uq_stillborn_tag_farm_namespace',
                  MESSAGE = 'animal tag conflicts with a stillborn tag in this farm';
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        """
        CREATE TRIGGER trg_stillborn_tag_namespace
        BEFORE INSERT OR UPDATE OF farm_id, tag, status ON kid_entries
        FOR EACH ROW EXECUTE FUNCTION enforce_stillborn_tag_namespace()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_animal_tag_against_stillborns
        BEFORE INSERT OR UPDATE OF farm_id, tag_number ON animals
        FOR EACH ROW EXECUTE FUNCTION enforce_animal_tag_against_stillborns()
        """
    )


def upgrade() -> None:
    op.add_column(
        "bucket_moves",
        sa.Column(
            "effective_date",
            sa.Date(),
            server_default=sa.text("CURRENT_DATE"),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            """
            WITH move_context AS (
              SELECT
                move.id,
                move.from_bucket IS NULL AS initial_placement,
                animal.purchase_date,
                animal.date_of_birth,
                animal.estimated_dob,
                (
                  move.moved_at AT TIME ZONE 'UTC'
                  AT TIME ZONE coalesce(farm.timezone, 'Asia/Kolkata')
                )::date AS recorded_date
              FROM bucket_moves move
              JOIN animals animal ON animal.id = move.animal_id
              JOIN farms farm ON farm.id = animal.farm_id
            )
            UPDATE bucket_moves move
            SET effective_date = CASE
              WHEN context.initial_placement THEN coalesce(
                context.purchase_date,
                context.date_of_birth,
                context.estimated_dob,
                context.recorded_date
              )
              ELSE context.recorded_date
            END
            FROM move_context context
            WHERE context.id = move.id
            """
        )
    )
    op.execute("UPDATE bucket_moves SET effective_date = current_date WHERE effective_date IS NULL")
    op.alter_column("bucket_moves", "effective_date", existing_type=sa.Date(), nullable=False)

    # A rejection describes only the current returned-to-PENDING state.
    op.execute(
        """
        UPDATE tasks
        SET verification_note = NULL, rejected_by_id = NULL, rejected_at = NULL
        WHERE status <> 'PENDING'
          AND (verification_note IS NOT NULL
               OR rejected_by_id IS NOT NULL
               OR rejected_at IS NOT NULL)
        """
    )
    op.create_check_constraint(
        "ck_tasks_rejection_current_state",
        "tasks",
        "status = 'PENDING' OR (verification_note IS NULL "
        "AND rejected_by_id IS NULL AND rejected_at IS NULL)",
        postgresql_not_valid=True,
    )
    op.execute("ALTER TABLE tasks VALIDATE CONSTRAINT ck_tasks_rejection_current_state")

    # Retain the first historical stillborn identifier, clearing only later or
    # animal-conflicting duplicates before adding the durable uniqueness gate.
    op.execute(
        """
        UPDATE kid_entries victim
        SET tag = NULL
        WHERE victim.status = 'STILLBORN'
          AND victim.tag IS NOT NULL
          AND btrim(victim.tag) <> ''
          AND (
            EXISTS (
              SELECT 1 FROM animals animal
              WHERE animal.farm_id = victim.farm_id
                AND animal.tag_number = victim.tag
            )
            OR EXISTS (
              SELECT 1 FROM kid_entries earlier
              WHERE earlier.farm_id = victim.farm_id
                AND earlier.tag = victim.tag
                AND earlier.id < victim.id
            )
          )
        """
    )
    op.create_index(
        "uq_kid_entries_farm_tag",
        "kid_entries",
        ["farm_id", "tag"],
        unique=True,
        postgresql_where=sa.text("tag IS NOT NULL AND btrim(tag) <> ''"),
    )
    _create_stillborn_tag_namespace_triggers()

    # Preserve malformed historical kiddings but detach an association that
    # asserted the wrong pregnancy. New linked rows are rejected below.
    op.execute(
        """
        UPDATE kidding_records kidding
        SET breeding_record_id = NULL
        FROM breeding_records breeding
        WHERE breeding.id = kidding.breeding_record_id
          AND (breeding.outcome <> 'CONFIRMED_PREGNANT'
               OR breeding.doe_id <> kidding.doe_id
               OR breeding.farm_id <> kidding.farm_id)
        """
    )
    _replace_reproductive_trigger(strict=True)
    _repair_schedule_links()


def downgrade() -> None:
    # Text-link repairs are intentionally not falsified on downgrade; only the
    # stricter write behavior/schema is removed.
    _replace_reproductive_trigger(strict=False)
    op.execute("DROP TRIGGER IF EXISTS trg_animal_tag_against_stillborns ON animals")
    op.execute("DROP TRIGGER IF EXISTS trg_stillborn_tag_namespace ON kid_entries")
    op.execute("DROP FUNCTION IF EXISTS enforce_animal_tag_against_stillborns()")
    op.execute("DROP FUNCTION IF EXISTS enforce_stillborn_tag_namespace()")
    op.drop_index("uq_kid_entries_farm_tag", table_name="kid_entries")
    op.drop_constraint("ck_tasks_rejection_current_state", "tasks", type_="check")
    op.drop_column("bucket_moves", "effective_date")
