"""Unit-level edge-case tests: model computed properties, enums, Pydantic
schema validators, and pure service/utils helpers.

Almost everything here is DB-free: in-memory model instances, direct calls to
pure functions, and schema validation (valid / invalid / boundary inputs). The
`client` fixture is used only for a handful of boundary cases that must prove
a value accepted by the schema also fits the real database column (the exact
DB-length limits; lengths the schema admits beyond those limits live in
tests/test_unit_bugs.py).

Covered:
- app/utils.py: add_months (leap years, month-end clamping, out-of-range),
  today/utcnow naivety.
- app/permissions.py: catalog/group/preset/role-map internal consistency.
- app/models.py: enum exhaustiveness, breed constants, computed properties
  (age_months day-boundary, latest_weight tie-breaks, days_in_current_bucket
  clamping, is_breeding_ready at exactly 10 months / 22 kg, display names,
  Role.permission_set, Task.needs_verification), expected/planned dates,
  conception_rate rounding, quarantine schedule generation.
- app/services.py pure helpers: move_animal guards, recipe_for_animal bucket
  and day/age boundaries, SHIFT_SPLIT.
- app/schemas/*: every input schema with valid, invalid and boundary inputs.
- README.md claims that are derivable from the code (table count, the driver
  scheme its runbook commands feed to the async engine).
"""

import re
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

import app.services.feeding as feeding_service
from app import security
from app.db import Base
from app.models import (
    BREEDING_READY_BUCKETS,
    BUCK_DOE_RATIO,
    BUCK_ROTATION_DAYS,
    GESTATION_DAYS,
    KIDDING_WINDOW_DAYS,
    MAX_FAILED_CYCLES_BEFORE_CULL,
    MEAT_SALE_AGE_MONTHS,
    MEAT_SALE_WEIGHT_KG,
    MIN_BREEDING_AGE_MONTHS,
    MIN_BREEDING_WEIGHT_KG,
    QUARANTINE_PROTOCOL,
    SHIFT_SPLIT,
    ULTRASOUND_AFTER_BREEDING_DAYS,
    VERIFICATION_REQUIRED_CATEGORIES,
    WEANING_DAYS,
    Animal,
    AnimalSource,
    AnimalStatus,
    BirthType,
    BreedingMethod,
    BreedingOutcome,
    BreedingRecord,
    Bucket,
    BucketMove,
    FeedingShift,
    HealthEventType,
    IngredientCategory,
    KiddingEase,
    KidStatus,
    PurchaseBatch,
    Role,
    Sex,
    Task,
    TaskCategory,
    TaskStatus,
    TransactionCategory,
    TransactionType,
    User,
    WeightRecord,
    conception_rate,
    expected_kidding_date,
    planned_ultrasound_date,
    quarantine_schedule,
)
from app.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_GROUPS,
    PERMISSIONS,
    ROLE_PRESET_CODES,
    ROLE_PRESETS,
    TASK_CATEGORY_ROLE_MAP,
    TASK_ROLE_CODES,
)
from app.schemas.animals import (
    AnimalCreateIn,
    MoveIn,
    StatusChangeIn,
    WeightIn,
)
from app.schemas.auth import FarmCreateIn, LoginIn, RegisterIn
from app.schemas.breeding import BreedingCreateIn, UltrasoundIn
from app.schemas.common import MAX_ID
from app.schemas.feeding import DispenseIn, FeedSettingIn, MixIn, StockAddIn
from app.schemas.finance import TransactionIn
from app.schemas.health import HealthEventIn
from app.schemas.kidding import KiddingCreateIn, KidIn
from app.schemas.purchases import PurchaseBatchIn
from app.schemas.tasks import TaskCreateIn, TaskRejectIn, TaskSkipIn
from app.schemas.team import PasswordResetIn, RoleIn, WorkerCreateIn
from app.services import move_animal, recipe_for_animal
from app.utils import add_months, allocate_money, business_date, money, today, utcnow

from .conftest import owner_with_farm, register

TODAY = today()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()
# PastOrTodayDate tolerates one day of timezone headroom (clients east of
# UTC). Use a fixed far-future value for invalid pins: deriving it from TODAY
# made the full suite flaky when it crossed midnight in the business timezone.
GUARANTEED_FUTURE = date.max.isoformat()
NAN = float("nan")
INF = float("inf")


def test_dummy_password_hash_is_computed_once_for_startup_warmup(monkeypatch) -> None:
    calls = 0

    def fake_hash_password(password: str) -> str:
        nonlocal calls
        calls += 1
        return f"hashed:{password}"

    security.prime_dummy_password_hash.cache_clear()
    monkeypatch.setattr(security, "hash_password", fake_hash_password)
    try:
        first = security.prime_dummy_password_hash()
        second = security.prime_dummy_password_hash()
    finally:
        security.prime_dummy_password_hash.cache_clear()

    assert first == second == "hashed:dummy-password-for-timing-equalization"
    assert calls == 1


@pytest.mark.parametrize(
    ("total", "parts", "expected"),
    [
        ("1000.00", 3, ["333.34", "333.33", "333.33"]),
        ("0.02", 4, ["0.01", "0.01", "0.00", "0.00"]),
        ("0", 3, ["0.00", "0.00", "0.00"]),
    ],
)
def test_allocate_money_is_exact_and_never_negative(total, parts, expected) -> None:
    shares = allocate_money(total, parts)
    assert [str(share) for share in shares] == expected
    assert sum(shares) == money(total)
    assert all(share >= 0 for share in shares)


def test_allocate_money_rejects_invalid_parts_and_negative_totals() -> None:
    with pytest.raises(ValueError, match="parts must be positive"):
        allocate_money("1.00", 0)
    with pytest.raises(ValueError, match="value cannot be negative"):
        allocate_money("-0.01", 1)


# ---------------------------------------------------------------------------
# app.utils — add_months (month arithmetic with clamping)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2026, 1, 15), 1, date(2026, 2, 15)),
        (date(2026, 12, 15), 1, date(2027, 1, 15)),  # year rollover forward
        (date(2026, 3, 15), -1, date(2026, 2, 15)),
        (date(2026, 1, 15), -2, date(2025, 11, 15)),  # year rollover backward
        (date(2026, 6, 15), 0, date(2026, 6, 15)),
        (date(2026, 6, 15), 2.9, date(2026, 8, 15)),  # fractional months truncate
        (date(2023, 1, 31), 1, date(2023, 2, 28)),  # clamp to Feb 28 (non-leap)
        (date(2024, 1, 31), 1, date(2024, 2, 29)),  # clamp to Feb 29 (leap)
        (date(2024, 3, 31), -1, date(2024, 2, 29)),
        (date(2023, 3, 31), -1, date(2023, 2, 28)),
        (date(2024, 2, 29), 12, date(2025, 2, 28)),  # leap day + 1y clamps
        (date(2026, 5, 31), 1, date(2026, 6, 30)),  # 31st → 30-day month
        (date(2026, 10, 31), -1, date(2026, 9, 30)),
        (date(2026, 8, 6), 24, date(2028, 8, 6)),
    ],
)
def test_add_months(start: date, months: float, expected: date) -> None:
    assert add_months(start, months) == expected


@pytest.mark.parametrize(
    ("start", "months"),
    [
        (date(9999, 12, 1), 1),  # year 10000 out of range
        (date(1, 1, 15), -1),  # year 0 out of range
        (date(2026, 1, 1), 100000),  # absurd input ages must not explode date()
    ],
)
def test_add_months_out_of_range_raises(start: date, months: float) -> None:
    with pytest.raises(ValueError, match="out of range"):
        add_months(start, months)


def test_today_returns_plain_date() -> None:
    assert type(today()) is date


def test_utcnow_is_naive_datetime() -> None:
    ts = utcnow()
    assert isinstance(ts, datetime)
    assert ts.tzinfo is None  # SQLite-era contract: naive UTC


# ---------------------------------------------------------------------------
# app.permissions — catalog, groups, presets, role map consistency
# ---------------------------------------------------------------------------
def test_permission_codes_unique_and_nonempty() -> None:
    codes = [code for code, _ in PERMISSIONS]
    assert len(codes) == len(set(codes))
    assert all("." in code for code in codes)  # "module.action" convention


def test_all_permissions_matches_catalog() -> None:
    assert ALL_PERMISSIONS == {code for code, _ in PERMISSIONS}


def test_permission_groups_cover_catalog_exactly() -> None:
    grouped = [code for _, codes in PERMISSION_GROUPS for code in codes]
    assert set(grouped) == ALL_PERMISSIONS
    assert len(grouped) == len(set(grouped))  # no code listed in two groups


def test_every_permission_has_distinct_nonempty_label() -> None:
    labels = [_label for _, _label in PERMISSIONS]
    assert all(label.strip() for label in labels)
    assert len(set(labels)) == len(labels)


def test_role_preset_codes_unique() -> None:
    codes = [p["code"] for p in ROLE_PRESETS]
    assert len(codes) == len(set(codes))
    assert set(codes) == {
        # universal crew + office presets
        "MANAGER",
        "MOVER",
        "VET",
        "BUYER",
        "FEEDER",
        "CLEANER",
        "CLEANER_MANAGER",
        "ACCOUNTANT",
        "VIEWER",
    }
    assert set(codes) == ROLE_PRESET_CODES


@pytest.mark.parametrize("preset", ROLE_PRESETS, ids=lambda p: p["code"])
def test_role_preset_permissions_are_valid_catalog_codes(preset: dict) -> None:
    assert preset["name"].strip()
    assert preset["permissions"], "preset with no permissions is useless"
    assert set(preset["permissions"]) <= ALL_PERMISSIONS
    # every preset can at least see the dashboard
    assert "dashboard.view" in preset["permissions"]
    # every preset a generated duty can route to must be able to open and
    # close that duty (officers/auditors receive no duties by design)
    if preset["code"] in TASK_ROLE_CODES:
        assert {"tasks.view", "tasks.complete"} <= set(preset["permissions"])


def test_task_category_role_map_targets_valid_categories_and_presets() -> None:
    categories = {c.value for c in TaskCategory}
    preset_codes = {p["code"] for p in ROLE_PRESETS}
    assert set(TASK_CATEGORY_ROLE_MAP) <= categories
    assert set(TASK_CATEGORY_ROLE_MAP.values()) <= preset_codes


