"""Animals: herd, weight records, bucket moves, bucket reference tables."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..utils import today, utcnow
from .constants import BREEDING_READY_BUCKETS, MIN_BREEDING_AGE_MONTHS, MIN_BREEDING_WEIGHT_KG
from .enums import AnimalStatus, BreedingOutcome, Sex

if TYPE_CHECKING:
    from .breeding import BreedingRecord
    from .core import Farm
    from .purchases import PurchaseBatch


class Animal(Base):
    __tablename__ = "animals"
    __table_args__ = (UniqueConstraint("farm_id", "tag_number", name="uq_animal_tag_per_farm"),)

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
    purchase_price: Mapped[float | None]
    seller_name: Mapped[str | None] = mapped_column(String(120))
    purchase_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_batches.id"), index=True
    )

    # Born animals
    dam_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"), index=True)
    sire_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))
    birth_weight: Mapped[float | None]

    current_bucket: Mapped[str] = mapped_column(String(20))  # Bucket enum
    status: Mapped[str] = mapped_column(String(10), default=AnimalStatus.ACTIVE.value)
    status_date: Mapped[date | None]  # when sold/dead/culled
    status_notes: Mapped[str | None] = mapped_column(String(255))
    sale_price: Mapped[float | None]
    buyer_name: Mapped[str | None] = mapped_column(String(120))
    cull_candidate: Mapped[bool] = mapped_column(default=False)
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
        dob = self.effective_dob
        if dob is None:
            return None
        ref = today()
        months = (ref.year - dob.year) * 12 + (ref.month - dob.month)
        if ref.day < dob.day:
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
        move = self.last_bucket_move
        ref: datetime | date | None = move.moved_at if move else self.created_at
        if ref is None:
            return 0
        moved_date = ref.date() if isinstance(ref, datetime) else ref
        return max((today() - moved_date).days, 0)

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
        if self.sex != Sex.F.value or self.status != AnimalStatus.ACTIVE.value:
            return False
        if self.current_bucket not in [b.value for b in BREEDING_READY_BUCKETS]:
            return False
        age = self.age_months
        if age is None or age < MIN_BREEDING_AGE_MONTHS:
            return False
        weight = self.latest_weight_kg
        if weight is None or weight < MIN_BREEDING_WEIGHT_KG:
            return False
        if self.is_currently_pregnant:
            return False
        return True

    @property
    def display_name(self) -> str:
        return f"{self.tag_number}" + (f" · {self.name}" if self.name else "")


class WeightRecord(Base):
    __tablename__ = "weight_records"

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
