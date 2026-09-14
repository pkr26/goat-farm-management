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
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from ..db import Base
from ..utils import DEFAULT_BUSINESS_TIMEZONE, business_date, today, utcnow
from .constants import BREEDING_READY_BUCKETS
from .enums import (
    AnimalSource,
    AnimalStatus,
    BirthType,
    BreedingOutcome,
    Bucket,
    MortalityCause,
    Sex,
    sql_in_values,
)
from .species import GOAT_PROFILE

if TYPE_CHECKING:
    from .breeding import BreedingRecord
    from .core import Farm
    from .purchases import PurchaseBatch

# Vocabulary IN-lists rendered once from the enum definitions so the CHECK
# literals cannot drift from models/enums.py (see sql_in_values).
_BUCKET_VALUES = sql_in_values(Bucket)
_ANIMAL_STATUS_VALUES = sql_in_values(AnimalStatus)
_MORTALITY_CAUSE_VALUES = sql_in_values(MortalityCause)


class Animal(Base):
    __tablename__ = "animals"
    __table_args__ = (
        UniqueConstraint("farm_id", "tag_number", name="uq_animal_tag_per_farm"),
        UniqueConstraint("farm_id", "id", name="uq_animals_farm_id_id"),
        ForeignKeyConstraint(
            ["farm_id", "purchase_batch_id"],
            ["purchase_batches.farm_id", "purchase_batches.id"],
            name="fk_animals_farm_purchase_batch",
        ),
        ForeignKeyConstraint(
            ["farm_id", "dam_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_animals_farm_dam",
        ),
        ForeignKeyConstraint(
            ["farm_id", "sire_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_animals_farm_sire",
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
        CheckConstraint(f"sex IN ({sql_in_values(Sex)})", name="ck_animals_sex"),
        CheckConstraint(
            f"birth_type IS NULL OR birth_type IN ({sql_in_values(BirthType)})",
            name="ck_animals_birth_type",
        ),
        CheckConstraint(f"source IN ({sql_in_values(AnimalSource)})", name="ck_animals_source"),
        CheckConstraint(
            f"current_bucket IN ({_BUCKET_VALUES})",
            name="ck_animals_current_bucket",
        ),
        CheckConstraint(
            f"status IN ({_ANIMAL_STATUS_VALUES})",
            name="ck_animals_status",
        ),
        CheckConstraint(
            "(source = 'BORN' AND purchase_date IS NULL AND purchase_price IS NULL "
            "AND seller_name IS NULL AND purchase_batch_id IS NULL) OR "
            "(source = 'PURCHASED' AND birth_type IS NULL AND birth_weight IS NULL)",
            name="ck_animals_source_fields",
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND status_date IS NULL) OR "
            "(status IN ('SOLD', 'DEAD', 'CULLED') AND status_date IS NOT NULL)",
            name="ck_animals_status_date",
        ),
        CheckConstraint(
            "status IN ('SOLD', 'CULLED') OR (sale_price IS NULL AND buyer_name IS NULL)",
            name="ck_animals_sale_fields",
        ),
        CheckConstraint(
            "status = 'DEAD' OR (mortality_cause IS NULL AND mortality_reported_at IS NULL)",
            name="ck_animals_mortality_fields",
        ),
        # Husbandry-standards death audit: the coded cause, the carcass
        # disposal and any necropsy narrative are terminal facts of a DEAD
        # animal, exactly like the legacy free-text cause above. necropsy_done
        # is a plain boolean and stays false on living rows, so it needs no
        # DEAD gate here.
        CheckConstraint(
            "status = 'DEAD' OR (mortality_cause_code IS NULL AND disposal_method IS NULL "
            "AND necropsy_findings IS NULL)",
            name="ck_animals_death_audit_fields",
        ),
        CheckConstraint(
            f"mortality_cause_code IS NULL OR mortality_cause_code IN ({_MORTALITY_CAUSE_VALUES})",
            name="ck_animals_mortality_cause_code",
        ),
        CheckConstraint(
            "(current_bucket <> 'MALE_KIDS' OR sex = 'M') AND "
            "(current_bucket <> 'FEMALE_KIDS' OR sex = 'F') AND "
            "(current_bucket NOT IN "
            "('PREGNANCY_EARLY', 'PREGNANCY_LATE', 'DELIVERY', 'RESTING') OR sex = 'F')",
            name="ck_animals_bucket_sex",
        ),
        CheckConstraint(
            "(dam_id IS NULL OR dam_id <> id) AND "
            "(sire_id IS NULL OR sire_id <> id) AND "
            "(dam_id IS NULL OR sire_id IS NULL OR dam_id <> sire_id)",
            name="ck_animals_parent_identity",
        ),
        CheckConstraint(
            "birth_weight IS NULL OR "
            "(birth_weight >= 0 AND birth_weight <= 1000 AND "
            "birth_weight::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_animals_birth_weight_bounded",
        ),
        CheckConstraint(
            "purchase_price IS NULL OR purchase_price <= 1000000000",
            name="ck_animals_purchase_price_bounded",
        ),
        CheckConstraint(
            "sale_price IS NULL OR sale_price <= 1000000000",
            name="ck_animals_sale_price_bounded",
        ),
        # Market-convention sale capture: a recorded weight-at-sale is a
        # positive measurement (₹/kg benchmarking divides by it), never zero.
        CheckConstraint(
            "sale_weight_kg IS NULL OR sale_weight_kg > 0",
            name="ck_animals_sale_weight_positive",
        ),
        CheckConstraint(
            "movement_restricted IS FALSE OR "
            "(restriction_reason IS NOT NULL AND btrim(restriction_reason) <> '')",
            name="ck_animals_restriction_reason",
        ),
        CheckConstraint(
            "suspected_scheduled_disease IS FALSE OR "
            "(movement_restricted IS TRUE AND suspected_disease IS NOT NULL "
            "AND btrim(suspected_disease) <> '')",
            name="ck_animals_suspected_disease_hold",
        ),
        CheckConstraint(
            "(restriction_cleared_at IS NULL AND restriction_clearance_reference IS NULL) OR "
            "(restriction_cleared_at IS NOT NULL AND "
            "restriction_clearance_reference IS NOT NULL "
            "AND btrim(restriction_clearance_reference) <> '')",
            name="ck_animals_clearance_audit",
        ),
        CheckConstraint(
            "restriction_version >= 0",
            name="ck_animals_restriction_version",
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
    # Indexed like dam_id: the animal profile matches offspring with
    # ``dam_id = :id OR sire_id = :id``, so leaving one half unindexed made
    # every profile view scan the farm's entire lifetime herd — SOLD and DEAD
    # rows included — and did the same for the SET NULL cascade of a deletion.
    sire_id: Mapped[int | None] = mapped_column(
        ForeignKey("animals.id", ondelete="SET NULL"), index=True
    )
    birth_weight: Mapped[float | None]

    current_bucket: Mapped[str] = mapped_column(String(20))  # Bucket enum
    status: Mapped[str] = mapped_column(String(10), default=AnimalStatus.ACTIVE.value)
    status_date: Mapped[date | None]  # when sold/dead/culled
    status_notes: Mapped[str | None] = mapped_column(String(255))
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    buyer_name: Mapped[str | None] = mapped_column(String(120))
    # Live weight at sale (husbandry standards): the denominator of the
    # realized ₹/kg the ledger note records. Decimal, not float, because the
    # derived sale price (weight × rate) must be paise-exact.
    sale_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
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
    # Monotonic scheduled-disease hold episode. Placement increments it;
    # clearance closes that same version and cannot clear a newer episode.
    restriction_version: Mapped[int] = mapped_column(default=0, server_default="0")
    mortality_cause: Mapped[str | None] = mapped_column(String(120))
    mortality_reported_at: Mapped[date | None] = mapped_column(Date)
    # Husbandry-standards death audit: coded cause for stable cross-farm
    # reporting (the legacy free text above stays accepted for local detail),
    # carcass disposal, and an optional post-mortem record.
    mortality_cause_code: Mapped[str | None] = mapped_column(String(30))  # MortalityCause enum
    disposal_method: Mapped[str | None] = mapped_column(String(60))
    necropsy_done: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    necropsy_findings: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    farm: Mapped[Farm] = relationship(back_populates="animals")
    purchase_batch: Mapped[PurchaseBatch | None] = relationship(
        back_populates="animals", foreign_keys=[purchase_batch_id]
    )
    # History collections are NOT eager-loaded at the mapper level:
    # a mapper-level lazy="selectin" would fire on every Animal load — including
    # db.get() for simple writes. Read paths that serialize AnimalOut's computed
    # fields are derived with bounded latest-row SQL summaries instead.
    weight_records: Mapped[list[WeightRecord]] = relationship(
        back_populates="animal",
        order_by="WeightRecord.date",
        cascade="all, delete-orphan",
        # Two FK paths link the tables (animal_id and the tenant composite
        # fk_weight_records_farm_animal); the history collection follows the
        # single-column identity FK.
        foreign_keys="WeightRecord.animal_id",
    )
    bucket_moves: Mapped[list[BucketMove]] = relationship(
        back_populates="animal",
        order_by="BucketMove.moved_at",
        cascade="all, delete-orphan",
        # Two FK paths link the tables (animal_id and the tenant composite
        # fk_bucket_moves_farm_animal); the history collection follows the
        # single-column identity FK.
        foreign_keys="BucketMove.animal_id",
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

    def latest_weight_kg_on(self, reference_date: date) -> float | None:
        """Latest measurement available on ``reference_date``.

        A later weigh-in must not make a backdated service clinically valid.
        Birth weight is a fallback when the birth is on/before the reference
        date, or when no DOB was recorded. Its small value naturally fails
        adult thresholds; an unknown DOB still prevents age-based eligibility.
        """
        records = [record for record in self.weight_records if record.date <= reference_date]
        if records:
            return max(records, key=lambda record: (record.date, record.id or 0)).weight_kg
        dob = self.effective_dob
        return (
            self.birth_weight
            if self.birth_weight is not None and (dob is None or dob <= reference_date)
            else None
        )

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
        ref: datetime | date | None = (
            (move.effective_date or move.moved_at) if move else self.created_at
        )
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
        profile = GOAT_PROFILE
        if self.sex != Sex.F.value or self.status != AnimalStatus.ACTIVE.value:
            return False
        if self.movement_restricted or self.suspected_scheduled_disease:
            return False
        if self.current_bucket not in [b.value for b in BREEDING_READY_BUCKETS]:
            return False
        age = self.age_months_on(reference_date)
        if age is None or age < profile.min_breeding_age_months:
            return False
        weight = self.latest_weight_kg_on(reference_date)
        if weight is None or weight < profile.min_breeding_weight_kg:
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
        profile = GOAT_PROFILE
        if self.sex != Sex.F.value or self.status != AnimalStatus.ACTIVE.value:
            return False
        if self.movement_restricted or self.suspected_scheduled_disease:
            return False
        if self.current_bucket not in [*BREEDING_READY_BUCKETS, "BREEDING"]:
            return False
        age = self.age_months_on(reference_date)
        weight = self.latest_weight_kg_on(reference_date)
        return bool(
            age is not None
            and age >= profile.min_breeding_age_months
            and weight is not None
            and weight >= profile.min_breeding_weight_kg
            and not self.is_currently_pregnant
        )

    @property
    def is_buck_eligible(self) -> bool:
        """Canonical sire eligibility on today's default business date."""
        return self.is_buck_eligible_on(today())

    def is_buck_ready_on(self, reference_date: date) -> bool:
        """Factual maturity/health floor, independent of the current bucket."""
        profile = GOAT_PROFILE
        age = self.age_months_on(reference_date)
        weight = self.latest_weight_kg_on(reference_date)
        return bool(
            self.sex == Sex.M.value
            and self.status == AnimalStatus.ACTIVE.value
            and not self.movement_restricted
            and not self.suspected_scheduled_disease
            and age is not None
            and age >= profile.min_sire_breeding_age_months
            and weight is not None
            and weight >= profile.min_sire_breeding_weight_kg
        )

    def is_buck_eligible_on(self, reference_date: date) -> bool:
        """A ready sire already housed in a buck-capable breeding bucket."""
        return self.current_bucket in {"FOUNDATION", "BREEDING"} and self.is_buck_ready_on(
            reference_date
        )

    @property
    def display_name(self) -> str:
        return f"{self.tag_number}" + (f" · {self.name}" if self.name else "")


class WeightRecord(Base):
    __tablename__ = "weight_records"
    __table_args__ = (
        # Tenant guard: the measurement can never point at another farm's
        # animal, independently of API/service filters.
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_weight_records_farm_animal",
        ),
        Index("ix_weight_records_date", "date"),
        CheckConstraint("weight_kg > 0", name="ck_weight_records_weight_positive"),
        CheckConstraint(
            "bcs IS NULL OR bcs BETWEEN 1 AND 5",
            name="ck_weight_records_bcs_range",
        ),
        CheckConstraint(
            "weight_kg <= 1000 AND weight_kg::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_weight_records_weight_bounded",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    animal_id: Mapped[int] = mapped_column(ForeignKey("animals.id"), index=True)
    date: Mapped[date] = mapped_column(default=today)
    weight_kg: Mapped[float]
    bcs: Mapped[int | None]  # body condition score 1–5
    notes: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # foreign_keys disambiguates the single-column animal_id FK from the
    # (farm_id, animal_id) tenant composite on the same table.
    animal: Mapped[Animal] = relationship(back_populates="weight_records", foreign_keys=[animal_id])


class BucketMove(Base):
    __tablename__ = "bucket_moves"
    __table_args__ = (
        # Tenant guard: a lifecycle move can never point at another farm's
        # animal, independently of API/service filters.
        ForeignKeyConstraint(
            ["farm_id", "animal_id"],
            ["animals.farm_id", "animals.id"],
            name="fk_bucket_moves_farm_animal",
        ),
        CheckConstraint(
            f"from_bucket IS NULL OR from_bucket IN ({_BUCKET_VALUES})",
            name="ck_bucket_moves_from_bucket",
        ),
        CheckConstraint(
            f"to_bucket IN ({_BUCKET_VALUES})",
            name="ck_bucket_moves_to_bucket",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    animal_id: Mapped[int] = mapped_column(ForeignKey("animals.id"), index=True)
    from_bucket: Mapped[str | None] = mapped_column(String(20))  # None = initial placement
    to_bucket: Mapped[str] = mapped_column(String(20))
    moved_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Business-effective date of the lifecycle fact. ``moved_at`` remains the
    # immutable audit insertion instant; backdated purchases, kiddings and
    # pregnancy losses must not reset feeding age to the time they were typed.
    effective_date: Mapped[date] = mapped_column(
        Date,
        default=today,
        server_default=text("CURRENT_DATE"),
    )
    reason: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # foreign_keys disambiguates the single-column animal_id FK from the
    # (farm_id, animal_id) tenant composite on the same table.
    animal: Mapped[Animal] = relationship(back_populates="bucket_moves", foreign_keys=[animal_id])


# ---------------------------------------------------------------------------
# Reference: bucket definitions (seeded)
# ---------------------------------------------------------------------------
class BucketDefinition(Base):
    __tablename__ = "bucket_definitions"
    __table_args__ = (
        # One definition row per lifecycle stage code.
        UniqueConstraint("code", name="uq_bucket_definitions_code"),
        CheckConstraint(
            f"code IN ({_BUCKET_VALUES})",
            name="ck_bucket_definitions_code",
        ),
        CheckConstraint(
            "daily_kg_per_head > 0 AND daily_kg_per_head <= 1000000 AND "
            "daily_kg_per_head::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_bucket_definitions_daily_kg",
        ),
        CheckConstraint("sort_order >= 0", name="ck_bucket_definitions_sort_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    who: Mapped[str | None] = mapped_column(String(255))
    exit_rule: Mapped[str | None] = mapped_column(String(255))
    # Feeding plans and shift allocation operate in whole grams.  Keep the
    # public float contract but make the persisted per-head quantity exact.
    daily_kg_per_head: Mapped[float] = mapped_column(Numeric(15, 3, asdecimal=False), default=1.2)
    sort_order: Mapped[int] = mapped_column(default=0)


class BucketFeedSetting(Base):
    """Per-farm override of BucketDefinition.daily_kg_per_head."""

    __tablename__ = "bucket_feed_settings"
    __table_args__ = (
        UniqueConstraint("farm_id", "bucket", name="uq_feed_setting_per_bucket"),
        CheckConstraint(
            f"bucket IN ({_BUCKET_VALUES})",
            name="ck_bucket_feed_settings_bucket",
        ),
        CheckConstraint(
            "daily_kg_per_head > 0 AND daily_kg_per_head <= 1000000 AND "
            "daily_kg_per_head::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_bucket_feed_settings_daily_kg",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    bucket: Mapped[str] = mapped_column(String(20))
    daily_kg_per_head: Mapped[float] = mapped_column(Numeric(15, 3, asdecimal=False))


# Read-heavy herd views use bounded latest-row probes.  These composite
# indexes keep those probes index-only/ordered even after years of history.
Index(
    "ix_bucket_moves_animal_moved_id_desc",
    BucketMove.animal_id,
    BucketMove.moved_at.desc(),
    BucketMove.id.desc(),
)
Index(
    "ix_weight_records_animal_date_id_desc",
    WeightRecord.animal_id,
    WeightRecord.date.desc(),
    WeightRecord.id.desc(),
    postgresql_include=["weight_kg"],
)
Index(
    "ix_weight_records_recent_date_id_animal",
    WeightRecord.date.desc(),
    WeightRecord.id.desc(),
    WeightRecord.animal_id,
)
Index(
    "ix_animals_farm_active_bucket_tag_id",
    Animal.farm_id,
    Animal.current_bucket,
    Animal.tag_number,
    Animal.id,
    postgresql_include=["name", "sex", "birth_weight", "created_at"],
    postgresql_where=text("status = 'ACTIVE'"),
)
Index(
    "ix_animals_farm_active_cull_tag_id",
    Animal.farm_id,
    Animal.tag_number,
    Animal.id,
    postgresql_include=["name"],
    postgresql_where=text("status = 'ACTIVE' AND cull_candidate IS TRUE"),
)
Index("ix_animals_farm_status", Animal.farm_id, Animal.status)


@event.listens_for(Session, "before_flush")
def _derive_history_farm_id(session: Session, flush_context: object, instances: object) -> None:
    """Populate weight_records/bucket_moves.farm_id for pre-tenant writers.

    These history tables gained their tenant farm_id (migration
    b6d8f0a2c4e6) late; their call sites construct rows from the animal
    alone (``WeightRecord(animal_id=...)``) and are owned by other remediation
    streams. Resolving the animal's farm here keeps every ORM flush correct
    without threading farm_id through each writer: the animal is looked up in
    the session's identity map or database, so a row can never silently land
    with a wrong farm. A row whose animal cannot be resolved keeps farm_id
    NULL and fails the NOT NULL constraint loudly rather than guessing.
    Bulk/Core ``insert()`` statements bypass flush events and must supply
    farm_id themselves — the only such writers are migrations and tests.
    """
    for obj in session.new:
        if not isinstance(obj, (WeightRecord, BucketMove)) or obj.farm_id is not None:
            continue
        if obj.animal_id is None:
            continue
        animal = session.get(Animal, obj.animal_id)
        if animal is not None:
            obj.farm_id = animal.farm_id