@pytest.mark.parametrize(
    ("category", "role_code"),
    [
        ("ULTRASOUND", "VET"),
        ("VACCINE", "VET"),
        ("DEWORMING", "VET"),
        ("QUARANTINE", "VET"),
        ("KIDDING_DUE", "VET"),
        ("BUCKET_MOVE", "MOVER"),
        ("WEANING", "MOVER"),
        ("FEED", "FEEDER"),
        ("CLEANING", "CLEANER"),
        # Husbandry-standards wave: watch/disinfection rounds → CLEANER,
        # clinical protocols → VET, weighing/moves → MOVER, planning and
        # squad rotation → MANAGER, policy paperwork → ACCOUNTANT.
        ("KIDDING_WATCH", "CLEANER"),
        ("BIRTHING_KIT", "MANAGER"),
        ("HEALTH_CHECK", "VET"),
        ("HEAT_WATCH", "CLEANER"),
        ("HOOF_TRIMMING", "VET"),
        ("SPRAYING", "VET"),
        ("DISINFECTION", "CLEANER"),
        ("WEIGHING", "MOVER"),
        ("REBREED", "MANAGER"),
        ("BUCK_ROTATION", "MANAGER"),
        ("INSURANCE", "ACCOUNTANT"),
    ],
)
def test_task_category_role_map_matches_spec(category: str, role_code: str) -> None:
    # SPEC: ultrasound/vaccine → VET, bucket moves/weaning → MOVER, feed → FEEDER
    assert TASK_CATEGORY_ROLE_MAP[category] == role_code


# ---------------------------------------------------------------------------
# app.models — enum exhaustiveness and breed constants (SPEC §Breed constants)
# ---------------------------------------------------------------------------
ENUM_CASES = [
    (
        Bucket,
        {
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
        },
    ),
    (AnimalStatus, {"ACTIVE", "SOLD", "DEAD", "CULLED"}),
    (AnimalSource, {"BORN", "PURCHASED"}),
    (Sex, {"M", "F"}),
    (BirthType, {"SINGLE", "TWIN", "TRIPLET", "QUADRUPLET", "MULTIPLET"}),
    (BreedingMethod, {"NATURAL", "AI", "AI_SEXED"}),
    # UNASSESSED closes a service that could never be scanned because the doe
    # left the herd; it asserts neither a conception nor a failure to conceive.
    (BreedingOutcome, {"PENDING", "CONFIRMED_PREGNANT", "FAILED", "ABORTED", "UNASSESSED"}),
    # SPEC §KiddingRecord originally defined ease as NORMAL/ASSISTED/DIFFICULT;
    # CAESAREAN was re-admitted with the husbandry-standards vocabulary wave.
    (KiddingEase, {"NORMAL", "ASSISTED", "DIFFICULT", "CAESAREAN"}),
    (KidStatus, {"ALIVE", "STILLBORN", "DIED"}),
    (
        HealthEventType,
        {"VACCINE", "DEWORMING", "TREATMENT", "FOOTBATH", "VITAMIN", "EXAM", "FECAL_EXAM"},
    ),
    (IngredientCategory, {"ROUGHAGE_WET", "ROUGHAGE_DRY", "CONCENTRATE"}),
    (FeedingShift, {"MORNING", "AFTERNOON", "NIGHT"}),
    (TransactionType, {"INCOME", "EXPENSE"}),
    (
        TransactionCategory,
        {
            "ANIMAL_SALE",
            "ANIMAL_PURCHASE",
            "FEED",
            "MEDICINE",
            "VET",
            "LABOUR",
            "EQUIPMENT",
            "MANURE",
            "OTHER",
        },
    ),
    (TaskStatus, {"PENDING", "DONE", "SKIPPED", "VERIFIED"}),
    (
        TaskCategory,
        {
            "VACCINE",
            "DEWORMING",
            "ULTRASOUND",
            "KIDDING_DUE",
            "WEANING",
            "BUCKET_MOVE",
            "QUARANTINE",
            "FEED",
            "CLEANING",
            "OTHER",
            "KIDDING_WATCH",
            "BIRTHING_KIT",
            "HEALTH_CHECK",
            "HEAT_WATCH",
            "HOOF_TRIMMING",
            "SPRAYING",
            "DISINFECTION",
            "WEIGHING",
            "REBREED",
            "BUCK_ROTATION",
            "INSURANCE",
        },
    ),
]


@pytest.mark.parametrize(
    ("enum_cls", "values"), ENUM_CASES, ids=[c.__name__ for c, _ in ENUM_CASES]
)
def test_enum_values_match_spec(enum_cls: type, values: set[str]) -> None:
    assert {e.value for e in enum_cls} == values
    assert all(isinstance(e.value, str) for e in enum_cls)


def test_enums_are_str_mixins() -> None:
    assert Bucket.QUARANTINE == "QUARANTINE"
    assert AnimalStatus.ACTIVE.value == "ACTIVE"
    assert isinstance(TaskStatus.PENDING, str)


def test_breed_constants_match_spec() -> None:
    # SPEC §Breed constants (Osmanabadi)
    assert GESTATION_DAYS == 150
    assert KIDDING_WINDOW_DAYS == (145, 155)
    assert KIDDING_WINDOW_DAYS[0] <= GESTATION_DAYS <= KIDDING_WINDOW_DAYS[1]
    assert ULTRASOUND_AFTER_BREEDING_DAYS == 32
    assert MIN_BREEDING_AGE_MONTHS == 10
    assert MIN_BREEDING_WEIGHT_KG == 22.0
    assert WEANING_DAYS == 60
    assert BUCK_ROTATION_DAYS == 7
    assert BUCK_DOE_RATIO == 20
    assert MEAT_SALE_AGE_MONTHS == (8, 9)
    assert MEAT_SALE_WEIGHT_KG == (24.0, 28.0)
    assert MAX_FAILED_CYCLES_BEFORE_CULL == 2


def test_verification_required_categories() -> None:
    assert VERIFICATION_REQUIRED_CATEGORIES == ("CLEANING",)
    assert set(VERIFICATION_REQUIRED_CATEGORIES) <= {c.value for c in TaskCategory}


def test_breeding_ready_buckets_match_spec() -> None:
    # SPEC: breeding-ready doe lives in FOUNDATION / FEMALE_KIDS / RESTING
    assert BREEDING_READY_BUCKETS == (Bucket.FOUNDATION, Bucket.FEMALE_KIDS, Bucket.RESTING)


def test_shift_split_covers_all_shifts_and_sums_to_100pct() -> None:
    assert set(SHIFT_SPLIT) == set(FeedingShift)
    assert sum(SHIFT_SPLIT.values()) == pytest.approx(1.0)
    assert SHIFT_SPLIT[FeedingShift.MORNING] == 0.40
    assert SHIFT_SPLIT[FeedingShift.AFTERNOON] == 0.20
    assert SHIFT_SPLIT[FeedingShift.NIGHT] == 0.40


# ---------------------------------------------------------------------------
# app.models — in-memory entity helpers
# ---------------------------------------------------------------------------
def make_animal_object(**overrides: object) -> Animal:
    """An in-memory (never persisted) breeding-ready doe, 12 months / 25 kg."""
    animal = Animal(
        farm_id=1,
        tag_number="D-001",
        sex=Sex.F.value,
        source=AnimalSource.PURCHASED.value,
        status=AnimalStatus.ACTIVE.value,
        current_bucket=Bucket.FOUNDATION.value,
        date_of_birth=add_months(today(), -12),
        breedings_as_doe=[],
        weight_records=[WeightRecord(date=today(), weight_kg=25.0)],
        bucket_moves=[],
    )
    for key, value in overrides.items():
        setattr(animal, key, value)
    return animal


def make_breeding_object(outcome: str, **overrides: object) -> BreedingRecord:
    br = BreedingRecord(
        farm_id=1,
        doe_id=1,
        buck_id=2,
        breeding_date=today() - timedelta(days=60),
        outcome=outcome,
    )
    for key, value in overrides.items():
        setattr(br, key, value)
    return br


def make_move_object(days_ago: int, **overrides: object) -> BucketMove:
    move = BucketMove(
        animal_id=1,
        from_bucket=None,
        to_bucket=Bucket.FOUNDATION.value,
        moved_at=datetime.combine(today() - timedelta(days=days_ago), datetime.min.time()),
    )
    for key, value in overrides.items():
        setattr(move, key, value)
    return move


# ---------------------------------------------------------------------------
# app.models — User / Role / Task helpers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Pavan", "Pavan"),
        ("", "owner@farm.in"),  # empty name falls back to email
        (None, "owner@farm.in"),
    ],
)
def test_user_display_name(name: str | None, expected: str) -> None:
    user = User(email="owner@farm.in", name=name, password_hash="x")
    assert user.display_name == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["animals.view", "tasks.view"]', {"animals.view", "tasks.view"}),
        ("[]", set()),
        ("", set()),
        ("not-json", set()),  # malformed JSON must not crash permission checks
        ('["a", 1, null, "b"]', {"a", "b"}),  # non-string entries filtered
    ],
)
def test_role_permission_set(raw: str, expected: set[str]) -> None:
    role = Role(farm_id=1, name="R", permissions=raw)
    assert role.permission_set() == expected


@pytest.mark.parametrize(
    ("category", "status", "expected"),
    [
        ("CLEANING", "DONE", True),  # DONE means "awaiting verification" for cleaning
        ("CLEANING", "SKIPPED", False),
        ("VACCINE", "DONE", False),
        ("BUCKET_MOVE", "DONE", False),
        ("OTHER", "DONE", False),
    ],
)
def test_task_needs_verification(category: str, status: str, expected: bool) -> None:
    task = Task(farm_id=1, title="t", due_date=today(), category=category, status=status)
    assert task.needs_verification is expected


# ---------------------------------------------------------------------------
# app.models — Animal computed properties
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("dob", "est", "expected"),
    [
        (date(2020, 5, 1), date(2021, 5, 1), date(2020, 5, 1)),  # real DOB wins
        (None, date(2021, 5, 1), date(2021, 5, 1)),  # estimated fallback
        (None, None, None),
    ],
)
def test_effective_dob(dob: date | None, est: date | None, expected: date | None) -> None:
    animal = make_animal_object(date_of_birth=dob, estimated_dob=est)
    assert animal.effective_dob == expected


