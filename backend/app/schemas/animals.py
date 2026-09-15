"""Pydantic schemas for the animals module."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from ..models.constants import HISTORY_OVERRIDE_REASON_PREFIX, MAX_ANIMAL_TAG_LENGTH
from ..models.helpers import no_control_characters
from .common import (
    MAX_FREE_TEXT_LENGTH,
    MoneyFloat,
    NonNegativeMoneyFloat,
    NonNegativeWeightKgFloat,
    PastOrTodayDate,
    PostgresText,
    StrictBool,
    StrictInputModel,
    StrictInt,
    WeightKgFloat,
)

Sex = Literal["M", "F"]
AnimalSourceStr = Literal["BORN", "PURCHASED"]
AnimalStatusStr = Literal["ACTIVE", "SOLD", "DEAD", "CULLED"]
BirthTypeStr = Literal["SINGLE", "TWIN", "TRIPLET", "QUADRUPLET", "MULTIPLET"]
# Coded mortality vocabulary (husbandry standards). Re-declares
# models.enums.MortalityCause like every other wire Literal; the parity test
# keeps the two lists from drifting.
MortalityCauseStr = Literal[
    "PNEUMONIA",
    "DIARRHOEA",
    "COLIBACILLOSIS",
    "ENTEROTOXAEMIA",
    "PPR_SUSPECTED",
    "FMD_SUSPECTED",
    "GOAT_POX_SUSPECTED",
    "PARASITISM",
    "COCCIDIOSIS",
    "NUTRITIONAL",
    "HEAT_STRESS",
    "PREDATION",
    "ACCIDENT",
    "DYSTOCIA",
    "OLD_AGE",
    "OTHER",
    "UNKNOWN",
]
# Coded carcass-disposal vocabulary (husbandry standards). Re-declares
# models.enums.DisposalMethod like every other wire Literal; the parity test
# keeps the two lists from drifting.
DisposalMethodStr = Literal["DEEP_BURIAL", "BURNING", "RENDERING", "COMPOSTING", "OTHER"]
# Osmanabadi phenotype vocabulary (models.enums.CoatColor): pure-line
# tracking for the Bakrid premium (~73% black, ~90% of males horned).
CoatColorStr = Literal["black", "black_patched", "brown", "white", "spotted"]
BucketStr = Literal[
    "QUARANTINE",
    "FOUNDATION",
    "BREEDING",
    "PREGNANCY_EARLY",
    "PREGNANCY_LATE",
    "DELIVERY",
    "RECOVERY",
    "RESTING",
    "MALE_KIDS",
    "FEMALE_KIDS",
]


# Tags and names are identifiers, not narrative: embedded tab/LF/CR makes
# them visually confusable on the task board/pickers and breaks
# line-oriented exports (PostgresText keeps \t\n\r only for prose fields).
IdentifierText = Annotated[PostgresText, AfterValidator(no_control_characters)]


class AnimalCreateIn(StrictInputModel):
    tag_number: IdentifierText | None = Field(
        default=None, min_length=1, max_length=MAX_ANIMAL_TAG_LENGTH
    )
    name: IdentifierText | None = Field(default=None, max_length=80)
    sex: Sex
    source: AnimalSourceStr
    current_bucket: BucketStr
    # Empty means the species default breed (Osmanabadi), resolved
    # server-side.
    breed: PostgresText = Field(default="", max_length=60)
    coat_color: CoatColorStr | None = None
    horned: StrictBool | None = None
    date_of_birth: PastOrTodayDate | None = None
    estimated_dob: PastOrTodayDate | None = None
    birth_type: BirthTypeStr | None = None
    birth_weight: NonNegativeWeightKgFloat | None = None
    purchase_date: PastOrTodayDate | None = None
    purchase_price: NonNegativeMoneyFloat | None = None
    seller_name: PostgresText | None = Field(default=None, max_length=120)
    weight_kg: WeightKgFloat | None = None  # optional entry weight record
    weight_date: PastOrTodayDate | None = None
    notes: PostgresText | None = Field(default=None, max_length=MAX_FREE_TEXT_LENGTH)
    # Existing-herd migration only. Normal PURCHASED registrations are
    # server-forced into a managed one-head quarantine batch, while BORN rows
    # normally come only from the kidding workflow. Supplying a reason marks a
    # deliberate, owner-only, attributed historical entry instead.
    historical_import_reason: PostgresText | None = Field(
        default=None, min_length=1, max_length=255
    )

    @model_validator(mode="after")
    def _source_fields_are_coherent(self) -> "AnimalCreateIn":
        """Reject source-field combinations that would create false lineage.

        Direct historical entry remains possible (for example a BORN animal
        with a birth weight), but a record cannot simultaneously claim a farm
        birth and purchase provenance.
        """
        if self.source == "BORN" and any(
            value is not None
            for value in (self.purchase_date, self.purchase_price, self.seller_name)
        ):
            raise ValueError("Born animals cannot include purchase provenance")
        if self.source == "PURCHASED" and any(
            value is not None for value in (self.birth_type, self.birth_weight)
        ):
            raise ValueError("Purchased animals cannot include birth-only fields")
        if self.historical_import_reason is not None and not self.historical_import_reason.strip():
            raise ValueError("historical_import_reason cannot be blank")
        if self.source == "BORN" and not (self.historical_import_reason or "").strip():
            raise ValueError(
                "Direct BORN entry is a historical import and requires historical_import_reason"
            )
        if self.weight_date is not None and self.weight_kg is None:
            raise ValueError("weight_date requires weight_kg")
        if self.current_bucket == "MALE_KIDS" and self.sex != "M":
            raise ValueError("Only male animals may enter MALE_KIDS")
        if self.current_bucket == "FEMALE_KIDS" and self.sex != "F":
            raise ValueError("Only female animals may enter FEMALE_KIDS")
        if self.current_bucket == "RESTING" and self.sex != "F":
            raise ValueError("Only female animals may enter RESTING")
        if (
            self.current_bucket in {"PREGNANCY_EARLY", "PREGNANCY_LATE", "DELIVERY"}
            and self.sex != "F"
        ):
            raise ValueError(f"Only female animals may enter {self.current_bucket}")
        return self


class AnimalUpdateIn(StrictInputModel):
    """Owner edit of the phenotype record (pure-line tracking).

    Both fields are nullable on purpose: omitted leaves the stored value,
    explicit null clears it (unrecorded again).
    """

    coat_color: CoatColorStr | None = None
    horned: StrictBool | None = None


class AnimalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tag_number: str
    name: str | None
    breed: str
    sex: Sex
    date_of_birth: date | None
    estimated_dob: date | None
    birth_type: BirthTypeStr | None
    source: AnimalSourceStr
    dam_id: int | None
    sire_id: int | None
    birth_weight: float | None
    current_bucket: BucketStr
    status: AnimalStatusStr
    status_date: date | None
    sale_price: float | None
    # Operational sale fact (like latest_weight_kg); paired with sale_price in
    # the ledger note for realized ₹/kg benchmarking.
    sale_weight_kg: float | None
    buyer_name: str | None
    mortality_cause: str | None
    mortality_cause_code: MortalityCauseStr | None
    disposal_method: DisposalMethodStr | None
    necropsy_done: bool
    necropsy_findings: str | None
    coat_color: CoatColorStr | None
    horned: bool | None
    purchase_date: date | None
    purchase_price: float | None
    seller_name: str | None
    cull_candidate: bool
    movement_restricted: bool
    restriction_reason: str | None
    suspected_scheduled_disease: bool
    suspected_disease: str | None
    authority_notified_at: date | None
    restriction_cleared_at: datetime | None
    restriction_cleared_by_id: int | None
    restriction_clearance_reference: str | None
    restriction_version: int
    mortality_reported_at: date | None
    notes: str | None
    created_at: datetime
    # computed
    age_months: int | None = None
    latest_weight_kg: float | None = None
    is_breeding_ready: bool = False
    is_currently_pregnant: bool = False
    days_in_current_bucket: int = 0


class AnimalListOut(BaseModel):
    animals: list[AnimalOut]
    total: int


class WeightIn(StrictInputModel):
    date: PastOrTodayDate | None = None  # defaults to today
    weight_kg: WeightKgFloat
    bcs: StrictInt | None = Field(default=None, ge=1, le=5)
    notes: PostgresText | None = Field(
        default=None, max_length=255
    )  # weight_records.notes String(255)


class WeightRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date
    weight_kg: float
    bcs: int | None
    notes: str | None


class MoveIn(StrictInputModel):
    to_bucket: BucketStr
    reason: PostgresText | None = Field(
        default=None, max_length=255
    )  # bucket_moves.reason String(255)
    history_override: StrictBool = False

    @model_validator(mode="after")
    def _history_override_requires_reason(self) -> "MoveIn":
        if self.history_override and not (self.reason or "").strip():
            raise ValueError("A history override requires a reason")
        # move_bucket prepends HISTORY_OVERRIDE_REASON_PREFIX itself, and four
        # domain predicates read it back as proof that a RECOVERY departure was
        # an owner-authorised backdating rather than a real one (orphan-wean
        # provenance, the dam-retirement sweep, weaning dependants, and
        # replan_dam_after_last_kid_death). Any holder of animals.move could
        # otherwise author the marker by hand and hide a genuine departure from
        # all four. Reserve the sentinel at the boundary.
        if (
            (self.reason or "")
            .lstrip()
            .upper()
            .startswith(HISTORY_OVERRIDE_REASON_PREFIX.strip().upper())
        ):
            raise ValueError("reason must not begin with the reserved history-override marker")
        return self


class BucketMoveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_bucket: BucketStr | None
    to_bucket: BucketStr
    reason: str | None
    effective_date: date
    moved_at: datetime


class AnimalOffspringOut(BaseModel):
    """Identity/lifecycle fields rendered in a parent's kids table."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tag_number: str
    name: str | None
    sex: str
    date_of_birth: date | None
    estimated_dob: date | None
    status: str


