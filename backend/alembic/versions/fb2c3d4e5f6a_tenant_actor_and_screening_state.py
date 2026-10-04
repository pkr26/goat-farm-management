"""Harden screening evidence states and tenant-bound actor attribution.

Revision ID: fb2c3d4e5f6a
Revises: fa1b2c3d4e5f
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "fb2c3d4e5f6a"
down_revision: str | Sequence[str] | None = "fa1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ACTOR_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("screening_batches", ("created_by_id",)),
    ("screening_findings", ("reviewed_by_id",)),
    ("screening_finding_reviews", ("reviewed_by_id",)),
    ("health_events", ("created_by_id",)),
    ("transactions", ("created_by_id", "voided_by_id")),
)


def _actor_trigger_name(table: str) -> str:
    return f"trg_{table}_farm_actor"


def upgrade() -> None:
    # Fail closed rather than silently rewriting contradictory clinical
    # evidence. The exception tells the operator which repair is required.
    op.execute(
        """
        DO $$
        DECLARE bad_table text;
        BEGIN
            SELECT source INTO bad_table
            FROM (
                SELECT 'screening_images' AS source
                FROM screening_images
                WHERE NOT (
                    (status = 'ERROR' AND error IS NOT NULL AND btrim(error) <> '') OR
                    (
                        status IN ('SKIPPED', 'UNASSESSABLE') AND
                        (error IS NULL OR btrim(error) <> '')
                    ) OR (
                        status NOT IN ('ERROR', 'SKIPPED', 'UNASSESSABLE') AND
                        error IS NULL
                    )
                )
                OR NOT (
                    (width IS NULL AND height IS NULL) OR
                    (
                        width IS NOT NULL AND height IS NOT NULL AND
                        width > 0 AND height > 0
                    )
                )
                UNION ALL
                SELECT 'screening_crops'
                FROM screening_crops
                WHERE NOT (
                    (status = 'ERROR' AND error IS NOT NULL AND btrim(error) <> '') OR
                    (
                        status IN ('SKIPPED', 'UNASSESSABLE') AND
                        (error IS NULL OR btrim(error) <> '')
                    ) OR (
                        status NOT IN ('ERROR', 'SKIPPED', 'UNASSESSABLE') AND
                        error IS NULL
                    )
                )
                UNION ALL
                SELECT 'screening_runs'
                FROM screening_runs
                WHERE (
                    (
                        run_status = 'OK' AND error IS NULL AND latency_ms IS NOT NULL AND
                        (
                            (stage = 'DETECT' AND verdict IS NULL) OR
                            (
                                stage IN ('GATE', 'CROSS_CHECK') AND
                                verdict IS NOT NULL AND
                                verdict IN ('healthy', 'flagged', 'unassessable')
                            ) OR (
                                stage IN (
                                    'SPECIALIST_SKIN', 'SPECIALIST_EYE',
                                    'SPECIALIST_HOOF', 'SPECIALIST_UDDER',
                                    'SPECIALIST_GENERAL'
                                ) AND verdict IS NULL
                            )
                        )
                    ) OR (
                        run_status = 'ERROR' AND error IS NOT NULL AND btrim(error) <> '' AND
                        verdict IS NULL AND confidence IS NULL
                    )
                ) IS NOT TRUE
            ) AS contradictions
            LIMIT 1;
            IF bad_table IS NOT NULL THEN
                RAISE EXCEPTION
                    'Contradictory screening evidence in %. Reconcile the row and retry.',
                    bad_table;
            END IF;
        END $$
        """
    )

    op.drop_constraint(
        "ck_screening_images_error_requires_error_status", "screening_images", type_="check"
    )
    op.create_check_constraint(
        "ck_screening_images_error_requires_error_status",
        "screening_images",
        "(status = 'ERROR' AND error IS NOT NULL AND btrim(error) <> '') OR "
        "(status IN ('SKIPPED', 'UNASSESSABLE') AND "
        "(error IS NULL OR btrim(error) <> '')) OR "
        "(status NOT IN ('ERROR', 'SKIPPED', 'UNASSESSABLE') AND error IS NULL)",
    )
    op.drop_constraint("ck_screening_images_dimensions_positive", "screening_images", type_="check")
    op.create_check_constraint(
        "ck_screening_images_dimensions_positive",
        "screening_images",
        "(width IS NULL AND height IS NULL) OR "
        "(width IS NOT NULL AND height IS NOT NULL AND width > 0 AND height > 0)",
    )
    op.drop_constraint(
        "ck_screening_crops_error_requires_error_status", "screening_crops", type_="check"
    )
    op.create_check_constraint(
        "ck_screening_crops_error_requires_error_status",
        "screening_crops",
        "(status = 'ERROR' AND error IS NOT NULL AND btrim(error) <> '') OR "
        "(status IN ('SKIPPED', 'UNASSESSABLE') AND "
        "(error IS NULL OR btrim(error) <> '')) OR "
        "(status NOT IN ('ERROR', 'SKIPPED', 'UNASSESSABLE') AND error IS NULL)",
    )
    op.drop_constraint("ck_screening_runs_verdict_vocabulary", "screening_runs", type_="check")
    op.create_check_constraint(
        "ck_screening_runs_verdict_vocabulary",
        "screening_runs",
        "run_status <> 'OK' OR ((stage = 'DETECT' AND verdict IS NULL) OR "
        "(stage IN ('GATE', 'CROSS_CHECK') AND verdict IS NOT NULL AND "
        "verdict IN ('healthy', 'flagged', 'unassessable')) OR "
        "(stage IN ('SPECIALIST_SKIN', 'SPECIALIST_EYE', 'SPECIALIST_HOOF', "
        "'SPECIALIST_UDDER', 'SPECIALIST_GENERAL') AND verdict IS NULL))",
    )
    op.create_check_constraint(
        "ck_screening_runs_payload_by_status",
        "screening_runs",
        "(run_status = 'OK' AND error IS NULL) OR "
        "(run_status = 'ERROR' AND error IS NOT NULL AND btrim(error) <> '' "
        "AND verdict IS NULL AND confidence IS NULL)",
    )

    # Owners intentionally are not memberships. Preserve each owner as a
    # compact immutable affiliation anchor before actor validation is added,
    # then let a database trigger retain every later owner (including raw-SQL
    # transfers). Assignment-time validation below still requires the current
    # owner (or a retained membership); history proves old attribution but
    # does not authorize fabricating new rows after ownership is transferred.
    op.create_table(
        "farm_owner_history",
        sa.Column("farm_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["farm_id"], ["farms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("farm_id", "user_id"),
    )
    op.execute(
        """
        CREATE FUNCTION validate_farm_owner_history_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM farms
                WHERE farms.id = NEW.farm_id AND farms.owner_id = NEW.user_id
            ) THEN
                RAISE EXCEPTION
                    'user % is not the current owner of farm %', NEW.user_id, NEW.farm_id
                    USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_farm_owner_history() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            -- A row-level FK cascade runs after the parent farm row has been
            -- deleted and is no longer visible to this transaction. Permit
            -- that one whole-tenant lifecycle path while still rejecting a
            -- direct attempt to rewrite or erase a live farm's owner history.
            IF TG_OP = 'DELETE' AND NOT EXISTS (
                SELECT 1 FROM farms WHERE farms.id = OLD.farm_id
            ) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'farm_owner_history is append-only'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_farm_owner_history_validate
        BEFORE INSERT ON farm_owner_history
        FOR EACH ROW EXECUTE FUNCTION validate_farm_owner_history_insert()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_farm_owner_history_append_only
        BEFORE UPDATE OR DELETE ON farm_owner_history
        FOR EACH ROW EXECUTE FUNCTION protect_farm_owner_history()
        """
    )
    op.execute(
        """
        INSERT INTO farm_owner_history (farm_id, user_id, acquired_at)
        SELECT id, owner_id, COALESCE(created_at, timezone('UTC', now()))
        FROM farms
        ON CONFLICT (farm_id, user_id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE FUNCTION retain_farm_owner_history() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO farm_owner_history (farm_id, user_id)
            VALUES (NEW.id, NEW.owner_id)
            ON CONFLICT (farm_id, user_id) DO NOTHING;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_farms_retain_owner_history
        AFTER INSERT OR UPDATE OF owner_id ON farms
        FOR EACH ROW EXECUTE FUNCTION retain_farm_owner_history()
        """
    )

    # Existing cross-tenant attribution is evidence corruption, not something
    # a migration may guess how to repair. Inactive memberships count: they are
    # deliberately retained as historical affiliation anchors.
    for table, actor_columns in _ACTOR_COLUMNS:
        for actor_column in actor_columns:
            op.execute(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM {table} AS record
                        WHERE record.{actor_column} IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM farms
                              WHERE farms.id = record.farm_id
                                AND farms.owner_id = record.{actor_column}
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM farm_memberships
                              WHERE farm_memberships.farm_id = record.farm_id
                                AND farm_memberships.user_id = record.{actor_column}
                          )
                    ) THEN
                        RAISE EXCEPTION
                            'Cross-farm actor in %.%; reconcile historical affiliation',
                            '{table}', '{actor_column}';
                    END IF;
                END $$
                """
            )

    op.execute(
        """
        CREATE FUNCTION enforce_farm_actor_attribution() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            actor_column text;
            actor_id bigint;
            previous_actor_id bigint;
        BEGIN
            FOREACH actor_column IN ARRAY TG_ARGV LOOP
                actor_id := NULLIF(to_jsonb(NEW) ->> actor_column, '')::bigint;
                -- Attribution is an assignment-time invariant, not a reason
                -- to retroactively invalidate immutable historical evidence.
                -- A former owner/member may legitimately disappear from the
                -- current affiliation tables after this row was written. An
                -- UPDATE that preserves both tenant and actor therefore keeps
                -- that established attribution; changing either revalidates
                -- against the current owner or a retained membership.
                IF TG_OP = 'UPDATE' THEN
                    previous_actor_id :=
                        NULLIF(to_jsonb(OLD) ->> actor_column, '')::bigint;
                    IF NEW.farm_id IS NOT DISTINCT FROM OLD.farm_id
                       AND actor_id IS NOT DISTINCT FROM previous_actor_id THEN
                        CONTINUE;
                    END IF;
                END IF;
                IF actor_id IS NULL THEN
                    CONTINUE;
                END IF;
                IF EXISTS (
                    SELECT 1 FROM farms
                    WHERE farms.id = NEW.farm_id
                      AND farms.owner_id = actor_id
                ) OR EXISTS (
                    SELECT 1 FROM farm_memberships
                    WHERE farm_memberships.farm_id = NEW.farm_id
                      AND farm_memberships.user_id = actor_id
                ) THEN
                    CONTINUE;
                END IF;
                RAISE EXCEPTION
                    'actor % in %.% has no current or retained affiliation with farm %',
                    actor_id, TG_TABLE_NAME, actor_column, NEW.farm_id
                    USING ERRCODE = '23503';
            END LOOP;
            RETURN NEW;
        END;
        $$
        """
    )
    for table, actor_columns in _ACTOR_COLUMNS:
        arguments = ", ".join(f"'{column}'" for column in actor_columns)
        watched = ", ".join(("farm_id", *actor_columns))
        trigger = _actor_trigger_name(table)
        op.execute(
            f"CREATE TRIGGER {trigger}_insert BEFORE INSERT ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION enforce_farm_actor_attribution({arguments})"
        )
        op.execute(
            f"CREATE TRIGGER {trigger}_update BEFORE UPDATE OF {watched} ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION enforce_farm_actor_attribution({arguments})"
        )

    # Guard exact legacy seed text so farm/operator customizations survive.
    op.execute(
        """
        UPDATE bucket_definitions
        SET who = 'Purchased doelings 6–7 mo; own female kids 2–11 mo'
        WHERE code = 'FOUNDATION'
          AND who = 'Purchased doelings 6–7 mo; own female kids 2–10 mo'
        """
    )
    op.execute(
        """
        UPDATE bucket_definitions
        SET who = 'Female kids 2–11 months; 60/40 mix steady'
        WHERE code = 'FEMALE_KIDS'
          AND who = 'Female kids 2–10 months; 60/40 mix steady'
        """
    )
    op.execute(
        """
        UPDATE bucket_definitions
        SET exit_rule = 'Breeding-ready (≥12 mo, ≥22 kg) → BREEDING'
        WHERE code IN ('FOUNDATION', 'FEMALE_KIDS')
          AND exit_rule = 'Breeding-ready (10–12 mo, ≥22 kg) → BREEDING'
        """
    )


