"""Health."""

from __future__ import annotations

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today, utcnow
from .enums import AdministrationRoute, HealthEventType, sql_in_values

if TYPE_CHECKING:
    from .animals import Animal


class HealthEvent(Base):
    __tablename__ = "health_events"
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="uq_health_events_farm_id_id"),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_health_events_farm_animal",
        ),
        ForeignKeyConstraint(
            ["farm_id", "purchase_batch_id"],
            ["purchase_batches.farm_id", "purchase_batches.id"],
            name="fk_health_events_farm_purchase_batch",
        ),
        CheckConstraint(
            "cost IS NULL OR cost >= 0",
            name="ck_health_events_cost_nonneg",
        ),
        CheckConstraint(
            f"type IN ({sql_in_values(HealthEventType)})",
            name="ck_health_events_type",
        ),
        CheckConstraint(
            f"route IS NULL OR route IN ({sql_in_values(AdministrationRoute)})",
            name="ck_health_events_route",
        ),
        CheckConstraint(
            "animal_id IS NOT NULL OR purchase_batch_id IS NOT NULL",
            name="ck_health_events_target",
        ),
        CheckConstraint(
            "cost IS NULL OR cost <= 1000000000",
            name="ck_health_events_cost_bounded",
        ),
        CheckConstraint(
            "next_due_date IS NULL OR next_due_date > date",
            name="ck_health_events_next_due_after_event",
        ),
        CheckConstraint(
            "next_due_date IS NULL OR "
            "(schedule_template_name IS NOT NULL "
            "AND btrim(schedule_template_name) <> '' "
            "AND next_due_authority IS NOT NULL "
            "AND btrim(next_due_authority) <> '')",
            name="ck_health_events_next_due_provenance",
        ),
        CheckConstraint(
            "schedule_template_id IS NULL OR type IN ('VACCINE', 'DEWORMING')",
            name="ck_health_events_schedule_template_type",
        ),
        CheckConstraint(
            "product_manufactured_on IS NULL OR product_manufactured_on <= date",
            name="ck_health_events_manufactured_before_event",
        ),
        CheckConstraint(
            "product_expires_on IS NULL OR product_expires_on >= date",
            name="ck_health_events_expiry_after_event",
        ),
        CheckConstraint(
            "product_manufactured_on IS NULL OR product_expires_on IS NULL OR "
            "product_expires_on >= product_manufactured_on",
            name="ck_health_events_product_date_order",
        ),
        CheckConstraint(
            "vaccine_valid_until IS NULL OR vaccine_valid_until >= date",
            name="ck_health_events_validity_after_event",
        ),
        CheckConstraint(
            "vaccine_valid_until IS NULL OR product_expires_on IS NULL OR "
            "vaccine_valid_until <= product_expires_on",
            name="ck_health_events_validity_before_expiry",
        ),
        CheckConstraint(
            "withdrawal_until IS NULL OR withdrawal_until >= date",
            name="ck_health_events_withdrawal_after_event",
        ),
        # Health events are immutable through the application and an active
        # withdrawal blocks every sale/cull path.  Keep a mistyped future year
        # from permanently stranding an otherwise marketable animal even when
        # an out-of-process writer bypasses Pydantic validation.
        CheckConstraint(
            "withdrawal_until IS NULL OR withdrawal_until <= date + 730",
            name="ck_health_events_withdrawal_within_max",
        ),
        # Same immutability argument for the schedule side: next_due_date is
        # the anchor the recurring duty is spawned from, so a mistyped far
        # future would pin a farm's schedule — and its generated duties —
        # decades out with no correction path. Ten years dwarfs every seeded
        # cadence (the longest repeat cycle is annual). Schema/validator twin:
        # MAX_NEXT_DUE_DAYS in schemas/health.py.
        CheckConstraint(
            "next_due_date IS NULL OR next_due_date <= date + 3650",
            name="ck_health_events_next_due_bounded",
        ),
        CheckConstraint(
            "suspected_scheduled_disease IS FALSE OR "
            "(disease_target IS NOT NULL AND btrim(disease_target) <> '')",
            name="ck_health_events_suspected_disease",
        ),
        CheckConstraint(
            "suspected_scheduled_disease IS TRUE OR "
            "(authority_notified_at IS NULL AND isolation_started_at IS NULL)",
            name="ck_health_events_compliance_requires_suspicion",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # ITEM 9 (2026-09-21 playbook): closes the backdating blind spot — same-day
    # entry and a backdated event are now distinguishable.
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=text("timezone('UTC', now())")
    )
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    animal_id: Mapped[int | None] = mapped_column(
        ForeignKey("animals.id"), index=True
    )  # null = batch event
    purchase_batch_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_batches.id"))
    date: Mapped[dt.date] = mapped_column(default=today)
    type: Mapped[str] = mapped_column(String(12))  # HealthEventType enum
    product_name: Mapped[str | None] = mapped_column(String(120))
    disease_target: Mapped[str | None] = mapped_column(String(120))
    dose: Mapped[str | None] = mapped_column(String(60))
    route: Mapped[str | None] = mapped_column(String(20))  # AdministrationRoute enum
    vet_name: Mapped[str | None] = mapped_column(String(120))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    next_due_date: Mapped[dt.date | None]
    # Exact schedule/template linkage prevents a free-text product name from
    # silently satisfying an unrelated scheduled task.
    schedule_template_name: Mapped[str | None] = mapped_column(String(120))
    schedule_template_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "vaccine_templates.id",
            name="fk_health_events_schedule_template",
        )
    )
    next_due_authority: Mapped[str | None] = mapped_column(String(120))
    product_lot: Mapped[str | None] = mapped_column(String(120))
    product_manufactured_on: Mapped[dt.date | None] = mapped_column(Date)
    product_expires_on: Mapped[dt.date | None] = mapped_column(Date)
    vaccine_valid_until: Mapped[dt.date | None] = mapped_column(Date)
    certificate_number: Mapped[str | None] = mapped_column(String(120))
    official_tag_number: Mapped[str | None] = mapped_column(String(80))
    administered_by: Mapped[str | None] = mapped_column(String(120))
    withdrawal_until: Mapped[dt.date | None] = mapped_column(Date)
    suspected_scheduled_disease: Mapped[bool] = mapped_column(Boolean, default=False)
    authority_notified_at: Mapped[dt.date | None] = mapped_column(Date)
    isolation_started_at: Mapped[dt.date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    animal: Mapped[Animal | None] = relationship(foreign_keys=[animal_id])


Index(
    "ix_health_events_animal_template_latest",
    HealthEvent.animal_id,
    HealthEvent.schedule_template_id,
    HealthEvent.date.desc(),
    HealthEvent.id.desc(),
)
# The farm-wide events feed pages with ORDER BY date DESC, id DESC after a
# farm_id equality filter; the plain date index cannot serve that ordering
# tenant-bounded, so every page sorted the farm's whole history.
Index(
    "ix_health_events_farm_date_id",
    HealthEvent.farm_id,
    HealthEvent.date.desc(),
    HealthEvent.id.desc(),
)


class MovementRestrictionAction(Base):
    """Immutable placement/clearance fact for one restriction episode."""

    __tablename__ = "movement_restriction_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_movement_restriction_actions_farm_animal",
        ),
        ForeignKeyConstraint(
            ["farm_id", "health_event_id"],
            ["health_events.farm_id", "health_events.id"],
            name="fk_movement_restriction_actions_farm_health_event",
        ),
        UniqueConstraint(
            "farm_id",
            "animal_id",
            "restriction_version",
            "action",
            name="uq_movement_restriction_action_episode",
        ),
        CheckConstraint(
            "restriction_version >= 1",
            name="ck_movement_restriction_actions_version",
        ),
        CheckConstraint(
            "action IN ('PLACED', 'CLEARED')",
            name="ck_movement_restriction_actions_action",
        ),
        CheckConstraint(
            "btrim(action_reference) <> ''",
            name="ck_movement_restriction_actions_reference",
        ),
        CheckConstraint(
            "action <> 'PLACED' OR (disease_target IS NOT NULL AND btrim(disease_target) <> '')",
            name="ck_movement_restriction_actions_placement_disease",
        ),
        Index(
            "ix_movement_restriction_actions_animal_version",
            "animal_id",
            "restriction_version",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    animal_id: Mapped[int] = mapped_column(ForeignKey("animals.id"), index=True)
    restriction_version: Mapped[int]
    action: Mapped[str] = mapped_column(String(10))
    acted_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    acted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action_reference: Mapped[str] = mapped_column(String(255))
    disease_target: Mapped[str | None] = mapped_column(String(120))
    health_event_id: Mapped[int | None] = mapped_column(ForeignKey("health_events.id"))


class VaccineTemplate(Base):
    """Seeded reference data: vaccination schedule templates."""

    __tablename__ = "vaccine_templates"
    __table_args__ = (
        UniqueConstraint("name", name="uq_vaccine_templates_name"),
        CheckConstraint(
            "first_dose_age_months IS NULL OR "
            "(first_dose_age_months > 0 AND first_dose_age_months <= 240 AND "
            "first_dose_age_months::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_vaccine_templates_first_age",
        ),
        CheckConstraint(
            "booster_weeks IS NULL OR "
            "(booster_weeks > 0 AND booster_weeks <= 520 AND "
            "booster_weeks::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_vaccine_templates_booster",
        ),
        CheckConstraint(
            "repeat_months IS NULL OR "
            "(repeat_months > 0 AND repeat_months <= 240 AND "
            "repeat_months::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_vaccine_templates_repeat",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    # Exact numerics (never float8, per the exact-numerics program): the
    # cadences are converted to due dates with Decimal arithmetic, so a
    # drifted 3.999999-week booster would land duties on the wrong day.
    # ``asdecimal=False`` keeps the established float ORM/API contract.
    first_dose_age_months: Mapped[float | None] = mapped_column(
        Numeric(8, 2, asdecimal=False)
    )  # null = pregnancy-linked
    booster_weeks: Mapped[float | None] = mapped_column(Numeric(8, 2, asdecimal=False))
    repeat_months: Mapped[float | None] = mapped_column(Numeric(8, 2, asdecimal=False))
    timing_note: Mapped[str | None] = mapped_column(String(255))