def test_age_months_none_without_any_dob() -> None:
    assert make_animal_object(date_of_birth=None, estimated_dob=None).age_months is None


def test_age_months_exact_month_boundary() -> None:
    # born exactly 10 months ago today → 10 months
    assert make_animal_object(date_of_birth=add_months(today(), -10)).age_months == 10


def test_age_months_one_day_short_of_boundary() -> None:
    # born 10 months ago + 1 day → not yet 10 full months
    dob = add_months(today(), -10) + timedelta(days=1)
    assert make_animal_object(date_of_birth=dob).age_months == 9


def test_age_months_one_day_past_boundary() -> None:
    dob = add_months(today(), -10) - timedelta(days=1)
    assert make_animal_object(date_of_birth=dob).age_months == 10


def test_age_months_born_today_is_zero() -> None:
    assert make_animal_object(date_of_birth=today()).age_months == 0


def test_age_months_future_dob_clamps_to_zero() -> None:
    assert make_animal_object(date_of_birth=today() + timedelta(days=10)).age_months == 0


def test_age_months_uses_estimated_dob_fallback() -> None:
    animal = make_animal_object(date_of_birth=None, estimated_dob=add_months(today(), -7))
    assert animal.age_months == 7


def test_age_months_leap_day_birthday() -> None:
    # born 2024-02-29: age in whole months must never go negative or crash
    animal = make_animal_object(date_of_birth=date(2024, 2, 29))
    assert animal.age_months is not None and animal.age_months >= 0


def test_latest_weight_none_without_records() -> None:
    assert make_animal_object(weight_records=[]).latest_weight is None


def test_latest_weight_picks_most_recent_date() -> None:
    older = WeightRecord(date=today() - timedelta(days=30), weight_kg=20.0)
    newer = WeightRecord(date=today() - timedelta(days=5), weight_kg=24.0)
    animal = make_animal_object(weight_records=[older, newer])
    assert animal.latest_weight is newer
    assert animal.latest_weight_kg == 24.0


def test_latest_weight_same_date_picks_higher_id() -> None:
    first = WeightRecord(date=today(), weight_kg=20.0)
    first.id = 1
    second = WeightRecord(date=today(), weight_kg=21.0)
    second.id = 2
    animal = make_animal_object(weight_records=[second, first])
    assert animal.latest_weight is second


def test_latest_weight_kg_falls_back_to_birth_weight() -> None:
    animal = make_animal_object(weight_records=[], birth_weight=2.6)
    assert animal.latest_weight_kg == 2.6


def test_latest_weight_kg_none_when_no_records_and_no_birth_weight() -> None:
    assert make_animal_object(weight_records=[], birth_weight=None).latest_weight_kg is None


def test_latest_weight_kg_on_picks_latest_record_and_breaks_date_ties_by_id() -> None:
    """The as-of lookup orders by (date, id or 0), exactly like the SQL it mirrors."""
    older = WeightRecord(date=today() - timedelta(days=30), weight_kg=20.0)
    newer = WeightRecord(date=today() - timedelta(days=5), weight_kg=24.0)
    animal = make_animal_object(weight_records=[older, newer])
    assert animal.latest_weight_kg_on(today()) == 24.0
    assert animal.latest_weight_kg_on(today() - timedelta(days=10)) == 20.0

    unsaved = WeightRecord(date=today(), weight_kg=30.0)  # id stays None
    saved = WeightRecord(date=today(), weight_kg=25.0)
    saved.id = 1
    tied = make_animal_object(weight_records=[unsaved, saved])
    assert tied.latest_weight_kg_on(today()) == 25.0


def test_latest_weight_kg_on_birth_weight_fallback_respects_the_reference_date() -> None:
    """Birth weight only counts from the birth date on; an unknown DOB still counts."""
    dob = today() - timedelta(days=100)
    animal = make_animal_object(weight_records=[], birth_weight=2.6, date_of_birth=dob)
    assert animal.latest_weight_kg_on(today()) == 2.6
    assert animal.latest_weight_kg_on(dob) == 2.6
    assert animal.latest_weight_kg_on(dob - timedelta(days=1)) is None

    unknown = make_animal_object(
        weight_records=[], birth_weight=2.6, date_of_birth=None, estimated_dob=None
    )
    assert unknown.latest_weight_kg_on(today()) == 2.6


def test_last_bucket_move_none_without_moves() -> None:
    assert make_animal_object(bucket_moves=[]).last_bucket_move is None


def test_last_bucket_move_picks_most_recent() -> None:
    older = make_move_object(10)
    newer = make_move_object(2)
    animal = make_animal_object(bucket_moves=[older, newer])
    assert animal.last_bucket_move is newer


def test_days_in_current_bucket_from_last_move() -> None:
    animal = make_animal_object(bucket_moves=[make_move_object(5)])
    assert animal.days_in_current_bucket == 5


def test_days_in_current_bucket_move_today_is_zero() -> None:
    animal = make_animal_object(bucket_moves=[make_move_object(0)])
    assert animal.days_in_current_bucket == 0


def test_days_in_current_bucket_future_move_clamps_to_zero() -> None:
    animal = make_animal_object(bucket_moves=[make_move_object(-3)])
    assert animal.days_in_current_bucket == 0


def test_days_in_current_bucket_falls_back_to_created_at() -> None:
    animal = make_animal_object(bucket_moves=[])
    animal.created_at = utcnow() - timedelta(days=3)
    assert animal.days_in_current_bucket == 3


def test_days_in_current_bucket_no_move_no_created_at_is_zero() -> None:
    animal = make_animal_object(bucket_moves=[])
    animal.created_at = None
    assert animal.days_in_current_bucket == 0


