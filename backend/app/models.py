"""Full data model (Phases 1–3). All entities defined up-front per SPEC."""

# Ported 1:1 from the v1 models module: str/Enum mix-ins (not StrEnum) and the
# original punctuation in docstrings/comments/protocol strings are kept
# byte-identical on purpose.
# ruff: noqa: UP042

from __future__ import annotations

import datetime as dt
import enum
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils import today, utcnow

# ---------------------------------------------------------------------------
# Breed constants (Osmanabadi, per SPEC)
# ---------------------------------------------------------------------------
GESTATION_DAYS = 150
KIDDING_WINDOW_DAYS = (145, 155)
ULTRASOUND_AFTER_BREEDING_DAYS = 32
MIN_BREEDING_AGE_MONTHS = 10
MIN_BREEDING_WEIGHT_KG = 22.0
WEANING_DAYS = 60
BUCK_ROTATION_DAYS = 7
BUCK_DOE_RATIO = 20
MEAT_SALE_AGE_MONTHS = (8, 9)
MEAT_SALE_WEIGHT_KG = (24.0, 28.0)
MAX_FAILED_CYCLES_BEFORE_CULL = 2


# ---------------------------------------------------------------------------
# String enums (values stored verbatim in SQLite)
# ---------------------------------------------------------------------------
class Bucket(str, enum.Enum):
    QUARANTINE = "QUARANTINE"
    FOUNDATION = "FOUNDATION"
    BREEDING = "BREEDING"
    PREGNANCY_EARLY = "PREGNANCY_EARLY"
    PREGNANCY_LATE = "PREGNANCY_LATE"
    DELIVERY = "DELIVERY"
    RECOVERY = "RECOVERY"
    RESTING = "RESTING"
    MALE_KIDS = "MALE_KIDS"
    FEMALE_KIDS = "FEMALE_KIDS"


class AnimalStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SOLD = "SOLD"
    DEAD = "DEAD"
    CULLED = "CULLED"


class AnimalSource(str, enum.Enum):
    BORN = "BORN"
    PURCHASED = "PURCHASED"


class Sex(str, enum.Enum):
    M = "M"
    F = "F"


class BirthType(str, enum.Enum):
    SINGLE = "SINGLE"
    TWIN = "TWIN"
    TRIPLET = "TRIPLET"


class BreedingMethod(str, enum.Enum):
    NATURAL = "NATURAL"


