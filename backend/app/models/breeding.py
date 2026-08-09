"""Breeding & kidding."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from .enums import BreedingMethod, BreedingOutcome, KiddingEase, KidStatus

if TYPE_CHECKING:
    from .animals import Animal


class BreedingRecord(Base):
    __tablename__ = "breeding_records"
    # One open (PENDING) breeding per doe at the DB level: every record is
    # born PENDING, so this partial unique index serializes concurrent
    # double-submits that race past create_breeding_record's pre-check. A
    # CONFIRMED_PREGNANT row must NOT be covered here — record_kidding
    # resolves a pregnancy by adding the KiddingRecord row, not by changing
    # the outcome, so covering CONFIRMED would bar a doe from ever being
    # re-bred after her first kidding.
    __table_args__ = (
        UniqueConstraint("farm_id", "id", name="uq_breeding_records_farm_id_id"),
        CheckConstraint(
            "loss_cause IS NULL OR loss_cause IN "
            "('UNKNOWN', 'DISEASE', 'INJURY', 'NUTRITIONAL', 'TRAUMA', "
            "'ANIMAL_STATUS_CHANGE', 'OTHER')",
            name="ck_breeding_loss_cause",
        ),
        CheckConstraint(
            "loss_date IS NULL OR loss_date >= breeding_date",
            name="ck_breeding_loss_after_breeding",
        ),
        CheckConstraint(
            "loss_date IS NULL OR ultrasound_result_date IS NULL "
            "OR loss_date >= ultrasound_result_date",
            name="ck_breeding_loss_after_ultrasound",
        ),
        CheckConstraint(
            "(outcome = 'ABORTED' AND pregnant IS FALSE "
            "AND loss_date IS NOT NULL AND loss_cause IS NOT NULL "
            "AND loss_recorded_at IS NOT NULL) OR "
            "(outcome <> 'ABORTED' AND loss_date IS NULL AND loss_cause IS NULL "
            "AND loss_notes IS NULL AND loss_recorded_by_id IS NULL "
            "AND loss_recorded_at IS NULL)",
            name="ck_breeding_loss_metadata_matches_outcome",
        ),
        CheckConstraint(
            "method IN ('NATURAL')",
            name="ck_breeding_records_method",
        ),
        CheckConstraint(
            "outcome IN ('PENDING', 'CONFIRMED_PREGNANT', 'FAILED', 'ABORTED', 'UNASSESSED')",
            name="ck_breeding_records_outcome",
        ),
        CheckConstraint(
            "heat_cycle_number BETWEEN 1 AND 99",
            name="ck_breeding_records_heat_cycle",
        ),
        CheckConstraint(
            "doe_id <> buck_id",
            name="ck_breeding_records_distinct_parents",
        ),
        CheckConstraint(
            "ultrasound_date IS NULL OR ultrasound_date >= breeding_date",
            name="ck_breeding_records_ultrasound_date",
        ),
        CheckConstraint(
            "ultrasound_result_date IS NULL OR ultrasound_result_date >= breeding_date",
            name="ck_breeding_records_result_date",
        ),
        CheckConstraint(
            "ultrasound_result_date IS NULL OR ultrasound_date IS NULL OR "
            "ultrasound_result_date >= ultrasound_date",
            name="ck_breeding_records_result_after_plan",
        ),
        CheckConstraint(
            "expected_kidding_date IS NULL OR expected_kidding_date > breeding_date",
            name="ck_breeding_records_expected_date",
        ),
        CheckConstraint(
            "kid_count_detected IS NULL OR kid_count_detected BETWEEN 1 AND 3",
            name="ck_breeding_records_kid_count",
        ),
        # UNASSESSED shares PENDING's shape on purpose: closing an unassessable
        # service must not invent a scan that never happened.
        CheckConstraint(
            "(outcome IN ('PENDING', 'UNASSESSED') AND ultrasound_done IS FALSE "
            "AND pregnant IS NULL "
            "AND kid_count_detected IS NULL AND ultrasound_result_date IS NULL) OR "
            "(outcome = 'CONFIRMED_PREGNANT' AND ultrasound_done IS TRUE "
            "AND pregnant IS TRUE) OR "
            "(outcome = 'FAILED' AND ultrasound_done IS TRUE AND pregnant IS FALSE "
            "AND kid_count_detected IS NULL) OR "
            "(outcome = 'ABORTED' AND pregnant IS FALSE)",
            name="ck_breeding_records_outcome_state",
        ),
        CheckConstraint(
            "(outcome = 'CONFIRMED_PREGNANT' AND expected_kidding_date IS NOT NULL) OR "
            "(outcome IN ('PENDING', 'FAILED', 'UNASSESSED') AND expected_kidding_date IS NULL) OR "
            "outcome = 'ABORTED'",
            name="ck_breeding_records_expected_state",
        ),
        ForeignKeyConstraint(
            ["farm_id", "doe_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_breeding_records_farm_doe",
        ),
        ForeignKeyConstraint(
            ["farm_id", "buck_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_breeding_records_farm_buck",
        ),
        Index(
            "uq_breeding_open_pregnancy",
            "doe_id",
            unique=True,
            postgresql_where=text("outcome = 'PENDING'"),
        ),
        Index("ix_breeding_records_breeding_date", "breeding_date"),
        Index("ix_breeding_records_doe_date_id", "doe_id", "breeding_date", "id"),
        Index("ix_breeding_records_expected_kidding_date", "expected_kidding_date"),
        Index("ix_breeding_records_loss_date", "loss_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    doe_id: Mapped[int] = mapped_column(ForeignKey("animals.id"), index=True)
    buck_id: Mapped[int] = mapped_column(ForeignKey("animals.id"), index=True)
    breeding_date: Mapped[date]
    method: Mapped[str] = mapped_column(String(10), default=BreedingMethod.NATURAL.value)
    heat_cycle_number: Mapped[int] = mapped_column(default=1)
    ultrasound_date: Mapped[date | None]  # planned: breeding_date + 32
    ultrasound_result_date: Mapped[date | None]
    ultrasound_done: Mapped[bool] = mapped_column(default=False)
    pregnant: Mapped[bool | None]
    kid_count_detected: Mapped[int | None]  # 1/2/3
    expected_kidding_date: Mapped[date | None]  # breeding_date + 150
    outcome: Mapped[str] = mapped_column(String(20), default=BreedingOutcome.PENDING.value)
    loss_date: Mapped[date | None]
    loss_cause: Mapped[str | None] = mapped_column(String(40))
    loss_notes: Mapped[str | None] = mapped_column(Text)
    loss_recorded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    loss_recorded_at: Mapped[datetime | None]
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    doe: Mapped[Animal] = relationship(
        foreign_keys="BreedingRecord.doe_id", back_populates="breedings_as_doe"
    )
    buck: Mapped[Animal] = relationship(foreign_keys="BreedingRecord.buck_id")
    kidding_record: Mapped[KiddingRecord | None] = relationship(
        back_populates="breeding_record",
        foreign_keys="KiddingRecord.breeding_record_id",
        uselist=False,
    )


class KiddingRecord(Base):
    __tablename__ = "kidding_records"
    # One kidding per pregnancy — backs the router's check-then-act with a
    # real constraint so a concurrent double-submit can't duplicate kids.
    __table_args__ = (
        UniqueConstraint("breeding_record_id", name="uq_kidding_breeding_record"),
        UniqueConstraint("farm_id", "id", name="uq_kidding_records_farm_id_id"),
        CheckConstraint(
            "ease IN ('NORMAL', 'ASSISTED', 'DIFFICULT')",
            name="ck_kidding_records_ease",
        ),
        ForeignKeyConstraint(
            ["farm_id", "doe_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_kidding_records_farm_doe",
        ),
        ForeignKeyConstraint(
            ["farm_id", "breeding_record_id"],
            ["breeding_records.farm_id", "breeding_records.id"],
            name="fk_kidding_records_farm_breeding_record",
        ),
        Index("ix_kidding_records_doe_id", "doe_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    doe_id: Mapped[int] = mapped_column(ForeignKey("animals.id"))
    date: Mapped[date]
    breeding_record_id: Mapped[int | None] = mapped_column(ForeignKey("breeding_records.id"))
    ease: Mapped[str] = mapped_column(String(10), default=KiddingEase.NORMAL.value)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    doe: Mapped[Animal] = relationship(foreign_keys=[doe_id])
    breeding_record: Mapped[BreedingRecord | None] = relationship(
        back_populates="kidding_record", foreign_keys=[breeding_record_id]
    )
    kids: Mapped[list[KidEntry]] = relationship(
        back_populates="kidding_record",
        cascade="all, delete-orphan",
        foreign_keys="KidEntry.kidding_record_id",
    )


class KidEntry(Base):
    __tablename__ = "kid_entries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ALIVE', 'STILLBORN', 'DIED')",
            name="ck_kid_entries_status",
        ),
        CheckConstraint(
            "(status = 'DIED' AND mortality_reported_at IS NOT NULL) OR "
            "(status <> 'DIED' AND mortality_reported_at IS NULL)",
            name="ck_kid_entries_mortality_matches_status",
        ),
        CheckConstraint("sex IN ('M', 'F')", name="ck_kid_entries_sex"),
        CheckConstraint(
            "birth_weight IS NULL OR "
            "(birth_weight BETWEEN 0 AND 1000 AND "
            "birth_weight::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_kid_entries_birth_weight",
        ),
        ForeignKeyConstraint(
            ["farm_id", "kidding_record_id"],
            ["kidding_records.farm_id", "kidding_records.id"],
            name="fk_kid_entries_farm_kidding_record",
        ),
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_kid_entries_farm_animal",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    kidding_record_id: Mapped[int] = mapped_column(ForeignKey("kidding_records.id"), index=True)
    tag: Mapped[str | None] = mapped_column(String(50))
    sex: Mapped[str] = mapped_column(String(1))
    birth_weight: Mapped[float | None]
    status: Mapped[str] = mapped_column(String(10), default=KidStatus.ALIVE.value)
    mortality_reported_at: Mapped[date | None]
    animal_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))  # auto-created Animal

    kidding_record: Mapped[KiddingRecord] = relationship(
        back_populates="kids", foreign_keys=[kidding_record_id]
    )
    animal: Mapped[Animal | None] = relationship(foreign_keys=[animal_id])


Index(
    "ix_breeding_records_farm_confirmed_doe_date_id",
    BreedingRecord.farm_id,
    BreedingRecord.doe_id,
    BreedingRecord.breeding_date.desc(),
    BreedingRecord.id.desc(),
    postgresql_where=text("outcome = 'CONFIRMED_PREGNANT'"),
)
Index(
    "ix_breeding_records_farm_confirmed_due_id",
    BreedingRecord.farm_id,
    BreedingRecord.expected_kidding_date,
    BreedingRecord.id,
    postgresql_include=["doe_id"],
    postgresql_where=text("outcome = 'CONFIRMED_PREGNANT'"),
)
