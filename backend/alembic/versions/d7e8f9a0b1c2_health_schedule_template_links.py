"""canonical, indexed health-event schedule template links

Revision ID: d7e8f9a0b1c2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-08 18:35:00.000000+00:00

Free-text schedule matching forced every animal schedule request to rescan its
entire health history once per template.  A nullable immutable FK preserves
unscheduled health records while making canonical latest-event probes bounded
index lookups.  Existing exact and unambiguous legacy matches are linked once;
ambiguous prose remains untouched for the application's capped compatibility
window instead of being assigned an arbitrary programme.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d7e8f9a0b1c2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_schedule_links() -> None:
    # Exact names came from the first canonical schedule UI and are
    # authoritative even when product_name contains a trade name.
    op.execute(
        sa.text(
            r"""
            UPDATE health_events event
            SET schedule_template_id = template.id
            FROM vaccine_templates template
            WHERE event.schedule_template_id IS NULL
              AND event.type IN ('VACCINE', 'DEWORMING')
              AND event.schedule_template_name IS NOT NULL
              AND btrim(event.schedule_template_name) <> ''
              AND btrim(regexp_replace(
                    replace(lower(event.schedule_template_name), '+', ' + '),
                    '\s+', ' ', 'g'
                  )) = btrim(regexp_replace(
                    replace(lower(template.name), '+', ' + '),
                    '\s+', ' ', 'g'
                  ))
              AND (
                (event.type = 'DEWORMING' AND template.name = 'Deworming')
                OR (event.type = 'VACCINE' AND template.name <> 'Deworming')
              )
            """
        )
    )

    # DEWORMING type is the reliable programme identity for old drug-name
    # rows (Albendazole, Ivermectin, ...), provided no conflicting explicit
    # schedule name was recorded.
    op.execute(
        sa.text(
            """
            UPDATE health_events event
            SET schedule_template_id = template.id
            FROM vaccine_templates template
            WHERE event.schedule_template_id IS NULL
              AND event.type = 'DEWORMING'
              AND (event.schedule_template_name IS NULL
                   OR btrim(event.schedule_template_name) = '')
              AND template.name = 'Deworming'
            """
        )
    )

    # Match the former compatibility rules set-wise, but link only events with
    # exactly one candidate. Combination/ambiguous prose remains unlinked so
    # migration never fabricates a single authoritative programme.
    op.execute(
        sa.text(
            r"""
            WITH eligible_events AS (
              SELECT
                event.id,
                btrim(regexp_replace(
                  replace(lower(concat(
                    coalesce(event.product_name, ''),
                    ' ',
                    coalesce(event.disease_target, '')
                  )), '+', ' + '),
                  '\s+', ' ', 'g'
                )) AS haystack
              FROM health_events event
              WHERE event.schedule_template_id IS NULL
                AND event.type = 'VACCINE'
                AND (event.schedule_template_name IS NULL
                     OR btrim(event.schedule_template_name) = '')
            ),
            candidates AS (
              SELECT event.id AS event_id, template.id AS template_id
              FROM eligible_events event
              CROSS JOIN vaccine_templates template
              WHERE template.name <> 'Deworming'
                AND (template.first_dose_age_months IS NOT NULL
                     OR template.repeat_months IS NOT NULL)
                AND (
                  strpos(
                    event.haystack,
                    btrim(regexp_replace(
                      replace(lower(split_part(template.name, '(', 1)), '+', ' + '),
                      '\s+', ' ', 'g'
                    ))
                  ) > 0
                  OR (
                    template.name = 'FMD'
                    AND (strpos(event.haystack, 'raksha-triovac') > 0
                         OR strpos(event.haystack, 'triovac') > 0
                         OR strpos(event.haystack, 'raksha-ovac') > 0)
                  )
                  OR (
                    template.name = 'PPR'
                    AND strpos(event.haystack, 'raksha-ppr') > 0
                  )
                  OR (
                    template.name = 'Goat Pox'
                    AND (strpos(event.haystack, 'goatpox') > 0
                         OR strpos(event.haystack, 'goat-pox') > 0
                         OR strpos(event.haystack, 'raksha-gp') > 0)
                  )
                  OR (
                    template.name = 'Enterotoxaemia (ET)'
                    AND (strpos(event.haystack, 'entero') > 0
                         OR strpos(event.haystack, 'raksha-et') > 0)
                  )
                  OR (
                    template.name = 'Haemorrhagic Septicaemia (HS)'
                    AND (strpos(event.haystack, 'hemorrhagic') > 0
                         OR strpos(event.haystack, 'raksha-hs') > 0)
                  )
                  OR (
                    substring(template.name from '\(([^)]+)\)') IS NOT NULL
                    AND event.haystack ~ (
                      '\m'
                      || lower(substring(template.name from '\(([^)]+)\)'))
                      || '\M'
                    )
                  )
                )
            ),
            unique_candidates AS (
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


def upgrade() -> None:
    op.add_column(
        "health_events",
        sa.Column("schedule_template_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_health_events_schedule_template",
        "health_events",
        "vaccine_templates",
        ["schedule_template_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_health_events_schedule_template_type",
        "health_events",
        "schedule_template_id IS NULL OR type IN ('VACCINE', 'DEWORMING')",
        postgresql_not_valid=True,
    )

    _backfill_schedule_links()
    op.execute(
        sa.text(
            "ALTER TABLE health_events VALIDATE CONSTRAINT ck_health_events_schedule_template_type"
        )
    )

    op.create_index(
        "ix_health_events_animal_template_latest",
        "health_events",
        [
            "animal_id",
            "schedule_template_id",
            sa.text("date DESC"),
            sa.text("id DESC"),
        ],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION guard_health_event_schedule_template_id()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              IF NEW.schedule_template_id IS DISTINCT FROM OLD.schedule_template_id THEN
                RAISE EXCEPTION USING
                  ERRCODE = '23514',
                  MESSAGE = 'health event schedule template link is immutable';
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
            CREATE TRIGGER trg_health_event_schedule_template_id_immutable
            BEFORE UPDATE OF schedule_template_id ON health_events
            FOR EACH ROW
            EXECUTE FUNCTION guard_health_event_schedule_template_id()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_health_event_schedule_template_id_immutable "
            "ON health_events"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS guard_health_event_schedule_template_id()"))
    op.drop_index("ix_health_events_animal_template_latest", table_name="health_events")
    op.drop_constraint(
        "ck_health_events_schedule_template_type",
        "health_events",
        type_="check",
    )
    op.drop_constraint(
        "fk_health_events_schedule_template",
        "health_events",
        type_="foreignkey",
    )
    op.drop_column("health_events", "schedule_template_id")