class BreedingOutcome(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED_PREGNANT = "CONFIRMED_PREGNANT"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class KiddingEase(str, enum.Enum):
    NORMAL = "NORMAL"
    ASSISTED = "ASSISTED"
    DIFFICULT = "DIFFICULT"


class KidStatus(str, enum.Enum):
    ALIVE = "ALIVE"
    STILLBORN = "STILLBORN"
    DIED = "DIED"


class HealthEventType(str, enum.Enum):
    VACCINE = "VACCINE"
    DEWORMING = "DEWORMING"
    TREATMENT = "TREATMENT"
    FOOTBATH = "FOOTBATH"
    VITAMIN = "VITAMIN"


class IngredientCategory(str, enum.Enum):
    ROUGHAGE_WET = "ROUGHAGE_WET"
    ROUGHAGE_DRY = "ROUGHAGE_DRY"
    CONCENTRATE = "CONCENTRATE"


class FeedingShift(str, enum.Enum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    NIGHT = "NIGHT"


class TransactionType(str, enum.Enum):
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class TransactionCategory(str, enum.Enum):
    ANIMAL_SALE = "ANIMAL_SALE"
    ANIMAL_PURCHASE = "ANIMAL_PURCHASE"
    FEED = "FEED"
    MEDICINE = "MEDICINE"
    VET = "VET"
    LABOUR = "LABOUR"
    EQUIPMENT = "EQUIPMENT"
    MILK = "MILK"
    MANURE = "MANURE"
    OTHER = "OTHER"


class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    VERIFIED = "VERIFIED"  # DONE + confirmed by a verifying role (e.g. Cleaner Manager)


class TaskCategory(str, enum.Enum):
    VACCINE = "VACCINE"
    DEWORMING = "DEWORMING"
    ULTRASOUND = "ULTRASOUND"
    KIDDING_DUE = "KIDDING_DUE"
    WEANING = "WEANING"
    BUCKET_MOVE = "BUCKET_MOVE"
    QUARANTINE = "QUARANTINE"
    FEED = "FEED"
    CLEANING = "CLEANING"
    OTHER = "OTHER"


# Task categories whose DONE state means "awaiting verification" by a role
# holding tasks.verify (e.g. cleaner marks done → cleaner manager verifies).
VERIFICATION_REQUIRED_CATEGORIES = (TaskCategory.CLEANING.value,)


# Buckets where a doe is eligible to become breeding-ready (per SPEC).
BREEDING_READY_BUCKETS = (Bucket.FOUNDATION, Bucket.FEMALE_KIDS, Bucket.RESTING)

# Feeding schedule split: 40% 6:30 AM / 20% 1:30 PM / 40% 7:30 PM.
SHIFT_SPLIT = {
    FeedingShift.MORNING: 0.40,
    FeedingShift.AFTERNOON: 0.20,
    FeedingShift.NIGHT: 0.40,
}


# ---------------------------------------------------------------------------
# Multi-tenancy
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    farms: Mapped[list[Farm]] = relationship(back_populates="owner")
    memberships: Mapped[list[FarmMembership]] = relationship(back_populates="user")

    @property
    def display_name(self) -> str:
        return self.name or self.email


class Farm(Base):
    __tablename__ = "farms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(120))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    owner: Mapped[User] = relationship(back_populates="farms")
    animals: Mapped[list[Animal]] = relationship(back_populates="farm")
    roles: Mapped[list[Role]] = relationship(back_populates="farm", cascade="all, delete-orphan")
    memberships: Mapped[list[FarmMembership]] = relationship(
        back_populates="farm", cascade="all, delete-orphan"
    )


class Role(Base):
    """Farm-scoped role: a named bundle of permission codes (JSON list).

    Preset roles carry a stable `code` (MOVER, VET, ...) used to map
    auto-generated tasks to a default assignee; custom roles have code=None.
    """

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("farm_id", "name", name="uq_role_name_per_farm"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    code: Mapped[str | None] = mapped_column(String(30))  # preset key; null for custom roles
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(255))
    permissions: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of codes
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    farm: Mapped[Farm] = relationship(back_populates="roles")
    memberships: Mapped[list[FarmMembership]] = relationship(back_populates="role")

    def permission_set(self) -> set[str]:
        import json

        try:
            return {p for p in json.loads(self.permissions or "[]") if isinstance(p, str)}
        except ValueError:
            return set()


class FarmMembership(Base):
    """Links a User to a Farm as a worker with a Role. The farm owner is not
    a membership — ownership (Farm.owner_id) implies all permissions."""

    __tablename__ = "farm_memberships"
    __table_args__ = (UniqueConstraint("user_id", "farm_id", name="uq_membership_user_farm"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(back_populates="memberships")
    farm: Mapped[Farm] = relationship(back_populates="memberships")
    role: Mapped[Role] = relationship(back_populates="memberships")


# ---------------------------------------------------------------------------
# Animals
# ---------------------------------------------------------------------------
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
    purchase_batch_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_batches.id"))

    # Born animals
    dam_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))
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
    weight_records: Mapped[list[WeightRecord]] = relationship(
        back_populates="animal",
        order_by="WeightRecord.date",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    bucket_moves: Mapped[list[BucketMove]] = relationship(
        back_populates="animal",
        order_by="BucketMove.moved_at",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    breedings_as_doe: Mapped[list[BreedingRecord]] = relationship(
        back_populates="doe", foreign_keys="BreedingRecord.doe_id", lazy="selectin"
    )
    kidding_records: Mapped[list[KiddingRecord]] = relationship(back_populates="doe")

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
# Purchases
# ---------------------------------------------------------------------------
class PurchaseBatch(Base):
    __tablename__ = "purchase_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    date: Mapped[date] = mapped_column(default=today)
    supplier: Mapped[str | None] = mapped_column(String(120))
    count: Mapped[int]
    avg_age_months: Mapped[float | None]
    avg_weight_kg: Mapped[float | None]
    total_price: Mapped[float | None]
    notes: Mapped[str | None] = mapped_column(Text)

    animals: Mapped[list[Animal]] = relationship(back_populates="purchase_batch")


# ---------------------------------------------------------------------------
# Breeding & kidding
# ---------------------------------------------------------------------------
class BreedingRecord(Base):
    __tablename__ = "breeding_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    doe_id: Mapped[int] = mapped_column(ForeignKey("animals.id"))
    buck_id: Mapped[int] = mapped_column(ForeignKey("animals.id"))
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
        back_populates="breeding_record", uselist=False, lazy="selectin"
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

    doe: Mapped[Animal] = relationship(back_populates="kidding_records")
    breeding_record: Mapped[BreedingRecord | None] = relationship(back_populates="kidding_record")
    kids: Mapped[list[KidEntry]] = relationship(
        back_populates="kidding_record", cascade="all, delete-orphan"
    )


class KidEntry(Base):
    __tablename__ = "kid_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    kidding_record_id: Mapped[int] = mapped_column(ForeignKey("kidding_records.id"))
    tag: Mapped[str | None] = mapped_column(String(50))
    sex: Mapped[str] = mapped_column(String(1))
    birth_weight: Mapped[float | None]
    status: Mapped[str] = mapped_column(String(10), default=KidStatus.ALIVE.value)
    animal_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))  # auto-created Animal

    kidding_record: Mapped[KiddingRecord] = relationship(back_populates="kids")
    animal: Mapped[Animal | None] = relationship()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class HealthEvent(Base):
    __tablename__ = "health_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    animal_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))  # null = batch event
    purchase_batch_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_batches.id"))
    date: Mapped[date] = mapped_column(default=today)
    type: Mapped[str] = mapped_column(String(12))  # HealthEventType enum
    product_name: Mapped[str | None] = mapped_column(String(120))
    disease_target: Mapped[str | None] = mapped_column(String(120))
    dose: Mapped[str | None] = mapped_column(String(60))
    route: Mapped[str | None] = mapped_column(String(20))  # SC / Oral / IM
    vet_name: Mapped[str | None] = mapped_column(String(120))
    cost: Mapped[float | None]
    next_due_date: Mapped[dt.date | None]
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    animal: Mapped[Animal | None] = relationship()


class VaccineTemplate(Base):
    """Seeded reference data: vaccination schedule templates."""

    __tablename__ = "vaccine_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    first_dose_age_months: Mapped[float | None]  # null = pregnancy-linked
    booster_weeks: Mapped[float | None]
    repeat_months: Mapped[float | None]
    timing_note: Mapped[str | None] = mapped_column(String(255))


# ---------------------------------------------------------------------------
# Feeding
# ---------------------------------------------------------------------------
class FeedRecipe(Base):
    __tablename__ = "feed_recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(255))

    lines: Mapped[list[FeedRecipeLine]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )


class FeedRecipeLine(Base):
    __tablename__ = "feed_recipe_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("feed_recipes.id"))
    ingredient: Mapped[str] = mapped_column(String(120))
    kg_per_100kg: Mapped[float]
    category: Mapped[str] = mapped_column(String(20))  # IngredientCategory enum

    recipe: Mapped[FeedRecipe] = relationship(back_populates="lines")


class FeedInventory(Base):
    __tablename__ = "feed_inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    ingredient: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20))  # IngredientCategory enum
    unit: Mapped[str] = mapped_column(String(10), default="kg")
    qty_on_hand: Mapped[float] = mapped_column(default=0.0)
    reorder_level: Mapped[float | None]
    last_purchase_price_per_kg: Mapped[float | None]


class FeedingRecord(Base):
    __tablename__ = "feeding_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    date: Mapped[date] = mapped_column(default=today)
    shift: Mapped[str] = mapped_column(String(10))  # FeedingShift enum
    bucket: Mapped[str] = mapped_column(String(20))  # Bucket enum
    recipe_code: Mapped[str | None] = mapped_column(String(30))
    qty_kg: Mapped[float]
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


# ---------------------------------------------------------------------------
# Finance
# ---------------------------------------------------------------------------
class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    date: Mapped[date] = mapped_column(default=today)
    type: Mapped[str] = mapped_column(String(10))  # TransactionType enum
    category: Mapped[str] = mapped_column(String(20))  # TransactionCategory enum
    amount: Mapped[float]
    related_animal_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))
    notes: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    related_animal: Mapped[Animal | None] = relationship()