def downgrade() -> None:
    # The corrected husbandry text is intentionally not reverted: reintroducing
    # unsafe guidance is not a truthful rollback of application behavior.
    for table, _actor_columns in reversed(_ACTOR_COLUMNS):
        trigger = _actor_trigger_name(table)
        op.execute(f"DROP TRIGGER {trigger}_update ON {table}")
        op.execute(f"DROP TRIGGER {trigger}_insert ON {table}")
    op.execute("DROP FUNCTION enforce_farm_actor_attribution()")
    op.execute("DROP TRIGGER trg_farms_retain_owner_history ON farms")
    op.execute("DROP FUNCTION retain_farm_owner_history()")
    op.execute("DROP TRIGGER trg_farm_owner_history_append_only ON farm_owner_history")
    op.execute("DROP TRIGGER trg_farm_owner_history_validate ON farm_owner_history")
    op.execute("DROP FUNCTION protect_farm_owner_history()")
    op.execute("DROP FUNCTION validate_farm_owner_history_insert()")
    op.drop_table("farm_owner_history")

    op.drop_constraint("ck_screening_runs_payload_by_status", "screening_runs", type_="check")
    op.drop_constraint("ck_screening_runs_verdict_vocabulary", "screening_runs", type_="check")
    op.create_check_constraint(
        "ck_screening_runs_verdict_vocabulary",
        "screening_runs",
        "run_status <> 'OK' OR verdict IN ('healthy', 'flagged', 'unassessable')",
    )
    op.drop_constraint(
        "ck_screening_crops_error_requires_error_status", "screening_crops", type_="check"
    )
    op.create_check_constraint(
        "ck_screening_crops_error_requires_error_status",
        "screening_crops",
        "status <> 'ERROR' OR (error IS NOT NULL AND btrim(error) <> '')",
    )
    op.drop_constraint("ck_screening_images_dimensions_positive", "screening_images", type_="check")
    op.create_check_constraint(
        "ck_screening_images_dimensions_positive",
        "screening_images",
        "width IS NULL OR height IS NULL OR (width > 0 AND height > 0)",
    )
    op.drop_constraint(
        "ck_screening_images_error_requires_error_status", "screening_images", type_="check"
    )
    op.create_check_constraint(
        "ck_screening_images_error_requires_error_status",
        "screening_images",
        "status <> 'ERROR' OR (error IS NOT NULL AND btrim(error) <> '')",
    )
