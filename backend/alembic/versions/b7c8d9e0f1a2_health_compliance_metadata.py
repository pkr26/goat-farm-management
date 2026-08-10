"""tie health compliance dates to scheduled-disease suspicion

Revision ID: b7c8d9e0f1a2
Revises: a6c9e2f4b7d1
Create Date: 2026-08-09 22:55:00.000000+00:00

Authority-notification and isolation dates describe a scheduled-disease
response. The old API accepted contradictory legacy rows, but those dates may
be regulatory evidence and must never be erased or reclassified automatically.
Fail with an actionable preflight when such rows exist; after an operator
reconciles their disease target/suspicion state, install a fully validated
constraint.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "a6c9e2f4b7d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep the preflight inside rendered SQL so both online Alembic and
    # ``alembic --sql`` artifacts fail with the same actionable evidence.
    # No row is rewritten: regulatory dates remain intact for an operator to
    # reconcile deliberately before retrying the migration.
    op.execute(
        """
        DO $health_compliance_preflight$
        DECLARE
            violation_count bigint;
            sample_ids bigint[];
        BEGIN
            SELECT count(*)
            INTO violation_count
            FROM health_events
            WHERE suspected_scheduled_disease IS NOT TRUE
              AND (
                authority_notified_at IS NOT NULL
                OR isolation_started_at IS NOT NULL
              );

            IF violation_count > 0 THEN
                SELECT array_agg(id ORDER BY id)
                INTO sample_ids
                FROM (
                    SELECT id
                    FROM health_events
                    WHERE suspected_scheduled_disease IS NOT TRUE
                      AND (
                        authority_notified_at IS NOT NULL
                        OR isolation_started_at IS NOT NULL
                      )
                    ORDER BY id
                    LIMIT 20
                ) AS sample;

                RAISE EXCEPTION USING
                    ERRCODE = 'check_violation',
                    MESSAGE = format(
                        'Cannot install health compliance integrity constraint: '
                        '%s legacy health event(s) have authority/isolation dates '
                        'without suspected_scheduled_disease=true. Preserve the dates '
                        'and reconcile each event with a nonblank disease_target before '
                        'retrying. Sample health_event ids: %s',
                        violation_count,
                        sample_ids
                    );
            END IF;
        END
        $health_compliance_preflight$
        """
    )
    # ADD NOT VALID holds its strong table lock only for the catalog change;
    # VALIDATE performs the potentially long scan under a less disruptive lock.
    # Both statements share Alembic's transaction, so a successful revision is
    # still guaranteed to end with a fully validated model constraint.
    op.create_check_constraint(
        "ck_health_events_compliance_requires_suspicion",
        "health_events",
        "suspected_scheduled_disease IS TRUE OR "
        "(authority_notified_at IS NULL AND isolation_started_at IS NULL)",
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE health_events VALIDATE CONSTRAINT "
        "ck_health_events_compliance_requires_suspicion"
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_health_events_compliance_requires_suspicion",
        "health_events",
        type_="check",
    )