# ---------------------------------------------------------------------------
# Tasks / alerts
# ---------------------------------------------------------------------------
class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date]
    status: Mapped[str] = mapped_column(String(10), default=TaskStatus.PENDING.value)
    animal_id: Mapped[int | None] = mapped_column(ForeignKey("animals.id"))
    purchase_batch_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_batches.id"))
    breeding_record_id: Mapped[int | None] = mapped_column(ForeignKey("breeding_records.id"))
    category: Mapped[str] = mapped_column(String(15), default=TaskCategory.OTHER.value)
    auto_generated: Mapped[bool] = mapped_column(default=False)

    # Duty assignment (RBAC): a role, a specific worker, or both null
    # (owner-visible only).
    assigned_role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"))
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # Attribution + verification trail ("everyone's job is noted and digitized").
    completed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None]
    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None]
    verification_note: Mapped[str | None] = mapped_column(String(255))  # reason when rejected

    # Recurring duty: on completion the next occurrence is spawned this many
    # days after the current due_date (e.g. 1 = daily cleaning).
    recur_days: Mapped[int | None]

    animal: Mapped[Animal | None] = relationship()
    breeding_record: Mapped[BreedingRecord | None] = relationship()
    assigned_role: Mapped[Role | None] = relationship()
    assigned_user: Mapped[User | None] = relationship(foreign_keys="Task.assigned_user_id")
    completed_by: Mapped[User | None] = relationship(foreign_keys="Task.completed_by_id")
    verified_by: Mapped[User | None] = relationship(foreign_keys="Task.verified_by_id")

    @property
    def needs_verification(self) -> bool:
        """DONE for these categories means 'awaiting verification', not final."""
        return self.category in VERIFICATION_REQUIRED_CATEGORIES


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


