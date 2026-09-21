"""propagate 2026-09-01 species reference-data corrections to existing farms

The remediation commit changed several pieces of seeded reference data that
``seed.py`` inserts with ``ON CONFLICT DO NOTHING`` — deliberately
create-only, so reference rows on an existing database never change under a
booting app. That mechanism is correct for startup, but it also meant four
corrections silently never reached any already-deployed farm:

* ``PPR`` vaccination cadence 36 -> 12 months (Telangana department camps
  revaccinate annually; the triennial trial immunity is not the field
  schedule the herd is audited against).
* The ``ORF`` vaccine template is dropped entirely — Indian practice (TNAU)
  does not vaccinate for contagious ecthyma; control is outbreak-driven.
* The goat ``QUARANTINE`` bucket ration 0.8 -> 1.1 kg/day/head: ~3% BW DM
  for a 30 kg adult of the 75:25 mix, so newly transported animals do not
  under-eat through the immunity-critical 45 days.
* Dairy dry-group/calving-pen move duties generated before the EDD-60
  dry-off change still carry the legacy EDD-21 due date, which the
  completion guard now rejects ("does not match the recorded pregnancy") —
  every such PENDING duty was permanently uncompletable. Re-date them to
  the species' EDD-60 point (and fix their title's promise).

Every UPDATE is guarded so an operator who deliberately customized a row is
never overwritten: PPR only at cadence 36, the ration only at 0.8, and only
duties whose due date still equals expected_kidding_date - 21 on a
BUFFALO_DAIRY farm.

Revision ID: b5d7f9a1c3e5
Revises: e3a5b7c9d1f2
"""

from alembic import op

revision = "b5d7f9a1c3e5"
down_revision = "e3a5b7c9d1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE vaccine_templates
           SET repeat_months = 12,
               timing_note = 'Core vaccine; annual revaccination (department camp schedule)'
         WHERE farm_type = 'GOAT'
           AND name = 'PPR'
           AND repeat_months = 36
        """
    )
    # Farms that already recorded ORF vaccinations hold FK references to the
    # template row (health_events.schedule_template_id, linked by
    # d7e8f9a0b1c2's backfill); deleting the template first aborted the
    # upgrade with a raw 23503. Detach the references before the delete —
    # the event rows keep every recorded fact; only the schedule-source link
    # retires with its template (2026-09-20 audit P2-13).
    #
    # d7e8f9a0b1c2 (re-created by e3f4a5b6c7d9) guards the very column this
    # UPDATE clears: trg_health_event_schedule_template_id_immutable raises
    # 23514 on any change to schedule_template_id, so the detach itself would
    # abort the upgrade on exactly the databases it exists to fix. Drop the
    # trigger around the one-shot migration UPDATE and re-create it — the
    # same pattern e3f4a5b6c7d9 uses for its own schedule-link repairs. The
    # guard function is never dropped, so the re-create needs no DDL of its
    # own; if the trigger is somehow absent (manually repaired database) the
    # IF EXISTS makes this a no-op either way.
    op.execute(
        "DROP TRIGGER IF EXISTS trg_health_event_schedule_template_id_immutable ON health_events"
    )
    op.execute(
        """
        UPDATE health_events AS event
           SET schedule_template_id = NULL
          FROM vaccine_templates AS template
         WHERE event.schedule_template_id = template.id
           AND template.farm_type = 'GOAT'
           AND template.name = 'ORF'
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_health_event_schedule_template_id_immutable
        BEFORE UPDATE OF schedule_template_id ON health_events
        FOR EACH ROW EXECUTE FUNCTION guard_health_event_schedule_template_id()
        """
    )
    op.execute("DELETE FROM vaccine_templates WHERE farm_type = 'GOAT' AND name = 'ORF'")
    op.execute(
        """
        UPDATE bucket_definitions
           SET daily_kg_per_head = 1.1
         WHERE farm_type = 'GOAT'
           AND code = 'QUARANTINE'
           AND daily_kg_per_head = 0.8
        """
    )
    # Legacy dairy delivery-move duties: EDD-21 -> EDD-60 (dry-off point).
    op.execute(
        """
        UPDATE tasks AS t
           SET due_date = br.expected_kidding_date - 60,
               title = replace(t.title, 'calving in ~2 weeks', 'calving in ~2 months')
          FROM breeding_records AS br
          JOIN farms AS f ON f.id = br.farm_id
         WHERE t.breeding_record_id = br.id
           AND f.farm_type = 'BUFFALO_DAIRY'
           AND t.category = 'BUCKET_MOVE'
           AND t.status = 'PENDING'
           AND t.due_date = br.expected_kidding_date - 21
        """
    )


def downgrade() -> None:
    # Reference-data corrections are not reverted: the guarded re-insert of
    # ORF/36-month PPR would reintroduce exactly the schedules the audit
    # removed, and rolling dry-off duties back to EDD-21 would make them
    # uncompletable again under the current guard.
    pass