def test_today_uses_the_requested_business_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    """One UTC instant can belong to different farm-local calendar dates."""
    import app.utils as utils

    fixed = datetime(2026, 3, 15, 23, 30, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            assert tz is not None
            return fixed.astimezone(tz)

    monkeypatch.setattr(utils, "datetime", FrozenDatetime)
    assert today() == date(2026, 3, 16)  # default: Asia/Kolkata
    assert today("America/Phoenix") == date(2026, 3, 15)


def test_animal_date_helpers_accept_the_farm_business_date() -> None:
    boundary = date(2026, 3, 16)
    animal = make_animal_object(
        date_of_birth=date(2025, 5, 16),
        weight_records=[WeightRecord(date=boundary, weight_kg=25.0)],
        bucket_moves=[],
    )
    animal.created_at = datetime(2026, 3, 15, 0, 0)

    assert animal.age_months_on(boundary - timedelta(days=1)) == 9
    assert animal.age_months_on(boundary) == 10
    assert animal.days_in_current_bucket_on(boundary - timedelta(days=1)) == 0
    assert animal.days_in_current_bucket_on(boundary) == 1
    assert animal.is_breeding_ready_on(boundary - timedelta(days=1)) is False
    assert animal.is_breeding_ready_on(boundary) is True


def test_bucket_timestamp_is_converted_from_utc_to_the_farm_date() -> None:
    moved_at = datetime(2026, 3, 15, 20, 0)  # naive UTC by storage contract
    animal = make_animal_object(
        bucket_moves=[
            BucketMove(
                animal_id=1,
                from_bucket=None,
                to_bucket=Bucket.RESTING.value,
                moved_at=moved_at,
            )
        ]
    )

    assert business_date(moved_at, "Asia/Kolkata") == date(2026, 3, 16)
    assert business_date(moved_at, "America/Phoenix") == date(2026, 3, 15)
    assert animal.days_in_current_bucket_on(date(2026, 3, 16), "Asia/Kolkata") == 0
    assert animal.days_in_current_bucket_on(date(2026, 3, 16), "America/Phoenix") == 1


@pytest.mark.parametrize(
    "outcome",
    [BreedingOutcome.PENDING.value, BreedingOutcome.FAILED.value, BreedingOutcome.ABORTED.value],
)
def test_is_currently_pregnant_false_for_non_confirmed_outcomes(outcome: str) -> None:
    animal = make_animal_object(breedings_as_doe=[make_breeding_object(outcome)])
    assert animal.is_currently_pregnant is False


def test_is_currently_pregnant_true_for_confirmed_without_kidding() -> None:
    animal = make_animal_object(
        breedings_as_doe=[make_breeding_object(BreedingOutcome.CONFIRMED_PREGNANT.value)]
    )
    assert animal.is_currently_pregnant is True


def test_is_currently_pregnant_false_without_breedings() -> None:
    assert make_animal_object(breedings_as_doe=[]).is_currently_pregnant is False


@pytest.mark.parametrize("bucket", ["FOUNDATION", "FEMALE_KIDS", "RESTING"])
def test_is_breeding_ready_eligible_buckets(bucket: str) -> None:
    assert make_animal_object(current_bucket=bucket).is_breeding_ready is True


@pytest.mark.parametrize(
    "bucket",
    [
        "QUARANTINE",
        "BREEDING",
        "PREGNANCY_EARLY",
        "PREGNANCY_LATE",
        "DELIVERY",
        "RECOVERY",
        "MALE_KIDS",
    ],
)
def test_is_breeding_ready_ineligible_buckets(bucket: str) -> None:
    assert make_animal_object(current_bucket=bucket).is_breeding_ready is False


@pytest.mark.parametrize("status", ["SOLD", "DEAD", "CULLED"])
def test_is_breeding_ready_requires_active_status(status: str) -> None:
    assert make_animal_object(status=status).is_breeding_ready is False


def test_is_breeding_ready_rejects_male() -> None:
    assert make_animal_object(sex=Sex.M.value).is_breeding_ready is False


def test_is_breeding_ready_age_exactly_10_months() -> None:
    assert make_animal_object(date_of_birth=add_months(today(), -10)).is_breeding_ready is True


def test_is_breeding_ready_age_one_day_short_of_10_months() -> None:
    dob = add_months(today(), -10) + timedelta(days=1)
    assert make_animal_object(date_of_birth=dob).is_breeding_ready is False


def test_is_breeding_ready_unknown_age() -> None:
    animal = make_animal_object(date_of_birth=None, estimated_dob=None)
    assert animal.is_breeding_ready is False


def test_is_breeding_ready_weight_exactly_22kg() -> None:
    animal = make_animal_object(weight_records=[WeightRecord(date=today(), weight_kg=22.0)])
    assert animal.is_breeding_ready is True


def test_is_breeding_ready_weight_just_below_22kg() -> None:
    animal = make_animal_object(weight_records=[WeightRecord(date=today(), weight_kg=21.99)])
    assert animal.is_breeding_ready is False


def test_is_breeding_ready_unknown_weight() -> None:
    animal = make_animal_object(weight_records=[], birth_weight=None)
    assert animal.is_breeding_ready is False


def test_is_breeding_ready_rejects_pregnant_doe() -> None:
    animal = make_animal_object(
        breedings_as_doe=[make_breeding_object(BreedingOutcome.CONFIRMED_PREGNANT.value)]
    )
    assert animal.is_breeding_ready is False


def test_is_breeding_ready_after_failed_breeding() -> None:
    # a FAILED cycle is not a pregnancy — the doe stays eligible
    animal = make_animal_object(
        breedings_as_doe=[make_breeding_object(BreedingOutcome.FAILED.value)]
    )
    assert animal.is_breeding_ready is True


@pytest.mark.parametrize("flag", ["movement_restricted", "suspected_scheduled_disease"])
def test_breeding_predicates_fail_closed_under_a_movement_hold(flag: str) -> None:
    """Either hold alone disqualifies an otherwise-ready doe — neither may be ignored."""
    animal = make_animal_object(**{flag: True})
    assert animal.is_breeding_ready is False
    assert animal.is_breeding_eligible is False


def test_is_breeding_eligible_rejects_male() -> None:
    """Re-service eligibility keeps the same sex guard as first-service readiness."""
    assert make_animal_object(sex=Sex.M.value).is_breeding_eligible is False


@pytest.mark.parametrize("status", ["SOLD", "DEAD", "CULLED"])
def test_is_breeding_eligible_requires_active_status(status: str) -> None:
    """A sold/dead/culled doe is never eligible, whatever her age and weight."""
    assert make_animal_object(status=status).is_breeding_eligible is False


def test_is_breeding_eligible_age_exactly_10_months() -> None:
    """10 months is inclusive: the minimum breeding age passes, not just exceeds."""
    assert make_animal_object(date_of_birth=add_months(today(), -10)).is_breeding_eligible is True


def test_is_breeding_eligible_age_one_day_short_of_10_months() -> None:
    """One day under the age floor fails; the conjunction may not degrade to an or."""
    dob = add_months(today(), -10) + timedelta(days=1)
    assert make_animal_object(date_of_birth=dob).is_breeding_eligible is False


def test_is_breeding_eligible_unknown_age() -> None:
    """No DOB and no estimate means eligibility fails closed rather than crashing."""
    animal = make_animal_object(date_of_birth=None, estimated_dob=None)
    assert animal.is_breeding_eligible is False


def test_is_breeding_eligible_weight_exactly_22kg() -> None:
    """22 kg is inclusive: the minimum breeding weight passes, not just exceeds."""
    animal = make_animal_object(weight_records=[WeightRecord(date=today(), weight_kg=22.0)])
    assert animal.is_breeding_eligible is True


def test_is_breeding_eligible_weight_just_below_22kg() -> None:
    """A hair under the weight floor fails, even with age and status satisfied."""
    animal = make_animal_object(weight_records=[WeightRecord(date=today(), weight_kg=21.99)])
    assert animal.is_breeding_eligible is False


def test_is_breeding_eligible_rejects_pregnant_doe() -> None:
    """A confirmed-pregnant doe is not re-serviceable, however mature she is."""
    animal = make_animal_object(
        breedings_as_doe=[make_breeding_object(BreedingOutcome.CONFIRMED_PREGNANT.value)]
    )
    assert animal.is_breeding_eligible is False


@pytest.mark.parametrize(
    ("tag", "name", "expected"),
    [
        ("A-001", None, "A-001"),
        ("A-001", "", "A-001"),  # blank name treated like no name
        ("A-001", "Kaveri", "A-001 · Kaveri"),
        ("🐐-1", "ఆకు", "🐐-1 · ఆకు"),  # unicode tags/names render fine
    ],
)
def test_animal_display_name(tag: str, name: str | None, expected: str) -> None:
    animal = make_animal_object(tag_number=tag, name=name)
    assert animal.display_name == expected


# ---------------------------------------------------------------------------
# app.models — computed domain logic (date math, conception rate, quarantine)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("breeding_date", "expected"),
    [
        (date(2026, 1, 10), date(2026, 6, 9)),  # SPEC: expected kidding = +150d
        (date(2024, 2, 29), date(2024, 7, 28)),  # bred on a leap day
        (date(2025, 11, 1), date(2026, 3, 31)),  # crosses a year boundary
        (date(2026, 12, 20), date(2027, 5, 19)),
    ],
)
def test_expected_kidding_date(breeding_date: date, expected: date) -> None:
    assert expected_kidding_date(breeding_date) == expected


@pytest.mark.parametrize(
    ("breeding_date", "expected"),
    [
        (date(2026, 1, 10), date(2026, 2, 11)),  # SPEC: ultrasound planned +32d
        (date(2024, 12, 31), date(2025, 2, 1)),  # crosses a year boundary
        (date(2024, 1, 31), date(2024, 3, 3)),  # crosses a leap February
    ],
)
def test_planned_ultrasound_date(breeding_date: date, expected: date) -> None:
    assert planned_ultrasound_date(breeding_date) == expected


def _br_with_outcome(outcome: str) -> BreedingRecord:
    return make_breeding_object(outcome)


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        ([], None),  # no records at all
        (["PENDING"], None),  # pending excluded → nothing completed
        (["CONFIRMED_PREGNANT"], 100.0),
        (["FAILED"], 0.0),
        (["CONFIRMED_PREGNANT", "CONFIRMED_PREGNANT", "FAILED"], 66.7),
        (["CONFIRMED_PREGNANT", "FAILED", "FAILED"], 33.3),
        # An ABORTED record was CONFIRMED_PREGNANT first (mark_aborted refuses
        # any other source state), so it conceived; the loss is a separate fact.
        (["CONFIRMED_PREGNANT", "ABORTED"], 100.0),
        (["ABORTED"], 100.0),
        (["ABORTED", "FAILED"], 50.0),
        (["CONFIRMED_PREGNANT", "FAILED", "PENDING"], 50.0),  # pending excluded
    ],
)
def test_conception_rate(outcomes: list[str], expected: float | None) -> None:
    records = [_br_with_outcome(o) for o in outcomes]
    assert conception_rate(records) == expected


def _batch(
    supplier: str | None = "Kurnool Traders", batch_date: date = date(2026, 3, 1)
) -> PurchaseBatch:
    batch = PurchaseBatch(farm_id=1, date=batch_date, supplier=supplier, count=50)
    batch.id = 7
    return batch


def test_quarantine_schedule_has_11_steps() -> None:
    assert len(quarantine_schedule(_batch())) == 11


@pytest.mark.parametrize(
    ("index", "day_offset", "category"),
    [
        (0, 1, "QUARANTINE"),  # SPEC §45-Day Quarantine Protocol
        (1, 1, "QUARANTINE"),
        (2, 4, "DEWORMING"),
        (3, 5, "QUARANTINE"),
        (4, 10, "VACCINE"),
        (5, 13, "QUARANTINE"),
        (6, 20, "VACCINE"),
        (7, 30, "VACCINE"),
        (8, 30, "QUARANTINE"),
        (9, 40, "VACCINE"),
        (10, 45, "BUCKET_MOVE"),
    ],
)
def test_quarantine_schedule_protocol_steps(index: int, day_offset: int, category: str) -> None:
    schedule = quarantine_schedule(_batch())
    item = schedule[index]
    # protocol day N is due N-1 days after the arrival date
    assert item["due_date"] == date(2026, 3, 1) + timedelta(days=day_offset - 1)
    assert item["category"] == category


def test_quarantine_schedule_titles_reference_only_the_batch_id() -> None:
    """RT-HIJ-3: supplier identity is procurement data gated behind
    purchases.view, while titles surface on the task board to every protocol
    role — so only the opaque batch id may travel in the title."""
    schedule = quarantine_schedule(_batch(supplier="Kurnool Traders"))
    assert all(str(item["title"]).startswith("[Batch #7] ") for item in schedule)
    assert all("Kurnool Traders" not in str(item["title"]) for item in schedule)


def test_quarantine_schedule_missing_supplier_matches_the_same_format() -> None:
    schedule = quarantine_schedule(_batch(supplier=None))
    assert all(str(item["title"]).startswith("[Batch #7] ") for item in schedule)


def test_quarantine_schedule_blank_supplier_matches_the_same_format() -> None:
    schedule = quarantine_schedule(_batch(supplier="   "))
    assert all(str(item["title"]).startswith("[Batch #7] ") for item in schedule)


def test_quarantine_schedule_titles_fit_the_task_title_capacity() -> None:
    batch = _batch(supplier="S" * 120)
    batch.id = 2_147_483_647
    schedule = quarantine_schedule(batch)
    assert all(len(item["title"]) <= 200 for item in schedule)
    assert all(item["title"].startswith(f"[Batch #{batch.id}] ") for item in schedule)
    assert schedule[0]["title"].endswith(QUARANTINE_PROTOCOL[0][2])


def test_quarantine_schedule_final_step_releases_to_foundation() -> None:
    last = quarantine_schedule(_batch())[-1]
    assert last["due_date"] == date(2026, 4, 14)  # arrival + 44
    assert "FOUNDATION" in str(last["title"])


def test_quarantine_schedule_dates_never_go_backwards() -> None:
    schedule = quarantine_schedule(_batch())
    due_dates = [item["due_date"] for item in schedule]
    assert due_dates == sorted(due_dates)