# ---------------------------------------------------------------------------
# Computed domain logic (shared by routes, tests, and later phases)
# ---------------------------------------------------------------------------
def expected_kidding_date(breeding_date: date) -> date:
    return breeding_date + timedelta(days=GESTATION_DAYS)


def planned_ultrasound_date(breeding_date: date) -> date:
    return breeding_date + timedelta(days=ULTRASOUND_AFTER_BREEDING_DAYS)


def conception_rate(records: Iterable[BreedingRecord]) -> float | None:
    """Confirmed / total completed breedings (PENDING excluded). Percent or None."""
    completed = [r for r in records if r.outcome != BreedingOutcome.PENDING.value]
    if not completed:
        return None
    confirmed = sum(1 for r in completed if r.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value)
    return round(100.0 * confirmed / len(completed), 1)


# 45-day quarantine protocol (day offsets relative to batch arrival date).
QUARANTINE_PROTOCOL = [
    (
        1,
        TaskCategory.QUARANTINE,
        "Days 1–3: rest, electrolyte/jaggery water, dry roughage only, zero grain",
    ),
    (4, TaskCategory.DEWORMING, "Day 4: deworm — Albendazole/Closantel oral + Ivermectin SC"),
    (5, TaskCategory.QUARANTINE, "Days 5–9: liver tonic in water + Vitamin AD3E injection"),
    (10, TaskCategory.VACCINE, "Day 10: vaccinate PPR (live viral, SC)"),
    (20, TaskCategory.VACCINE, "Day 20: vaccinate ET + Tetanus (toxoid, SC)"),
    (30, TaskCategory.VACCINE, "Day 30: vaccinate Goat Pox (live viral, SC)"),
    (40, TaskCategory.VACCINE, "Day 40: vaccinate FMD (killed, SC)"),
    (45, TaskCategory.BUCKET_MOVE, "Day 45: 10% zinc sulfate footbath → release to FOUNDATION"),
]


def quarantine_schedule(batch: PurchaseBatch) -> list[dict[str, object]]:
    """Due-dated quarantine task definitions for a purchase batch (auto-gen in Phase 2)."""
    return [
        {
            "due_date": batch.date + timedelta(days=day_offset - 1),
            "category": category.value,
            "title": f"[{batch.supplier or 'Purchase'} #{batch.id}] {title}",
        }
        for day_offset, category, title in QUARANTINE_PROTOCOL
    ]
