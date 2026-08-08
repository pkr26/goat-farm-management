"""Animals: herd, weight records, bucket moves, bucket reference tables."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import DEFAULT_BUSINESS_TIMEZONE, business_date, today, utcnow
from .constants import BREEDING_READY_BUCKETS, MIN_BREEDING_AGE_MONTHS, MIN_BREEDING_WEIGHT_KG
from .enums import AnimalStatus, BreedingOutcome, Sex

if TYPE_CHECKING:
    from .breeding import BreedingRecord
    from .core import Farm
    from .purchases import PurchaseBatch


class Animal(Base):
    __tablename__ = "animals"
    __table_args__ = (
        UniqueConstraint("farm_id", "tag_number", name="uq_animal_tag_per_farm"),
        Index(
            "ix_animals_farm_active",
            "farm_id",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        CheckConstraint(
            "birth_weight IS NULL OR birth_weight >= 0",
            name="ck_animals_birth_weight_nonneg",
        ),
        CheckConstraint(
            "purchase_price IS NULL OR purchase_price >= 0",
            name="ck_animals_purchase_price_nonneg",
        ),
        CheckConstraint(
            "sale_price IS NULL OR sale_price >= 0",
            name="ck_animals_sale_price_nonneg",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    tag_number: Mapped[str] = mapped_column(String(50))
    name: Mapped[str | None] = mapped_column(String(80))
    breed: Mapped[str] = mapped_column(String(80), default="Osmanabadi")
    sex: Mapped[str] = mapped_column(String(1))  # Sex enum
    date_of_birth: Mapped[date | None]
    estimated_dob: Mapped[date | None]
    birth_type: Mapped[str | None] = mapped_column(String(10))  # BirthType enum
    source: Mapped[str] = mapped_column(String(12))  # AnimalSource enum

    # Purchased animals
    purchase_date: Mapped[date | None]
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    seller_name: Mapped[str | None] = mapped_column(String(120))
    purchase_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_batches.id"), index=True
    )

    # Born animals
    dam_id: Mapped[int | None] = mapped_column(
        ForeignKey("animals.id", ondelete="SET NULL"), index=True
    )
    sire_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id", ondelete="SET NULL"))
    birth_weight: Mapped[float | None]

    current_bucket: Mapped[str] = mapped_column(String(20))  # Bucket enum
    status: Mapped[str] = mapped_column(String(10), default=AnimalStatus.ACTIVE.value)
    status_date: Mapped[date | None]  # when sold/dead/culled
    status_notes: Mapped[str | None] = mapped_column(String(255))
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    buyer_name: Mapped[str | None] = mapped_column(String(120))
    cull_candidate: Mapped[bool] = mapped_column(default=False)
    # A movement hold is deliberately a factual operational state, not a
    # diagnosis or treatment instruction.  It lets task and sale/release
    # workflows fail closed when a farm has recorded a restriction or a
    # possible scheduled disease pending local-veterinary/AHD direction.
    movement_restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    restriction_reason: Mapped[str | None] = mapped_column(String(255))
    suspected_scheduled_disease: Mapped[bool] = mapped_column(Boolean, default=False)
    suspected_disease: Mapped[str | None] = mapped_column(String(120))
    authority_notified_at: Mapped[date | None] = mapped_column(Date)
    # Most recent documented authority/veterinary clearance. The current
    # booleans above remain authoritative; retaining the last clearance even
    # if a later hold is placed preserves useful operational history.
    restriction_cleared_at: Mapped[datetime | None]
    restriction_cleared_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    restriction_clearance_reference: Mapped[str | None] = mapped_column(String(255))
    mortality_cause: Mapped[str | None] = mapped_column(String(120))
    mortality_reported_at: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    farm: Mapped[Farm] = relationship(back_populates="animals")
    purchase_batch: Mapped[PurchaseBatch | None] = relationship(back_populates="animals")
    # History collections are NOT eager-loaded at the mapper level:
    # a mapper-level lazy="selectin" would fire on every Animal load — including
    # db.get() for simple writes. Read paths that serialize AnimalOut's computed
    # fields (latest_weight_kg, days_in_current_bucket, is_currently_pregnant,
    # is_breeding_ready) opt in with services.ANIMAL_OUT_LOADS instead.
    weight_records: Mapped[list[WeightRecord]] = relationship(
        back_populates="animal",
        order_by="WeightRecord.date",
        cascade="all, delete-orphan",
    )
    bucket_moves: Mapped[list[BucketMove]] = relationship(
        back_populates="animal",
        order_by="BucketMove.moved_at",
        cascade="all, delete-orphan",
    )
    breedings_as_doe: Mapped[list[BreedingRecord]] = relationship(
        back_populates="doe", foreign_keys="BreedingRecord.doe_id"
    )

    # -- computed helpers ---------------------------------------------------
    @property
    def effective_dob(self) -> date | None:
        return self.date_of_birth or self.estimated_dob

    @property
    def age_months(self) -> int | None:
        """Age in whole months from DOB (or estimated DOB); None if unknown."""
        return self.age_months_on(today())

    def age_months_on(self, reference_date: date) -> int | None:
        """Age in whole months on an explicit farm-local business date."""
        dob = self.effective_dob
        if dob is None:
            return None
        months = (reference_date.year - dob.year) * 12 + (reference_date.month - dob.month)
        if reference_date.day < dob.day:
            months -= 1
        return max(months, 0)

    @property
    def latest_weight(self) -> WeightRecord | None:
        if not self.weight_records:
            return None
        return max(self.weight_records, key=lambda w: (w.date, w.id or 0))

    @property
    def latest_weight_kg(self) -> float | None:
        rec = self.latest_weight
        return rec.weight_kg if rec else self.birth_weight

    @property
    def last_bucket_move(self) -> BucketMove | None:
        if not self.bucket_moves:
            return None
        return max(self.bucket_moves, key=lambda m: (m.moved_at, m.id or 0))

    @property
    def days_in_current_bucket(self) -> int:
        return self.days_in_current_bucket_on(today())

    def days_in_current_bucket_on(
        self,
        reference_date: date,
        timezone_name: str = DEFAULT_BUSINESS_TIMEZONE,
    ) -> int:
        """Elapsed bucket days on an explicit farm-local business date."""
        move = self.last_bucket_move
        ref: datetime | date | None = move.moved_at if move else self.created_at
        if ref is None:
            return 0
        moved_date = business_date(ref, timezone_name) if isinstance(ref, datetime) else ref
        return max((reference_date - moved_date).days, 0)

    @property
    def is_currently_pregnant(self) -> bool:
        for br in self.breedings_as_doe:
            if br.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value and not br.kidding_record:
                return True
        return False

    @property
    def is_breeding_ready(self) -> bool:
        """Breeding-ready doe: female, ACTIVE, >=10 mo, >=22 kg, not pregnant,
        living in FOUNDATION / FEMALE_KIDS / RESTING (per SPEC)."""
        return self.is_breeding_ready_on(today())

    def is_breeding_ready_on(self, reference_date: date) -> bool:
        """Breeding readiness on an explicit farm-local business date."""
        if self.sex != Sex.F.value or self.status != AnimalStatus.ACTIVE.value:
            return False
        if self.movement_restricted or self.suspected_scheduled_disease:
            return False
        if self.current_bucket not in [b.value for b in BREEDING_READY_BUCKETS]:
            return False
        age = self.age_months_on(reference_date)
        if age is None or age < MIN_BREEDING_AGE_MONTHS:
            return False
        weight = self.latest_weight_kg
        if weight is None or weight < MIN_BREEDING_WEIGHT_KG:
            return False
        if self.is_currently_pregnant:
            return False
        return True

    @property
    def is_breeding_eligible(self) -> bool:
        """Canonical doe eligibility for both first service and re-service.

        A doe already parked in BREEDING remains eligible after a failed cycle,
        but she does not get to bypass the same age, weight, active-status and
        non-pregnancy safeguards used for a first service.
        """
        return self.is_breeding_eligible_on(today())

    def is_breeding_eligible_on(self, reference_date: date) -> bool:
        """First-service/re-service eligibility on a farm-local date."""
        if self.sex != Sex.F.value or self.status != AnimalStatus.ACTIVE.value:
            return False
        if self.movement_restricted or self.suspected_scheduled_disease:
            return False
        if self.current_bucket not in [*BREEDING_READY_BUCKETS, "BREEDING"]:
            return False
        age = self.age_months_on(reference_date)
        weight = self.latest_weight_kg
        return bool(
            age is not None
            and age >= MIN_BREEDING_AGE_MONTHS
            and weight is not None
            and weight >= MIN_BREEDING_WEIGHT_KG
            and not self.is_currently_pregnant
        )

    @property
    def is_buck_eligible(self) -> bool:
        """Minimal canonical buck guard without inventing a clinical threshold."""
        return bool(
            self.sex == Sex.M.value
            and self.status == AnimalStatus.ACTIVE.value
            and self.current_bucket
            not in {"QUARANTINE", "PREGNANCY_EARLY", "PREGNANCY_LATE", "DELIVERY"}
            and not self.movement_restricted
            and not self.suspected_scheduled_disease
        )

    @property
    def display_name(self) -> str:
        return f"{self.tag_number}" + (f" · {self.name}" if self.name else "")


class WeightRecord(Base):
    __tablename__ = "weight_records"
    __table_args__ = (
        Index("ix_weight_records_date", "date"),
        CheckConstraint("weight_kg > 0", name="ck_weight_records_weight_positive"),
        CheckConstraint(
            "bcs IS NULL OR bcs BETWEEN 1 AND 5",
            name="ck_weight_records_bcs_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    animal_id: Mapped[int] = mapped_column(ForeignKey("animals.id"), index=True)
    date: Mapped[date] = mapped_column(default=today)
    weight_kg: Mapped[float]
    bcs: Mapped[int | None]  # body condition score 1–5
    notes: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    animal: Mapped[Animal] = relationship(back_populates="weight_records")


class BucketMove(Base):
    __tablename__ = "bucket_moves"

    id: Mapped[int] = mapped_column(primary_key=True)
    animal_id: Mapped[int] = mapped_column(ForeignKey("animals.id"), index=True)
    from_bucket: Mapped[str | None] = mapped_column(String(20))  # None = initial placement
    to_bucket: Mapped[str] = mapped_column(String(20))
    moved_at: Mapped[datetime] = mapped_column(default=utcnow)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    animal: Mapped[Animal] = relationship(back_populates="bucket_moves")


# ---------------------------------------------------------------------------
# Reference: bucket definitions (seeded)
# ---------------------------------------------------------------------------
class BucketDefinition(Base):
    __tablename__ = "bucket_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    who: Mapped[str | None] = mapped_column(String(255))
    exit_rule: Mapped[str | None] = mapped_column(String(255))
    daily_kg_per_head: Mapped[float] = mapped_column(default=1.2)  # feeding plan setting
    sort_order: Mapped[int] = mapped_column(default=0)


class BucketFeedSetting(Base):
    """Per-farm override of BucketDefinition.daily_kg_per_head."""

    __tablename__ = "bucket_feed_settings"
    __table_args__ = (UniqueConstraint("farm_id", "bucket", name="uq_feed_setting_per_bucket"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    bucket: Mapped[str] = mapped_column(String(20))
    daily_kg_per_head: Mapped[float]