class StatusChangeIn(StrictInputModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    new_status: Literal["SOLD", "DEAD", "CULLED"]
    date: PastOrTodayDate | None = None  # defaults to today
    sale_price: NonNegativeMoneyFloat | None = None
    buyer_name: PostgresText | None = Field(default=None, max_length=120)
    # Market-convention sale capture (SOLD only): live weight at sale and the
    # realized ₹/kg rate. When sale_price is omitted but both are supplied the
    # endpoint derives price = money(weight × rate) — paise-exact.
    sale_weight_kg: WeightKgFloat | None = None
    sale_price_per_kg: MoneyFloat | None = None
    notes: PostgresText | None = Field(
        default=None, max_length=255
    )  # animals.status_notes String(255)
    mortality_cause: PostgresText | None = Field(default=None, max_length=120)
    mortality_cause_code: MortalityCauseStr | None = None
    # Bounded carcass-disposal vocabulary (ck_animals_disposal_method).
    disposal_method: DisposalMethodStr | None = None
    mortality_reported_at: PastOrTodayDate | None = None
    # A male sold with no birth or estimated date on record must have his age
    # fixed at sale time — the endpoint stamps this onto the animal before
    # the meat-sale age floor runs, so the estimate becomes part of the
    # permanent record instead of an unverifiable pass.
    estimated_dob: PastOrTodayDate | None = None
    necropsy_done: StrictBool = False
    necropsy_findings: PostgresText | None = Field(
        default=None, max_length=MAX_FREE_TEXT_LENGTH
    )  # animals.necropsy_findings Text
    suspected_scheduled_disease: StrictBool = False
    suspected_disease: PostgresText | None = Field(default=None, max_length=120)
    authority_notified_at: PastOrTodayDate | None = None

    @model_validator(mode="after")
    def _death_escalation_fields_are_coherent(self) -> "StatusChangeIn":
        if self.new_status not in {"SOLD", "CULLED"} and (
            self.sale_price is not None or self.buyer_name is not None
        ):
            raise ValueError("Sale price and buyer require SOLD or CULLED status")
        if self.new_status != "SOLD" and (
            self.sale_weight_kg is not None or self.sale_price_per_kg is not None
        ):
            raise ValueError("Sale weight and price-per-kg require SOLD status")
        if self.new_status != "SOLD" and self.estimated_dob is not None:
            raise ValueError("estimated_dob requires SOLD status")
        if self.new_status != "DEAD" and any(
            value is not None
            for value in (
                self.mortality_cause,
                self.mortality_cause_code,
                self.disposal_method,
                self.mortality_reported_at,
                self.necropsy_findings,
                self.suspected_disease,
                self.authority_notified_at,
            )
        ):
            raise ValueError("Mortality and disease-escalation fields require DEAD status")
        if self.new_status != "DEAD" and self.necropsy_done:
            raise ValueError("necropsy_done requires DEAD status")
        if self.necropsy_findings is not None and not self.necropsy_done:
            raise ValueError("Necropsy findings require necropsy_done=true")
        if self.sale_price_per_kg is not None and self.sale_weight_kg is None:
            raise ValueError("sale_price_per_kg requires sale_weight_kg")
        if self.suspected_scheduled_disease and not self.suspected_disease:
            raise ValueError("A suspected scheduled disease requires an identified disease")
        if not self.suspected_scheduled_disease and (
            self.suspected_disease is not None or self.authority_notified_at is not None
        ):
            raise ValueError(
                "Suspected disease and authority notification require "
                "suspected_scheduled_disease=true"
            )
        return self


class AnimalProfileOut(BaseModel):
    animal: AnimalOut
    kids: list[AnimalOffspringOut]
    kids_total: int
    kids_offset: int
    weights: list[WeightRecordOut]
    weights_total: int
    weights_offset: int
    moves: list[BucketMoveOut]
    moves_total: int
    moves_offset: int
    health_events: list["HealthEventOut"]
    health_events_total: int
    health_events_offset: int
    breedings: list[int]  # breeding record ids (details fetched via /api/breeding)
    breedings_total: int
    breedings_offset: int
    history_limit: int


class BucketBoardRow(BaseModel):
    bucket: str
    name: str
    who: str
    exit_rule: str
    daily_kg_per_head: float
    animals: list[AnimalOut]


from .health import HealthEventOut  # noqa: E402

AnimalProfileOut.model_rebuild()
