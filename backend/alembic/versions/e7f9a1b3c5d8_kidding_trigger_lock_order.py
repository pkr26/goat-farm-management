"""align direct kidding writes with the animal-first lifecycle lock order

Revision ID: e7f9a1b3c5d8
Revises: d5e7f9a1b3c4
Create Date: 2026-08-19 00:00:00.000000+00:00

The API locks every referenced Animal before its BreedingRecord.  The
cross-table reproductive trigger historically did the reverse for a direct
``kidding_records`` INSERT: its BEFORE trigger locked BreedingRecord, then the
row's FK checks acquired key-share on the doe.  A concurrent animal status
transition could therefore hold the doe while waiting for that breeding row,
forming a deterministic PostgreSQL deadlock.

Lock the one insert-time doe before the breeding parent in a trigger whose
name sorts before the existing reproductive trigger.  Post-insert relationship
rewrites are not a supported correction primitive and are now rejected before
they can acquire downstream locks.  Date-only corrections retain their kid-
mortality chronology guard in a separate trigger; it needs no parent lock
because the kid-mortality trigger already serializes on the KiddingRecord row.

The inverse is closed as well: a direct BreedingRecord parent rewrite used to
own Breeding before its composite animal FK acquired key-share on the new doe
or buck.  Breeding farm/doe/buck identity is an immutable recorded service fact,
so reject such rewrites in the alphabetically-first BEFORE trigger instead of
letting them race an Animal-first kidding insert.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7f9a1b3c5d8"
down_revision: str | Sequence[str] | None = "d5e7f9a1b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INSERT_LOCK_TRIGGER = "trg_00_kidding_insert_lock_order"
RELATIONSHIP_GUARD_TRIGGER = "trg_00_kidding_relationship_immutable"
DATE_GUARD_TRIGGER = "trg_kidding_date_mortality_guard"
REPRODUCTIVE_TRIGGER = "trg_kidding_reproductive_outcome"
BREEDING_RELATIONSHIP_GUARD_TRIGGER = "trg_00_breeding_relationship_immutable"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE FUNCTION guard_breeding_relationship_immutable()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              IF NEW.farm_id IS DISTINCT FROM OLD.farm_id
                 OR NEW.doe_id IS DISTINCT FROM OLD.doe_id
                 OR NEW.buck_id IS DISTINCT FROM OLD.buck_id THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23514',
                  CONSTRAINT = 'ck_breeding_relationship_immutable',
                  MESSAGE = 'breeding farm, doe and buck relationship are immutable';
              END IF;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER {BREEDING_RELATIONSHIP_GUARD_TRIGGER}
            BEFORE UPDATE OF farm_id, doe_id, buck_id ON breeding_records
            FOR EACH ROW EXECUTE FUNCTION guard_breeding_relationship_immutable()
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION lock_kidding_parents_in_lifecycle_order()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              -- Match every application workflow: Animal(s) first, then the
              -- reproductive row. Missing/cross-farm references deliberately
              -- fall through so their named composite FKs remain authoritative.
              PERFORM animal.id
              FROM animals AS animal
              WHERE animal.id = NEW.doe_id
                AND animal.farm_id = NEW.farm_id
              FOR UPDATE;

              IF NEW.breeding_record_id IS NOT NULL THEN
                PERFORM breeding.id
                FROM breeding_records AS breeding
                WHERE breeding.id = NEW.breeding_record_id
                FOR UPDATE;
              END IF;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER {INSERT_LOCK_TRIGGER}
            BEFORE INSERT ON kidding_records
            FOR EACH ROW EXECUTE FUNCTION lock_kidding_parents_in_lifecycle_order()
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION guard_kidding_relationship_immutable()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              IF NEW.farm_id IS DISTINCT FROM OLD.farm_id
                 OR NEW.doe_id IS DISTINCT FROM OLD.doe_id
                 OR NEW.breeding_record_id IS DISTINCT FROM OLD.breeding_record_id THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23514',
                  CONSTRAINT = 'ck_kidding_relationship_immutable',
                  MESSAGE = 'kidding farm, doe and breeding relationship are immutable';
              END IF;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER {RELATIONSHIP_GUARD_TRIGGER}
            BEFORE UPDATE OF farm_id, doe_id, breeding_record_id ON kidding_records
            FOR EACH ROW EXECUTE FUNCTION guard_kidding_relationship_immutable()
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION guard_kidding_date_against_mortality()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              IF NEW.date IS DISTINCT FROM OLD.date
                 AND EXISTS (
                   SELECT 1
                   FROM kid_entries
                   WHERE kidding_record_id = NEW.id
                     AND mortality_reported_at < NEW.date
                 ) THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23514',
                  CONSTRAINT = 'ck_kidding_date_before_kid_mortality',
                  MESSAGE = 'kidding date cannot follow a recorded kid mortality';
              END IF;
              RETURN NEW;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER {DATE_GUARD_TRIGGER}
            BEFORE UPDATE OF date ON kidding_records
            FOR EACH ROW EXECUTE FUNCTION guard_kidding_date_against_mortality()
            """
        )
    )

    # The shared reproductive function still protects INSERTs and
    # breeding-record updates.  Relationship UPDATEs now fail in the earlier
    # immutable guard, while date-only UPDATEs use the non-locking guard above.
    # Restrict this trigger to INSERT so an UPDATE never owns KiddingRecord and
    # then waits on Animal/Breeding in the opposite direction.
    op.execute(f"DROP TRIGGER {REPRODUCTIVE_TRIGGER} ON kidding_records")
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER {REPRODUCTIVE_TRIGGER}
            BEFORE INSERT ON kidding_records
            FOR EACH ROW EXECUTE FUNCTION enforce_reproductive_outcome_integrity()
            """
        )
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {REPRODUCTIVE_TRIGGER} ON kidding_records")
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER {REPRODUCTIVE_TRIGGER}
            BEFORE INSERT OR UPDATE OF breeding_record_id, date, doe_id, farm_id
            ON kidding_records
            FOR EACH ROW EXECUTE FUNCTION enforce_reproductive_outcome_integrity()
            """
        )
    )

    op.execute(f"DROP TRIGGER IF EXISTS {DATE_GUARD_TRIGGER} ON kidding_records")
    op.execute("DROP FUNCTION IF EXISTS guard_kidding_date_against_mortality()")
    op.execute(f"DROP TRIGGER IF EXISTS {RELATIONSHIP_GUARD_TRIGGER} ON kidding_records")
    op.execute("DROP FUNCTION IF EXISTS guard_kidding_relationship_immutable()")
    op.execute(f"DROP TRIGGER IF EXISTS {INSERT_LOCK_TRIGGER} ON kidding_records")
    op.execute("DROP FUNCTION IF EXISTS lock_kidding_parents_in_lifecycle_order()")
    op.execute(f"DROP TRIGGER IF EXISTS {BREEDING_RELATIONSHIP_GUARD_TRIGGER} ON breeding_records")
    op.execute("DROP FUNCTION IF EXISTS guard_breeding_relationship_immutable()")
