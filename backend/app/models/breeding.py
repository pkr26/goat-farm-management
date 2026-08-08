"""Breeding & kidding."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
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
        Index(
            "uq_breeding_open_pregnancy",
            "doe_id",
            unique=True,
            postgresql_where=text("outcome = 'PENDING'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    doe_id: Mapped[int] = mapped_column(ForeignKey("animals.id"), index=True)
    buck_id: Mapped[int] = mapped_column(ForeignKey("animals.id"), index=True)
    breeding_date: Mapped[date]
    method: Mapped[str] = mapped_column(String(10), default=BreedingMethod.NATURAL.value)
    heat_cycle_number: Mapped[int] = mapped_column(default=1)
    ultrasound_date: Mapped[date | None]  # planned: breeding_date + 32
    ultrasound_done: Mapped[bool] = mapped_column(default=False)
    pregnant: Mapped[bool | None]
    kid_count_detected: Mapped[int | None]  # 1/2/3
    expected_kidding_date: Mapped[date | None]  # breeding_date + 150
    outcome: Mapped[str] = mapped_column(String(20), default=BreedingOutcome.PENDING.value)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    doe: Mapped[Animal] = relationship(
        foreign_keys="BreedingRecord.doe_id", back_populates="breedings_as_doe"
    )
    buck: Mapped[Animal] = relationship(foreign_keys="BreedingRecord.buck_id")
    kidding_record: Mapped[KiddingRecord | None] = relationship(
        back_populates="breeding_record", uselist=False
    )


class KiddingRecord(Base):
    __tablename__ = "kidding_records"
    # One kidding per pregnancy — backs the router's check-then-act with a
    # real constraint so a concurrent double-submit can't duplicate kids.
    __table_args__ = (UniqueConstraint("breeding_record_id", name="uq_kidding_breeding_record"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    doe_id: Mapped[int] = mapped_column(ForeignKey("animals.id"))
    date: Mapped[date]
    breeding_record_id: Mapped[int | None] = mapped_column(ForeignKey("breeding_records.id"))
    ease: Mapped[str] = mapped_column(String(10), default=KiddingEase.NORMAL.value)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    doe: Mapped[Animal] = relationship()
    breeding_record: Mapped[BreedingRecord | None] = relationship(back_populates="kidding_record")
    kids: Mapped[list[KidEntry]] = relationship(
        back_populates="kidding_record", cascade="all, delete-orphan"
    )


class KidEntry(Base):
    __tablename__ = "kid_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    kidding_record_id: Mapped[int] = mapped_column(ForeignKey("kidding_records.id"), index=True)
    tag: Mapped[str | None] = mapped_column(String(50))
    sex: Mapped[str] = mapped_column(String(1))
    birth_weight: Mapped[float | None]
    status: Mapped[str] = mapped_column(String(10), default=KidStatus.ALIVE.value)
    animal_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))  # auto-created Animal

    kidding_record: Mapped[KiddingRecord] = relationship(back_populates="kids")
    animal: Mapped[Animal | None] = relationship()
