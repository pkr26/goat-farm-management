"""Truthful screening review, durable call budgets and treatment-round evidence.

Revision ID: f6c0e4f8b3d1
Revises: f5b9d3e7a2c0

No historical reviewer or round cohort is invented. Existing inconsistent
review/provenance rows stop the upgrade with an actionable preflight error;
export and reconcile them with the original author before retrying. Legacy
reviews remain revision zero. Current-day budget seeding is conservative and
is performed atomically on first reservation, not reset by this migration.
The new fact tables are append-only except parent-retention cascades.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6c0e4f8b3d1"
down_revision: str | Sequence[str] | None = "f5b9d3e7a2c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVIEW_CHECK = (
    "(status = 'PENDING_REVIEW' AND reviewed_at IS NULL AND reviewed_by_id IS"
    " NULL) OR (status IN ('CONFIRMED', 'REJECTED') AND reviewed_at IS NOT "
    "NULL AND reviewed_by_id IS NOT NULL)"
)


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN\n"
        "        IF EXISTS (SELECT 1 FROM screening_findings WHERE NOT (\n"
        "            (status = 'PENDING_REVIEW' AND reviewed_at IS NULL AND "
        "reviewed_by_id IS NULL)\n"
        "            OR (status IN ('CONFIRMED', 'REJECTED') AND reviewed_at IS "
        "NOT NULL AND reviewed_by_id IS NOT NULL)))\n"
        "        THEN RAISE EXCEPTION 'Clinical preflight: inconsistent screening"
        " review metadata. Export and reconcile original records; no reviewer is "
        "fabricated.'; END IF;\n"
        "        IF EXISTS (SELECT 1 FROM screening_runs r LEFT JOIN "
        "screening_crops c\n"
        "            ON c.farm_id=r.farm_id AND c.image_id=r.image_id AND "
        "c.id=r.crop_id\n"
        "            WHERE r.crop_id IS NOT NULL AND c.id IS NULL)\n"
        "        OR EXISTS (SELECT 1 FROM screening_findings f JOIN "
        "screening_runs r\n"
        "            ON r.farm_id=f.farm_id AND r.id=f.run_id WHERE f.crop_id IS "
        "DISTINCT FROM r.crop_id)\n"
        "        THEN RAISE EXCEPTION 'Clinical preflight: inconsistent "
        "run/image/crop/finding ancestry. Export and reconcile original "
        "provenance before retrying.'; END IF;\n"
        "    END $$"
    )
    op.create_unique_constraint("uq_tasks_farm_id", "tasks", ["farm_id", "id"])
    op.create_unique_constraint(
        "uq_health_events_farm_id_animal", "health_events", ["farm_id", "id", "animal_id"]
    )
    op.create_unique_constraint(
        "uq_screening_crops_farm_image_id", "screening_crops", ["farm_id", "image_id", "id"]
    )
    op.create_foreign_key(
        "fk_screening_runs_image_crop",
        "screening_runs",
        "screening_crops",
        ["farm_id", "image_id", "crop_id"],
        ["farm_id", "image_id", "id"],
        ondelete="CASCADE",
    )
    op.add_column(
        "screening_findings",
        sa.Column("review_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint(
        "ck_screening_findings_review_revision", "screening_findings", "review_revision >= 0"
    )
    op.drop_constraint(
        "ck_screening_findings_review_matches_status", "screening_findings", type_="check"
    )
    op.create_check_constraint(
        "ck_screening_findings_review_matches_status", "screening_findings", REVIEW_CHECK
    )
    for table in ("screening_images", "screening_crops"):
        op.drop_constraint(f"ck_{table}_status", table, type_="check")
        op.create_check_constraint(
            f"ck_{table}_status",
            table,
            (
                "status IN ('PENDING', 'PROCESSING', 'HEALTHY', 'FLAGGED', 'UNASS"
                "ESSABLE', 'SKIPPED', 'ERROR')"
            ),
        )
    op.drop_constraint("ck_screening_runs_verdict_vocabulary", "screening_runs", type_="check")
    op.alter_column(
        "screening_runs",
        "verdict",
        type_=sa.String(20),
        existing_type=sa.String(10),
        existing_nullable=True,
    )
    op.create_check_constraint(
        "ck_screening_runs_verdict_vocabulary",
        "screening_runs",
        "run_status <> 'OK' OR verdict IN ('healthy', 'flagged', 'unassessable')",
    )
    op.execute(
        "CREATE TABLE screening_finding_reviews (\n"
        "\tfinding_id BIGINT NOT NULL, \n"
        "\trevision INTEGER NOT NULL, \n"
        "\tfarm_id INTEGER NOT NULL, \n"
        "\tprevious_status VARCHAR(20) NOT NULL, \n"
        "\tstatus VARCHAR(20) NOT NULL, \n"
        "\treview_note TEXT, \n"
        "\treviewed_by_id INTEGER NOT NULL, \n"
        "\treviewed_at TIMESTAMP WITHOUT TIME ZONE DEFAULT timezone('UTC', now()) "
        "NOT NULL, \n"
        "\tPRIMARY KEY (finding_id, revision), \n"
        "\tCONSTRAINT fk_screening_finding_reviews_farm_finding FOREIGN "
        "KEY(farm_id, finding_id) REFERENCES screening_findings (farm_id, id) ON "
        "DELETE CASCADE, \n"
        "\tCONSTRAINT ck_screening_finding_reviews_revision CHECK (revision > 0),"
        " \n"
        "\tCONSTRAINT ck_screening_finding_reviews_status CHECK (previous_status "
        "IN ('PENDING_REVIEW', 'CONFIRMED', 'REJECTED') AND status IN "
        "('CONFIRMED', 'REJECTED')), \n"
        "\tCONSTRAINT ck_screening_finding_reviews_note_nonblank CHECK "
        "(review_note IS NULL OR btrim(review_note) <> ''), \n"
        "\tFOREIGN KEY(farm_id) REFERENCES farms (id), \n"
        "\tFOREIGN KEY(reviewed_by_id) REFERENCES users (id) ON DELETE RESTRICT\n"
        ")"
    )
    op.execute(
        "CREATE TABLE screening_daily_budgets (\n"
        "\tfarm_id INTEGER NOT NULL, \n"
        "\tlocal_date DATE NOT NULL, \n"
        "\treserved_calls INTEGER DEFAULT 0 NOT NULL, \n"
        "\tPRIMARY KEY (farm_id, local_date), \n"
        "\tCONSTRAINT ck_screening_daily_budgets_nonnegative CHECK (reserved_calls"
        " >= 0), \n"
        "\tFOREIGN KEY(farm_id) REFERENCES farms (id) ON DELETE CASCADE\n"
        ")"
    )
    op.execute(
        "CREATE TABLE screening_call_reservations (\n"
        "\tattempt_id VARCHAR(36) NOT NULL, \n"
        "\tfarm_id INTEGER NOT NULL, \n"
        "\tlocal_date DATE NOT NULL, \n"
        "\tprovider VARCHAR(40) NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT timezone('UTC', now()) "
        "NOT NULL, \n"
        "\tPRIMARY KEY (attempt_id), \n"
        "\tCONSTRAINT fk_screening_call_reservations_budget FOREIGN KEY(farm_id, "
        "local_date) REFERENCES screening_daily_budgets (farm_id, local_date) ON "
        "DELETE CASCADE\n"
        ")"
    )
    op.execute(
        "CREATE INDEX ix_screening_call_reservations_farm_date ON "
        "screening_call_reservations (farm_id, local_date)"
    )
    op.execute(
        "CREATE TABLE health_rounds (\n"
        "\ttask_id INTEGER NOT NULL, \n"
        "\tfarm_id INTEGER NOT NULL, \n"
        "\tsnapshot_at TIMESTAMP WITHOUT TIME ZONE DEFAULT timezone('UTC', now()) "
        "NOT NULL, \n"
        "\trequired_components JSONB NOT NULL, \n"
        "\tPRIMARY KEY (task_id), \n"
        "\tCONSTRAINT uq_health_rounds_farm_task UNIQUE (farm_id, task_id), \n"
        "\tCONSTRAINT fk_health_rounds_farm_task FOREIGN KEY(farm_id, task_id) "
        "REFERENCES tasks (farm_id, id) ON DELETE CASCADE, \n"
        "\tCONSTRAINT ck_health_rounds_components CHECK "
        "(jsonb_typeof(required_components) = 'array' AND "
        "jsonb_array_length(required_components) > 0), \n"
        "\tFOREIGN KEY(farm_id) REFERENCES farms (id)\n"
        ")"
    )
    op.execute(
        "CREATE TABLE health_round_targets (\n"
        "\ttask_id INTEGER NOT NULL, \n"
        "\tanimal_id INTEGER NOT NULL, \n"
        "\tfarm_id INTEGER NOT NULL, \n"
        "\tadded_at TIMESTAMP WITHOUT TIME ZONE DEFAULT timezone('UTC', now()) NOT"
        " NULL, \n"
        "\tadded_by_id INTEGER, \n"
        "\tinclusion_reason TEXT, \n"
        "\tPRIMARY KEY (task_id, animal_id), \n"
        "\tCONSTRAINT uq_health_round_targets_farm_task_animal UNIQUE (farm_id, "
        "task_id, animal_id), \n"
        "\tCONSTRAINT fk_health_round_targets_round FOREIGN KEY(farm_id, task_id) "
        "REFERENCES health_rounds (farm_id, task_id) ON DELETE CASCADE, \n"
        "\tCONSTRAINT fk_health_round_targets_animal FOREIGN KEY(farm_id, "
        "animal_id) REFERENCES animals (farm_id, id), \n"
        "\tCONSTRAINT ck_health_round_targets_addition CHECK ((added_by_id IS NULL"
        " AND inclusion_reason IS NULL) OR (added_by_id IS NOT NULL AND "
        "inclusion_reason IS NOT NULL AND btrim(inclusion_reason) <> '')), \n"
        "\tFOREIGN KEY(added_by_id) REFERENCES users (id) ON DELETE RESTRICT\n"
        ")"
    )
    op.execute(
        "CREATE TABLE health_round_coverage (\n"
        "\ttask_id INTEGER NOT NULL, \n"
        "\tanimal_id INTEGER NOT NULL, \n"
        "\tcomponent VARCHAR(120) NOT NULL, \n"
        "\tfarm_id INTEGER NOT NULL, \n"
        "\thealth_event_id INTEGER NOT NULL, \n"
        "\trecorded_at TIMESTAMP WITHOUT TIME ZONE DEFAULT timezone('UTC', now()) "
        "NOT NULL, \n"
        "\tPRIMARY KEY (task_id, animal_id, component), \n"
        "\tCONSTRAINT fk_health_round_coverage_target FOREIGN KEY(farm_id, "
        "task_id, animal_id) REFERENCES health_round_targets (farm_id, task_id, "
        "animal_id) ON DELETE CASCADE, \n"
        "\tCONSTRAINT fk_health_round_coverage_event FOREIGN KEY(farm_id, "
        "health_event_id, animal_id) REFERENCES health_events (farm_id, id, "
        "animal_id), \n"
        "\tCONSTRAINT ck_health_round_coverage_component CHECK (btrim(component) "
        "<> '')\n"
        ")"
    )
    op.execute(
        "CREATE TABLE health_round_exclusions (\n"
        "\ttask_id INTEGER NOT NULL, \n"
        "\tanimal_id INTEGER NOT NULL, \n"
        "\tfarm_id INTEGER NOT NULL, \n"
        "\treason TEXT NOT NULL, \n"
        "\trecorded_by_id INTEGER NOT NULL, \n"
        "\trecorded_at TIMESTAMP WITHOUT TIME ZONE DEFAULT timezone('UTC', now()) "
        "NOT NULL, \n"
        "\tPRIMARY KEY (task_id, animal_id), \n"
        "\tCONSTRAINT fk_health_round_exclusions_target FOREIGN KEY(farm_id, "
        "task_id, animal_id) REFERENCES health_round_targets (farm_id, task_id, "
        "animal_id) ON DELETE CASCADE, \n"
        "\tCONSTRAINT ck_health_round_exclusions_reason CHECK (btrim(reason) <> "
        "''), \n"
        "\tFOREIGN KEY(recorded_by_id) REFERENCES users (id) ON DELETE RESTRICT\n"
        ")"
    )
    _install_guards()
    _install_coverage_guard()
    # Old in-flight requests lack a durable ledger. Hold the farm's cutover
    # day rather than inventing an unused allowance for those requests.
    op.execute(
        "INSERT INTO screening_daily_budgets(farm_id, local_date, reserved_calls)"
        "\n"
        "        SELECT DISTINCT f.id, (now() AT TIME ZONE f.timezone)::date, "
        "2147483647\n"
        "        FROM farms f JOIN screening_images i ON i.farm_id=f.id\n"
        "        WHERE i.status='PROCESSING' ON CONFLICT DO NOTHING"
    )


def _install_guards() -> None:
    op.execute(
        "CREATE FUNCTION enforce_screening_finding_ancestry() RETURNS trigger "
        "LANGUAGE plpgsql AS $$\n"
        "    DECLARE parent_crop bigint;\n"
        "    BEGIN\n"
        "        SELECT crop_id INTO parent_crop FROM screening_runs WHERE "
        "farm_id=NEW.farm_id AND id=NEW.run_id FOR SHARE;\n"
        "        IF NOT FOUND OR NEW.crop_id IS DISTINCT FROM parent_crop THEN\n"
        "            RAISE EXCEPTION 'Finding crop must exactly match its run "
        "crop' USING ERRCODE='23514';\n"
        "        END IF;\n"
        "        RETURN NEW;\n"
        "    END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_screening_finding_ancestry BEFORE INSERT OR UPDATE OF"
        " farm_id,run_id,crop_id ON screening_findings FOR EACH ROW EXECUTE "
        "FUNCTION enforce_screening_finding_ancestry()"
    )
    op.execute(
        "CREATE FUNCTION screening_run_ancestry_immutable() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN\n"
        "        IF (NEW.farm_id,NEW.image_id,NEW.crop_id) IS DISTINCT FROM "
        "(OLD.farm_id,OLD.image_id,OLD.crop_id) THEN\n"
        "            RAISE EXCEPTION 'Screening run provenance is immutable' "
        "USING ERRCODE='23514';\n"
        "        END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_screening_run_ancestry_immutable BEFORE UPDATE OF "
        "farm_id,image_id,crop_id ON screening_runs FOR EACH ROW EXECUTE FUNCTION"
        " screening_run_ancestry_immutable()"
    )
    op.execute(
        "CREATE FUNCTION screening_review_immutable() RETURNS trigger LANGUAGE "
        "plpgsql AS $$ BEGIN\n"
        "        IF TG_OP='DELETE' AND NOT EXISTS (SELECT 1 FROM "
        "screening_findings WHERE id=OLD.finding_id) THEN RETURN OLD; END IF;\n"
        "        RAISE EXCEPTION 'Screening review history is append-only' USING "
        "ERRCODE='23514'; END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_screening_review_immutable BEFORE UPDATE OR DELETE ON"
        " screening_finding_reviews FOR EACH ROW EXECUTE FUNCTION "
        "screening_review_immutable()"
    )
    op.execute(
        "CREATE FUNCTION health_round_fact_immutable() RETURNS trigger LANGUAGE "
        "plpgsql AS $$ BEGIN\n"
        "        IF TG_OP='DELETE' THEN\n"
        "            IF TG_TABLE_NAME='health_rounds' THEN\n"
        "                IF NOT EXISTS (SELECT 1 FROM tasks WHERE id=OLD.task_id)"
        " THEN RETURN OLD; END IF;\n"
        "            ELSE\n"
        "                IF NOT EXISTS (SELECT 1 FROM health_rounds WHERE "
        "task_id=OLD.task_id) THEN RETURN OLD; END IF;\n"
        "            END IF;\n"
        "        END IF;\n"
        "        RAISE EXCEPTION 'Health-round evidence is append-only' USING "
        "ERRCODE='23514'; END $$"
    )
    for table in (
        "health_rounds",
        "health_round_targets",
        "health_round_coverage",
        "health_round_exclusions",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION health_round_fact_immutable()"
        )


def _install_coverage_guard() -> None:
    op.execute(
        "CREATE FUNCTION health_round_coverage_contract() RETURNS trigger "
        "LANGUAGE plpgsql AS $$\n"
        "    DECLARE declared jsonb; event_type text; task_type text;\n"
        "    BEGIN\n"
        "        SELECT r.required_components,t.category INTO declared,task_type\n"
        "        FROM health_rounds r JOIN tasks t ON t.id=r.task_id AND "
        "t.farm_id=r.farm_id\n"
        "        WHERE r.task_id=NEW.task_id AND r.farm_id=NEW.farm_id FOR SHARE "
        "OF r,t;\n"
        "        SELECT type INTO event_type FROM health_events WHERE "
        "id=NEW.health_event_id\n"
        "            AND farm_id=NEW.farm_id AND animal_id=NEW.animal_id FOR "
        "SHARE;\n"
        "        IF declared IS NULL OR NOT (declared ? NEW.component)\n"
        "            OR task_type NOT IN ('VACCINE','DEWORMING') OR event_type IS"
        " DISTINCT FROM task_type THEN\n"
        "            RAISE EXCEPTION 'Round coverage must match a declared "
        "component and clinical event type' USING ERRCODE='23514';\n"
        "        END IF; RETURN NEW;\n"
        "    END $$"
    )
    op.execute(
        "CREATE TRIGGER trg_health_round_coverage_contract BEFORE INSERT ON "
        "health_round_coverage FOR EACH ROW EXECUTE FUNCTION "
        "health_round_coverage_contract()"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN\n"
        "        IF EXISTS (SELECT 1 FROM screening_finding_reviews) OR EXISTS "
        "(SELECT 1 FROM health_rounds)\n"
        "        OR EXISTS (SELECT 1 FROM screening_call_reservations)\n"
        "        OR EXISTS (SELECT 1 FROM screening_images WHERE "
        "status='UNASSESSABLE')\n"
        "        OR EXISTS (SELECT 1 FROM screening_crops WHERE "
        "status='UNASSESSABLE')\n"
        "        OR EXISTS (SELECT 1 FROM screening_runs WHERE "
        "verdict='unassessable') THEN\n"
        "            RAISE EXCEPTION 'Clinical downgrade would lose new evidence."
        " Archive and reconcile it before a coordinated rollback.';\n"
        "        END IF; END $$"
    )
    op.execute("DROP TRIGGER trg_screening_finding_ancestry ON screening_findings")
    op.alter_column(
        "screening_runs",
        "verdict",
        type_=sa.String(10),
        existing_type=sa.String(20),
        existing_nullable=True,
    )
    op.execute("DROP FUNCTION health_round_coverage_contract() CASCADE")
    op.execute("DROP TRIGGER trg_screening_run_ancestry_immutable ON screening_runs")
    for table in (
        "health_round_exclusions",
        "health_round_coverage",
        "health_round_targets",
        "health_rounds",
        "screening_call_reservations",
        "screening_daily_budgets",
        "screening_finding_reviews",
    ):
        op.drop_table(table)
    for function in (
        "health_round_fact_immutable",
        "screening_review_immutable",
        "screening_run_ancestry_immutable",
        "enforce_screening_finding_ancestry",
    ):
        op.execute(f"DROP FUNCTION {function}()")
    op.drop_constraint("ck_screening_findings_review_revision", "screening_findings", type_="check")
    op.drop_column("screening_findings", "review_revision")
    op.drop_constraint(
        "ck_screening_findings_review_matches_status", "screening_findings", type_="check"
    )
    op.create_check_constraint(
        "ck_screening_findings_review_matches_status",
        "screening_findings",
        (
            "(status = 'PENDING_REVIEW' AND reviewed_at IS NULL) OR (status IN "
            "('CONFIRMED', 'REJECTED') AND reviewed_at IS NOT NULL)"
        ),
    )
    for table in ("screening_images", "screening_crops"):
        op.drop_constraint(f"ck_{table}_status", table, type_="check")
        op.create_check_constraint(
            f"ck_{table}_status",
            table,
            "status IN ('PENDING', 'PROCESSING', 'HEALTHY', 'FLAGGED', 'SKIPPED', 'ERROR')",
        )
    op.drop_constraint("ck_screening_runs_verdict_vocabulary", "screening_runs", type_="check")
    op.create_check_constraint(
        "ck_screening_runs_verdict_vocabulary",
        "screening_runs",
        "run_status <> 'OK' OR verdict IN ('healthy', 'flagged')",
    )
    op.drop_constraint("fk_screening_runs_image_crop", "screening_runs", type_="foreignkey")
    op.drop_constraint("uq_screening_crops_farm_image_id", "screening_crops", type_="unique")
    op.drop_constraint("uq_health_events_farm_id_animal", "health_events", type_="unique")
    op.drop_constraint("uq_tasks_farm_id", "tasks", type_="unique")