# ---------------------------------------------------------------------------
# app.services — move_animal (synchronous, no I/O: fake session suffices)
# ---------------------------------------------------------------------------
class FakeSession:
    """Duck-typed stand-in for AsyncSession: move_animal only calls .add()."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)


def test_move_animal_records_move_and_updates_bucket() -> None:
    db = FakeSession()
    animal = make_animal_object(current_bucket=Bucket.FOUNDATION.value)
    move_animal(db, animal, Bucket.BREEDING.value, "Breeding-ready", created_by_id=9)
    assert animal.current_bucket == Bucket.BREEDING.value
    assert len(db.added) == 1
    move = db.added[0]
    assert isinstance(move, BucketMove)
    assert move.from_bucket == Bucket.FOUNDATION.value
    assert move.to_bucket == Bucket.BREEDING.value
    assert move.reason == "Breeding-ready"
    assert move.created_by_id == 9


@pytest.mark.parametrize("status", ["SOLD", "DEAD", "CULLED"])
def test_move_animal_never_moves_non_active_animals(status: str) -> None:
    db = FakeSession()
    animal = make_animal_object(status=status)
    move_animal(db, animal, Bucket.BREEDING.value, "forged post")
    assert animal.current_bucket == Bucket.FOUNDATION.value  # unchanged
    assert db.added == []


def test_move_animal_same_bucket_is_noop() -> None:
    db = FakeSession()
    animal = make_animal_object(current_bucket=Bucket.RESTING.value)
    move_animal(db, animal, Bucket.RESTING.value, "redundant")
    assert db.added == []


def test_move_animal_blank_reason_stored_as_none() -> None:
    db = FakeSession()
    animal = make_animal_object()
    move_animal(db, animal, Bucket.BREEDING.value, "")
    move = db.added[0]
    assert isinstance(move, BucketMove)
    assert move.reason is None


# ---------------------------------------------------------------------------
# app.services — recipe_for_animal (SPEC §Feed allocation per bucket)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("bucket", "recipe"),
    [
        ("FOUNDATION", "LACTATING_60_40"),
        ("FEMALE_KIDS", "LACTATING_60_40"),
        ("PREGNANCY_LATE", "LACTATING_60_40"),
        ("RECOVERY", "LACTATING_60_40"),
        ("DELIVERY", "LACTATING_60_40"),
        ("BREEDING", "MAINTENANCE_75_25"),
        ("PREGNANCY_EARLY", "MAINTENANCE_75_25"),
        ("NO_SUCH_BUCKET", "MAINTENANCE_75_25"),  # unknown bucket → safe default
    ],
)
def test_recipe_for_animal_static_buckets(bucket: str, recipe: str) -> None:
    animal = make_animal_object(current_bucket=bucket, bucket_moves=[])
    animal.created_at = None
    assert recipe_for_animal(animal) == recipe


@pytest.mark.parametrize(
    ("days_in_bucket", "recipe"),
    [
        (0, "DRY_ROUGHAGE_ONLY"),  # SPEC: quarantine days 1–3 dry roughage only
        (1, "DRY_ROUGHAGE_ONLY"),
        (2, "DRY_ROUGHAGE_ONLY"),
        (3, "MAINTENANCE_75_25"),  # day 4 → transition to maintenance
        (44, "MAINTENANCE_75_25"),
    ],
)
def test_recipe_for_animal_quarantine_days_1_to_3_dry_roughage(
    days_in_bucket: int, recipe: str
) -> None:
    animal = make_animal_object(
        current_bucket=Bucket.QUARANTINE.value, bucket_moves=[make_move_object(days_in_bucket)]
    )
    assert recipe_for_animal(animal) == recipe


def test_recipe_for_animal_uses_explicit_farm_business_date() -> None:
    move = BucketMove(
        animal_id=1,
        from_bucket=None,
        to_bucket=Bucket.QUARANTINE.value,
        moved_at=datetime(2026, 3, 13, 23, 30),
    )
    animal = make_animal_object(
        current_bucket=Bucket.QUARANTINE.value,
        bucket_moves=[move],
    )

    assert recipe_for_animal(animal, date(2026, 3, 15), "UTC") == "DRY_ROUGHAGE_ONLY"
    assert recipe_for_animal(animal, date(2026, 3, 16), "UTC") == "MAINTENANCE_75_25"


def test_recipe_for_animal_resolves_an_omitted_date_in_the_named_timezone(monkeypatch) -> None:
    observed_timezones: list[str] = []

    def fake_today(timezone_name: str) -> date:
        observed_timezones.append(timezone_name)
        return date(2026, 3, 15)

    monkeypatch.setattr(feeding_service, "today", fake_today)
    animal = make_animal_object(current_bucket=Bucket.FOUNDATION.value, bucket_moves=[])

    recipe_for_animal(animal, timezone_name="America/Phoenix")

    assert observed_timezones == ["America/Phoenix"]


@pytest.mark.parametrize(
    ("days_in_bucket", "recipe"),
    [
        (0, "MAINTENANCE_75_25"),  # SPEC: RESTING days 1–10 dry-off 75/25
        (9, "MAINTENANCE_75_25"),
        (10, "FLUSH_70_30"),  # days ~10–30 flush 70/30
        (29, "FLUSH_70_30"),
    ],
)
def test_recipe_for_animal_resting_flush_switch_at_day_10(days_in_bucket: int, recipe: str) -> None:
    animal = make_animal_object(
        current_bucket=Bucket.RESTING.value, bucket_moves=[make_move_object(days_in_bucket)]
    )
    assert recipe_for_animal(animal) == recipe


@pytest.mark.parametrize(
    ("age_days", "recipe"),
    [
        (0, "LACTATING_60_40"),  # SPEC: male kids frame-builder till day 90
        (90, "LACTATING_60_40"),
        (91, "FATTENING_50_50"),  # day 91+ fattening
        (240, "FATTENING_50_50"),
    ],
)
def test_recipe_for_animal_male_kids_fattening_at_day_91(age_days: int, recipe: str) -> None:
    animal = make_animal_object(
        current_bucket=Bucket.MALE_KIDS.value,
        date_of_birth=today() - timedelta(days=age_days),
        bucket_moves=[],
    )
    assert recipe_for_animal(animal) == recipe


def test_recipe_for_animal_male_kid_unknown_age_defaults_to_fattening() -> None:
    animal = make_animal_object(
        current_bucket=Bucket.MALE_KIDS.value,
        date_of_birth=None,
        estimated_dob=None,
        bucket_moves=[],
    )
    assert recipe_for_animal(animal) == "FATTENING_50_50"


# ---------------------------------------------------------------------------
# app.schemas — auth
# ---------------------------------------------------------------------------
def test_register_normalizes_email_case_and_whitespace() -> None:
    user = RegisterIn(email="  Owner@Farm.IN ", password="secret123")
    assert user.email == "owner@farm.in"


@pytest.mark.parametrize("email", ["", "   ", "no-at-sign", "plain.address", "a@b"])
def test_register_rejects_malformed_email(email: str) -> None:
    with pytest.raises(ValidationError):
        RegisterIn(email=email, password="secret123")


@pytest.mark.parametrize("email", ["a@b.co", "x@y.z", "owner+farm@farm.in"])
def test_register_accepts_emails_with_at_sign(email: str) -> None:
    assert RegisterIn(email=email, password="secret123").email == email


def test_register_rejects_empty_password() -> None:
    with pytest.raises(ValidationError):
        RegisterIn(email="a@b.in", password="")


@pytest.mark.parametrize("missing", ["email", "password"])
def test_register_requires_email_and_password(missing: str) -> None:
    payload = {"email": "a@b.in", "password": "secret123"}
    del payload[missing]
    with pytest.raises(ValidationError):
        RegisterIn(**payload)


def test_register_name_optional_and_unicode() -> None:
    assert RegisterIn(email="a@b.in", password="x").name is None
    assert RegisterIn(email="a@b.in", password="x", name="పవన్ 🐐").name == "పవన్ 🐐"


def test_login_normalizes_email() -> None:
    assert LoginIn(email=" A@B.IN ", password="x").email == "a@b.in"


def test_login_rejects_bad_email() -> None:
    with pytest.raises(ValidationError):
        LoginIn(email="nope", password="x")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "A"),
        ("name", "F" * 120),
        ("name", "మా ఫారమ్ 🐐"),
        ("location", None),
        ("location", "L" * 120),
    ],
)
def test_farm_create_valid(field: str, value: object) -> None:
    FarmCreateIn(**({"name": "Alpha"} | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "F" * 121),
        ("location", "L" * 201),
    ],
)
def test_farm_create_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        FarmCreateIn(**({"name": "Alpha"} | {field: value}))


def test_farm_create_requires_name() -> None:
    with pytest.raises(ValidationError):
        FarmCreateIn()


# ---------------------------------------------------------------------------
# app.schemas — animals
# ---------------------------------------------------------------------------
VALID_ANIMAL = {
    "tag_number": "A-001",
    "sex": "F",
    "source": "PURCHASED",
    "current_bucket": "FOUNDATION",
}
BUCKETS = [
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tag_number", "T"),
        ("tag_number", "T" * 50),
        ("tag_number", "🐐-001"),
        ("tag_number", "'; DROP TABLE animals;--"),  # stored as data, never SQL
        ("name", None),
        ("name", "Kaveri"),
        ("name", "N" * 80),
        ("sex", "M"),
        ("sex", "F"),
        ("source", "BORN"),
        ("source", "PURCHASED"),
        ("breed", "Osmanabadi"),
        ("breed", "B" * 60),
        ("birth_type", None),
        ("birth_type", "SINGLE"),
        ("birth_type", "TWIN"),
        ("birth_type", "TRIPLET"),
        ("birth_weight", None),
        ("birth_weight", 0.0),  # zero is non-negative
        ("birth_weight", 0.001),
        ("purchase_price", None),
        ("purchase_price", 0.0),
        ("purchase_price", 1e9),
        ("weight_kg", None),
        ("weight_kg", 0.001),
        ("date_of_birth", None),
        ("date_of_birth", TODAY.isoformat()),
        ("date_of_birth", "2000-02-29"),  # leap day
        ("estimated_dob", None),
        ("estimated_dob", TODAY.isoformat()),
        ("purchase_date", None),
        ("purchase_date", TODAY.isoformat()),
        ("seller_name", None),
        ("seller_name", "S" * 120),
        ("notes", None),
        ("notes", "n" * 4_000),
    ],
)
def test_animal_create_valid_variants(field: str, value: object) -> None:
    payload = VALID_ANIMAL | {field: value}
    if field in {"birth_type", "birth_weight"} and value is not None:
        payload["source"] = "BORN"
    if payload["source"] == "BORN":
        payload["historical_import_reason"] = "Existing-herd schema fixture"
    AnimalCreateIn(**payload)


@pytest.mark.parametrize("bucket", BUCKETS)
def test_animal_create_accepts_every_bucket(bucket: str) -> None:
    sex = "M" if bucket == "MALE_KIDS" else "F"
    assert (
        AnimalCreateIn(**(VALID_ANIMAL | {"current_bucket": bucket, "sex": sex})).current_bucket
        == bucket
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tag_number", ""),
        ("tag_number", "T" * 51),
        ("name", "N" * 81),
        ("sex", "X"),
        ("sex", "m"),  # case-sensitive
        ("sex", ""),
        ("source", "STOLEN"),
        ("source", "born"),
        ("current_bucket", "PASTURE"),
        ("current_bucket", "quarantine"),
        ("birth_type", "QUAD"),
        ("birth_weight", -0.001),
        ("birth_weight", NAN),
        ("birth_weight", INF),
        ("purchase_price", -0.01),
        ("purchase_price", NAN),
        ("weight_kg", 0.0),  # positive-only
        ("weight_kg", -1.0),
        ("weight_kg", NAN),
        ("weight_kg", INF),
        ("date_of_birth", GUARANTEED_FUTURE),
        ("estimated_dob", GUARANTEED_FUTURE),
        ("purchase_date", GUARANTEED_FUTURE),
        ("breed", "B" * 61),
        ("seller_name", "S" * 121),
    ],
)
def test_animal_create_invalid_variants(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AnimalCreateIn(**(VALID_ANIMAL | {field: value}))


@pytest.mark.parametrize("missing", ["sex", "source", "current_bucket"])
def test_animal_create_missing_required_fields(missing: str) -> None:
    payload = dict(VALID_ANIMAL)
    del payload[missing]
    with pytest.raises(ValidationError):
        AnimalCreateIn(**payload)


def test_animal_create_tag_optional_defaults_none() -> None:
    payload = dict(VALID_ANIMAL)
    del payload["tag_number"]
    assert AnimalCreateIn(**payload).tag_number is None


def test_animal_create_defaults_breed_to_species_default() -> None:
    # Empty means the species default (Osmanabadi), resolved by the create
    # endpoint.
    assert AnimalCreateIn(**VALID_ANIMAL).breed == ""


def test_animal_create_rejects_unknown_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AnimalCreateIn(**(VALID_ANIMAL | {"hacker_field": "evil", "farm_id": 999}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("weight_kg", 0.001),
        ("weight_kg", 500.0),
        ("bcs", None),
        ("bcs", 1),
        ("bcs", 5),
        ("date", None),
        ("date", TODAY.isoformat()),
        ("notes", None),
    ],
)
def test_weight_in_valid(field: str, value: object) -> None:
    WeightIn(**({"weight_kg": 25.0} | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("weight_kg", 0.0),
        ("weight_kg", -0.5),
        ("weight_kg", NAN),
        ("weight_kg", INF),
        ("weight_kg", "heavy"),
        ("bcs", 0),
        ("bcs", 6),
        ("bcs", -1),
        ("date", GUARANTEED_FUTURE),
    ],
)
def test_weight_in_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        WeightIn(**({"weight_kg": 25.0} | {field: value}))


def test_weight_in_requires_weight() -> None:
    with pytest.raises(ValidationError):
        WeightIn()


@pytest.mark.parametrize("bucket", BUCKETS)
def test_move_in_accepts_every_bucket(bucket: str) -> None:
    assert MoveIn(to_bucket=bucket).to_bucket == bucket


@pytest.mark.parametrize("bucket", ["PASTURE", "", "foundation"])
def test_move_in_rejects_unknown_buckets(bucket: str) -> None:
    with pytest.raises(ValidationError):
        MoveIn(to_bucket=bucket)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("new_status", "SOLD"),
        ("new_status", "DEAD"),
        ("new_status", "CULLED"),
        ("sale_price", None),
        ("sale_price", 0.0),
        ("date", None),
        ("date", TODAY.isoformat()),
        ("buyer_name", "B" * 120),
    ],
)
def test_status_change_valid(field: str, value: object) -> None:
    StatusChangeIn(**({"new_status": "SOLD"} | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("new_status", "ACTIVE"),  # status changes are terminal-only
        ("new_status", "LOST"),
        ("sale_price", -0.01),
        ("sale_price", NAN),
        ("date", GUARANTEED_FUTURE),
        ("buyer_name", "B" * 121),
    ],
)
def test_status_change_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        StatusChangeIn(**({"new_status": "SOLD"} | {field: value}))


@pytest.mark.parametrize("status", ["SOLD", "CULLED"])
@pytest.mark.parametrize("field", ["sale_price", "buyer_name"])
def test_sale_statuses_accept_sale_fields(status: str, field: str) -> None:
    value: object = 100.0 if field == "sale_price" else "Buyer"
    StatusChangeIn(**{"new_status": status, field: value})


@pytest.mark.parametrize("field", ["sale_price", "buyer_name"])
def test_dead_status_rejects_irrelevant_sale_fields(field: str) -> None:
    value: object = 100.0 if field == "sale_price" else "Buyer"
    with pytest.raises(ValidationError):
        StatusChangeIn(**{"new_status": "DEAD", field: value})


# ---------------------------------------------------------------------------
# app.schemas — breeding
# ---------------------------------------------------------------------------
VALID_BREEDING = {"doe_id": 1, "buck_id": 2, "breeding_date": TODAY.isoformat()}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("heat_cycle_number", 1),
        ("heat_cycle_number", 99),
        ("doe_id", MAX_ID),
        ("buck_id", 1),  # same id as doe: schema allows, router/service guards
    ],
)
def test_breeding_create_valid(field: str, value: object) -> None:
    BreedingCreateIn(**(VALID_BREEDING | {field: value}))


def test_breeding_create_defaults_heat_cycle_to_1() -> None:
    assert BreedingCreateIn(**VALID_BREEDING).heat_cycle_number == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("heat_cycle_number", 0),
        ("heat_cycle_number", 100),
        ("heat_cycle_number", -1),
        ("doe_id", 0),
        ("doe_id", -3),
        ("doe_id", MAX_ID + 1),
        ("buck_id", 0),
        ("breeding_date", GUARANTEED_FUTURE),
    ],
)
def test_breeding_create_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        BreedingCreateIn(**(VALID_BREEDING | {field: value}))


@pytest.mark.parametrize("missing", ["doe_id", "buck_id", "breeding_date"])
def test_breeding_create_missing_required(missing: str) -> None:
    payload = dict(VALID_BREEDING)
    del payload[missing]
    with pytest.raises(ValidationError):
        BreedingCreateIn(**payload)


@pytest.mark.parametrize(
    ("pregnant", "kid_count"),
    [
        (True, 1),
        (True, 3),
        (True, None),
        (False, None),
    ],
)
def test_ultrasound_in_valid(pregnant: bool, kid_count: int | None) -> None:
    UltrasoundIn(pregnant=pregnant, kid_count=kid_count)


@pytest.mark.parametrize("kid_count", [0, 6, -1])
def test_ultrasound_in_invalid_kid_count(kid_count: int) -> None:
    with pytest.raises(ValidationError):
        UltrasoundIn(pregnant=True, kid_count=kid_count)


def test_ultrasound_in_rejects_kid_count_when_not_pregnant() -> None:
    with pytest.raises(ValidationError):
        UltrasoundIn(pregnant=False, kid_count=2)


def test_ultrasound_in_requires_pregnant_flag() -> None:
    with pytest.raises(ValidationError):
        UltrasoundIn()


# ---------------------------------------------------------------------------
# app.schemas — kidding
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tag", None),
        ("tag", "K-201"),
        ("tag", "T" * 50),
        ("sex", "M"),
        ("sex", "F"),
        ("status", "ALIVE"),
        ("status", "STILLBORN"),
        ("status", "DIED"),
        ("birth_weight", None),
        ("birth_weight", 0.0),
        ("birth_weight", 2.8),
    ],
)
def test_kid_in_valid(field: str, value: object) -> None:
    payload = {"sex": "M"} | {field: value}
    if field == "status" and value == "DIED":
        payload["mortality_reported_at"] = TODAY.isoformat()
    KidIn(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tag", "T" * 51),
        ("sex", "X"),
        ("status", "MISSING"),
        ("birth_weight", -0.1),
        ("birth_weight", NAN),
    ],
)
def test_kid_in_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        KidIn(**({"sex": "M"} | {field: value}))


def test_kid_in_requires_sex() -> None:
    with pytest.raises(ValidationError):
        KidIn()


VALID_KIDDING = {
    "breeding_record_id": 1,
    "date": TODAY.isoformat(),
    "kids": [{"sex": "M"}],
}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ease", "NORMAL"),
        ("ease", "ASSISTED"),
        ("ease", "DIFFICULT"),  # the three SPEC §KiddingRecord values
        ("ease", "NORMAL"),
        ("notes", None),
        ("notes", "n" * 4_000),
        ("date", TOMORROW),  # future dates are the router's guard, not the schema's
        ("kids", [{"sex": "M"}] * 10),
    ],
)
def test_kidding_create_valid(field: str, value: object) -> None:
    KiddingCreateIn(**(VALID_KIDDING | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ease", "MAGIC"),
        ("breeding_record_id", 0),
        ("breeding_record_id", -1),
        ("breeding_record_id", MAX_ID + 1),
        ("kids", []),  # at least one kid entry required
        ("kids", [{"sex": "M"}] * 11),  # capped at 10
    ],
)
def test_kidding_create_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        KiddingCreateIn(**(VALID_KIDDING | {field: value}))


@pytest.mark.parametrize("missing", ["breeding_record_id", "date", "kids"])
def test_kidding_create_missing_required(missing: str) -> None:
    payload = dict(VALID_KIDDING)
    del payload[missing]
    with pytest.raises(ValidationError):
        KiddingCreateIn(**payload)


# ---------------------------------------------------------------------------
# app.schemas — health
# ---------------------------------------------------------------------------
HEALTH_TYPES = [
    "VACCINE",
    "DEWORMING",
    "TREATMENT",
    "FOOTBATH",
    "VITAMIN",
    "EXAM",
    "FECAL_EXAM",
]
VALID_HEALTH = {"scope": "animal", "animal_id": 1, "type": "TREATMENT"}


@pytest.mark.parametrize("event_type", HEALTH_TYPES)
def test_health_event_accepts_every_type(event_type: str) -> None:
    assert HealthEventIn(**(VALID_HEALTH | {"type": event_type})).type == event_type


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "animal", "animal_id": 1, "type": "VACCINE"},
        {"scope": "bucket", "bucket": "FOUNDATION", "type": "DEWORMING"},
        {"scope": "batch", "purchase_batch_id": 1, "type": "FOOTBATH"},
        {"scope": "animal", "animal_id": 1, "type": "VITAMIN", "date": TODAY.isoformat()},
        {
            "scope": "animal",
            "animal_id": 1,
            "type": "VACCINE",
            "next_due_date": TOMORROW,
            "schedule_template_name": "Farm vaccination protocol",
            "next_due_authority": "Veterinarian prescription VET-001",
        },
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "cost": 0.0},
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "route": "R" * 20},
        # NOTE: a 60-char route was previously "valid" here, but the column is
        # String(20) — the schema cap is now 20, so that case moved to invalid.
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "dose": "D" * 60},
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "task_id": 5},
    ],
)
def test_health_event_valid(payload: dict) -> None:
    HealthEventIn(**payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "animal", "type": "VACCINE"},  # scope without its target
        {"scope": "bucket", "type": "VACCINE"},
        {"scope": "batch", "type": "VACCINE"},
        {"scope": "animal", "animal_id": 0, "type": "VACCINE"},
        {"scope": "animal", "animal_id": MAX_ID + 1, "type": "VACCINE"},
        {"scope": "batch", "purchase_batch_id": 0, "type": "VACCINE"},
        {"scope": "animal", "animal_id": 1, "type": "MAGIC"},
        {"scope": "animal", "animal_id": 1, "type": "vaccine"},  # case-sensitive
        {"scope": "animal", "animal_id": 1, "type": "VACCINE", "date": GUARANTEED_FUTURE},
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "cost": -0.01},
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "cost": NAN},
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "product_name": "P" * 121},
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "disease_target": "D" * 121},
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "dose": "D" * 61},
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "route": "R" * 21},
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "route": "R" * 61},
        {"scope": "animal", "animal_id": 1, "type": "TREATMENT", "vet_name": "V" * 121},
        {"scope": "animal", "animal_id": 1, "type": "VACCINE", "task_id": 0},
        {"scope": "animal", "animal_id": 1, "type": "VACCINE", "bucket": "PASTURE"},
    ],
)
def test_health_event_invalid(payload: dict) -> None:
    with pytest.raises(ValidationError):
        HealthEventIn(**payload)


def test_health_event_defaults_to_animal_scope() -> None:
    assert HealthEventIn(animal_id=1, type="VACCINE").scope == "animal"


# ---------------------------------------------------------------------------
# app.schemas — feeding
# ---------------------------------------------------------------------------
# recipe_code is required: a dispensing record without a recipe cannot be
# reconciled with the ration plan and bypasses finished-feed stock deduction.
VALID_DISPENSE = {
    "bucket": "FOUNDATION",
    "shift": "MORNING",
    "qty_kg": 1.5,
    "recipe_code": "LACTATING_60_40",
}


@pytest.mark.parametrize("shift", ["MORNING", "AFTERNOON", "NIGHT"])
def test_dispense_accepts_every_shift(shift: str) -> None:
    assert DispenseIn(**(VALID_DISPENSE | {"shift": shift})).shift == shift


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qty_kg", 0.001),
        ("qty_kg", 1000.0),
        ("recipe_code", "CREEP"),
        ("date", None),
        ("date", TODAY.isoformat()),
    ],
)
def test_dispense_valid(field: str, value: object) -> None:
    DispenseIn(**(VALID_DISPENSE | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("shift", "MIDDAY"),
        ("shift", "morning"),
        ("bucket", "PASTURE"),
        ("qty_kg", 0.0),
        ("qty_kg", -1.0),
        ("qty_kg", 0.0004),
        ("qty_kg", NAN),
        ("qty_kg", INF),
        ("date", GUARANTEED_FUTURE),
        ("recipe_code", None),
        ("recipe_code", ""),
    ],
)
def test_dispense_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        DispenseIn(**(VALID_DISPENSE | {field: value}))


@pytest.mark.parametrize("missing", ["bucket", "shift", "qty_kg", "recipe_code"])
def test_dispense_missing_required(missing: str) -> None:
    payload = dict(VALID_DISPENSE)
    del payload[missing]
    with pytest.raises(ValidationError):
        DispenseIn(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recipe_code", "CREEP"),
        ("batch_kg", 0.001),
        ("batch_kg", 500.0),
    ],
)
def test_mix_valid(field: str, value: object) -> None:
    MixIn(**({"recipe_code": "FATTENING_50_50", "batch_kg": 100.0} | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recipe_code", ""),
        ("batch_kg", 0.0),
        ("batch_kg", -5.0),
        ("batch_kg", 0.0004),
        ("batch_kg", NAN),
        ("batch_kg", INF),
    ],
)
def test_mix_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        MixIn(**({"recipe_code": "FATTENING_50_50", "batch_kg": 100.0} | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qty_kg", 0.001),
        ("price_per_kg", None),
        ("price_per_kg", 0.0),  # an explicit ₹0 restock is accepted
    ],
)
def test_stock_add_valid(field: str, value: object) -> None:
    StockAddIn(**({"qty_kg": 10.0} | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qty_kg", 0.0),
        ("qty_kg", -1.0),
        ("qty_kg", 0.0004),
        ("qty_kg", NAN),
        ("price_per_kg", -1.0),
        ("price_per_kg", 0.001),
        ("price_per_kg", NAN),
    ],
)
def test_stock_add_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        StockAddIn(**({"qty_kg": 10.0} | {field: value}))


def test_feed_quantities_round_half_up_to_gram_precision() -> None:
    assert StockAddIn(qty_kg=0.0005).qty_kg == 0.001
    assert StockAddIn(qty_kg=10.1235).qty_kg == 10.124


def test_money_inputs_round_half_up_but_cannot_silently_become_zero() -> None:
    assert TransactionIn(**(VALID_TXN | {"amount": 10.015})).amount == 10.02
    with pytest.raises(ValidationError):
        TransactionIn(**(VALID_TXN | {"amount": 0.001}))


def test_feed_setting_valid() -> None:
    assert FeedSettingIn(bucket="RESTING", daily_kg_per_head=1.2).daily_kg_per_head == 1.2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bucket", "PASTURE"),
        ("daily_kg_per_head", 0.0),
        ("daily_kg_per_head", -0.5),
        ("daily_kg_per_head", NAN),
    ],
)
def test_feed_setting_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        FeedSettingIn(**({"bucket": "RESTING", "daily_kg_per_head": 1.2} | {field: value}))


# ---------------------------------------------------------------------------
# app.schemas — finance
# ---------------------------------------------------------------------------
TXN_CATEGORIES = [
    "ANIMAL_SALE",
    "ANIMAL_PURCHASE",
    "FEED",
    "MEDICINE",
    "VET",
    "LABOUR",
    "EQUIPMENT",
    "MANURE",
    "OTHER",
]
VALID_TXN = {"date": TODAY.isoformat(), "type": "INCOME", "category": "OTHER", "amount": 100.0}


@pytest.mark.parametrize("txn_type", ["INCOME", "EXPENSE"])
def test_transaction_accepts_every_type(txn_type: str) -> None:
    assert TransactionIn(**(VALID_TXN | {"type": txn_type})).type == txn_type


@pytest.mark.parametrize("category", TXN_CATEGORIES)
def test_transaction_accepts_every_user_bookable_category(category: str) -> None:
    overrides = dict(VALID_TXN | {"category": category})
    if category in ("ANIMAL_SALE", "ANIMAL_PURCHASE"):
        # System-generated categories reject manual rows.
        with pytest.raises(ValidationError):
            TransactionIn(**overrides)
        return
    assert TransactionIn(**overrides).category == category


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount", 0.01),
        ("amount", 99999999.99),
        ("related_animal_id", None),
        ("related_animal_id", 5),
        ("notes", None),
        ("notes", "Sold to శేషు"),
    ],
)
def test_transaction_valid(field: str, value: object) -> None:
    TransactionIn(**(VALID_TXN | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "REFUND"),
        ("type", "income"),
        ("category", "DIVIDEND"),
        ("amount", 0.0),
        ("amount", -100.0),
        ("amount", NAN),
        ("amount", INF),
        ("date", GUARANTEED_FUTURE),
        ("related_animal_id", 0),
        ("related_animal_id", MAX_ID + 1),
    ],
)
def test_transaction_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TransactionIn(**(VALID_TXN | {field: value}))


@pytest.mark.parametrize("missing", ["date", "type", "category", "amount"])
def test_transaction_missing_required(missing: str) -> None:
    payload = dict(VALID_TXN)
    del payload[missing]
    with pytest.raises(ValidationError):
        TransactionIn(**payload)


# ---------------------------------------------------------------------------
# app.schemas — purchases
# ---------------------------------------------------------------------------
VALID_BATCH = {"date": TODAY.isoformat(), "count": 50}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("count", 1),
        ("count", 1000),  # services.MAX_BATCH_COUNT
        ("avg_age_months", None),
        ("avg_age_months", 0.0),
        ("avg_age_months", 240.0),  # services.MAX_AGE_MONTHS
        ("avg_age_months", 7.5),
        ("avg_weight_kg", None),
        ("avg_weight_kg", 0.0),
        ("total_price", None),
        ("total_price", 0.0),
        ("total_price", 1e9),
        ("supplier", None),
        ("supplier", "S" * 120),
        ("date", "2000-01-01"),  # lower bound of the _not_ancient guard
        ("notes", None),
        ("notes", "n" * 4_000),
        ("create_animals", False),
    ],
)
def test_purchase_batch_valid(field: str, value: object) -> None:
    PurchaseBatchIn(**(VALID_BATCH | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("count", 0),
        ("count", 1001),
        ("count", -5),
        ("avg_age_months", -0.1),
        ("avg_age_months", 240.1),
        ("avg_weight_kg", -0.01),
        ("avg_weight_kg", NAN),
        ("total_price", -0.01),
        ("total_price", NAN),
        ("supplier", "S" * 121),
        ("date", GUARANTEED_FUTURE),
        ("date", "1999-12-31"),  # _not_ancient guard
        ("date", "1900-06-15"),
    ],
)
def test_purchase_batch_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        PurchaseBatchIn(**(VALID_BATCH | {field: value}))


@pytest.mark.parametrize("missing", ["date", "count"])
def test_purchase_batch_missing_required(missing: str) -> None:
    payload = dict(VALID_BATCH)
    del payload[missing]
    with pytest.raises(ValidationError):
        PurchaseBatchIn(**payload)


def test_purchase_batch_defaults_create_animals_true() -> None:
    assert PurchaseBatchIn(**VALID_BATCH).create_animals is True


# ---------------------------------------------------------------------------
# app.schemas — tasks
# ---------------------------------------------------------------------------
SYSTEM_TASK_CATEGORIES = [
    "VACCINE",
    "DEWORMING",
    "ULTRASOUND",
    "KIDDING_DUE",
    "WEANING",
    "BUCKET_MOVE",
    "QUARANTINE",
]
MANUAL_TASK_CATEGORIES = ["FEED", "CLEANING", "OTHER"]
VALID_TASK = {"title": "Clean shed 3", "due_date": TODAY.isoformat()}


@pytest.mark.parametrize("category", MANUAL_TASK_CATEGORIES)
def test_task_create_accepts_every_safe_manual_category(category: str) -> None:
    assert TaskCreateIn(**(VALID_TASK | {"category": category})).category == category


@pytest.mark.parametrize("category", SYSTEM_TASK_CATEGORIES)
def test_task_create_rejects_system_workflow_categories(category: str) -> None:
    with pytest.raises(ValidationError):
        TaskCreateIn(**(VALID_TASK | {"category": category}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "T"),
        ("title", "T" * 200),
        ("title", "షెడ్ శుభ్రం 🧹"),
        ("due_date", TOMORROW),  # duties may be due in the future
        ("recur_days", None),
        ("recur_days", 1),
        ("recur_days", 3650),  # services.MAX_RECUR_DAYS
        ("assigned_role_id", None),
        ("assigned_role_id", 3),
        ("assigned_user_id", None),
        ("assigned_user_id", 7),
    ],
)
def test_task_create_valid(field: str, value: object) -> None:
    TaskCreateIn(**(VALID_TASK | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", ""),
        ("title", "T" * 201),
        ("category", "MAGIC"),
        ("category", "cleaning"),
        ("recur_days", 0),
        ("recur_days", -1),
        ("recur_days", 3651),
        ("assigned_role_id", 0),
        ("assigned_user_id", -2),
    ],
)
def test_task_create_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TaskCreateIn(**(VALID_TASK | {field: value}))


@pytest.mark.parametrize("missing", ["title", "due_date"])
def test_task_create_missing_required(missing: str) -> None:
    payload = dict(VALID_TASK)
    del payload[missing]
    with pytest.raises(ValidationError):
        TaskCreateIn(**payload)


def test_task_create_defaults_category_other() -> None:
    assert TaskCreateIn(**VALID_TASK).category == "OTHER"


@pytest.mark.parametrize("note", ["Not swept behind the feeders", "N" * 255])
def test_task_reject_valid(note: str) -> None:
    TaskRejectIn(note=note)


def test_task_reject_note_is_required() -> None:
    """RT-FG-5: an omitted or blank rejection note is a validation error."""
    with pytest.raises(ValidationError):
        TaskRejectIn(note=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        TaskRejectIn()
    with pytest.raises(ValidationError):
        TaskRejectIn(note="   ")


def test_task_reject_note_too_long() -> None:
    with pytest.raises(ValidationError):
        TaskRejectIn(note="N" * 256)


@pytest.mark.parametrize("reason", ["Area under repair", "R" * 255])
def test_task_skip_valid(reason: str) -> None:
    task = TaskSkipIn(reason=reason)
    assert task.reason == reason


def test_task_skip_reason_is_required() -> None:
    """RT-FG-4: a skip without a non-blank reason is a validation error."""
    with pytest.raises(ValidationError):
        TaskSkipIn(reason=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        TaskSkipIn()
    with pytest.raises(ValidationError):
        TaskSkipIn(reason="   ")


def test_task_skip_reason_too_long() -> None:
    with pytest.raises(ValidationError):
        TaskSkipIn(reason="R" * 256)


# ---------------------------------------------------------------------------
# app.schemas — team
# ---------------------------------------------------------------------------
def test_worker_create_valid() -> None:
    worker = WorkerCreateIn(email="w@farm.in", role_id=1)
    assert worker.password is None  # required only for brand-new accounts (router checks)


@pytest.mark.parametrize(
    "payload",
    [
        {"role_id": 1},  # missing email
        {"email": "w@farm.in"},  # missing role_id
        {"email": "w@farm.in", "role_id": 0},
        {"email": "w@farm.in", "role_id": MAX_ID + 1},
        {"email": "w@farm.in", "role_id": 1, "name": "N" * 121},
    ],
)
def test_worker_create_invalid(payload: dict) -> None:
    with pytest.raises(ValidationError):
        WorkerCreateIn(**payload)


def test_password_reset_valid() -> None:
    assert PasswordResetIn(password="x").password == "x"


@pytest.mark.parametrize("payload", [{"password": ""}, {}])
def test_password_reset_invalid(payload: dict) -> None:
    with pytest.raises(ValidationError):
        PasswordResetIn(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "R"),
        ("name", "R" * 80),
        ("description", None),
        ("description", "D" * 255),
        ("permissions", []),
        ("permissions", ["animals.view", "tasks.verify"]),
    ],
)
def test_role_in_valid(field: str, value: object) -> None:
    RoleIn(**({"name": "Night Watchman"} | {field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "R" * 81),
        ("description", "D" * 256),
    ],
)
def test_role_in_invalid(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        RoleIn(**({"name": "Night Watchman"} | {field: value}))


def test_role_in_defaults_permissions_empty() -> None:
    assert RoleIn(name="R").permissions == []


# ---------------------------------------------------------------------------
# DB-backed boundary checks (client fixture): values AT the database column
# limit must succeed end-to-end — the schema admits these and Postgres stores
# them. (Values the schema admits BEYOND the column limit are in
# tests/test_unit_bugs.py.)
# ---------------------------------------------------------------------------
async def _make_animal(client: httpx.AsyncClient, headers: dict) -> int:
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "BND-1",
            "sex": "F",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "historical_import_reason": "Existing-herd DB-boundary fixture",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_register_name_at_db_column_limit(client: httpx.AsyncClient) -> None:
    headers = await register(client, name="N" * 120)  # users.name is String(120)
    assert headers["Authorization"].startswith("Bearer ")


async def test_farm_location_at_db_column_limit(client: httpx.AsyncClient) -> None:
    headers = await register(client)
    resp = await client.post(
        "/api/auth/farms",
        json={"name": "Beta Farm", "location": "L" * 120},  # farms.location String(120)
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


async def test_weight_notes_at_db_column_limit(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal_id = await _make_animal(client, headers)
    resp = await client.post(
        f"/api/animals/{animal_id}/weight",
        json={"weight_kg": 25.0, "notes": "n" * 255},  # weight_records.notes String(255)
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


async def test_move_reason_at_db_column_limit(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal_id = await _make_animal(client, headers)
    resp = await client.post(
        f"/api/animals/{animal_id}/move",
        json={
            "to_bucket": "RESTING",
            "reason": "r" * 255,
            "history_override": True,
        },  # bucket_moves.reason String(255), including the audited override prefix
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def test_status_notes_at_db_column_limit(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal_id = await _make_animal(client, headers)
    resp = await client.post(
        f"/api/animals/{animal_id}/status",
        json={"new_status": "DEAD", "notes": "n" * 255},  # animals.status_notes String(255)
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def test_health_route_at_db_column_limit(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    animal_id = await _make_animal(client, headers)
    resp = await client.post(
        "/api/health/events",
        json={
            "scope": "animal",
            "animal_id": animal_id,
            "type": "TREATMENT",
            "route": "R" * 20,  # health_events.route String(20)
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


async def test_finance_notes_at_db_column_limit(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/finance/new",
        json={
            "date": TODAY.isoformat(),
            "type": "EXPENSE",
            "category": "OTHER",
            "amount": 10.0,
            "notes": "n" * 255,  # transactions.notes String(255)
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


async def test_animal_notes_free_text_is_bounded(client: httpx.AsyncClient) -> None:
    headers = await owner_with_farm(client)
    resp = await client.post(
        "/api/animals",
        json={
            "tag_number": "BND-2",
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "notes": "జ" * 4_000,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert len(resp.json()["notes"]) == 4_000
    rejected = await client.post(
        "/api/animals",
        json={
            "tag_number": "BND-3",
            "sex": "M",
            "source": "PURCHASED",
            "current_bucket": "FOUNDATION",
            "notes": "జ" * 4_001,
        },
        headers=headers,
    )
    assert rejected.status_code == 422


# ---------------------------------------------------------------------------
# README claims that are derivable from the code. Prose drifts silently; these
# keep the few README statements that have a machine-checkable answer honest.
# ---------------------------------------------------------------------------
README = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_table_count_matches_the_metadata() -> None:
    """README's backend layout states how many tables `models/` declares. It
    said 24 while the metadata declared 26 — the two undocumented ones being
    idempotency_records and movement_restriction_actions, exactly the tables a
    retention/inventory reader most needs."""
    match = re.search(r"models/\s+(\d+) tables", README.read_text())
    assert match is not None, "README no longer states a table count for models/"
    assert int(match.group(1)) == len(Base.metadata.tables)


def test_readme_database_urls_use_the_async_driver_scheme() -> None:
    """Every `GOATFARM_*DATABASE_URL` README assigns is consumed by SQLAlchemy's
    async engine (app/db.py, alembic/env.py), which needs `postgresql+asyncpg`.
    The restore runbook used to pass `${GOATFARM_RESTORE_DATABASE_URL}` through
    verbatim — a libpq URL that resolves to psycopg2 and dies mid-recovery with
    `ModuleNotFoundError: No module named 'psycopg2'`."""
    assignments = re.findall(r"GOATFARM_(?:MIGRATION_)?DATABASE_URL=(\S+)", README.read_text())
    assert assignments, "README no longer shows a database URL to check"
    offenders = [value for value in assignments if "postgresql+asyncpg" not in value]
    assert not offenders, f"non-async driver scheme in README runbook: {offenders}"
