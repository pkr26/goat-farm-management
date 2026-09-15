"""Schema/model parity guards.

The Pydantic ``Literal`` aliases in ``app/schemas/`` re-declare the string
enums in ``app/models.py`` (Pydantic v2 can't derive a JSON-schema enum from
a str-Enum Literal without losing the wire names), and the input sanity caps
are shared constants. A new bucket/category/status added to models but not
mirrored here fails these tests instead of silently rejecting API input.
"""

from typing import get_args

from app import models
from app.models.constants import (
    BIRTHING_KIT_LEAD_DAYS,
    BUCK_ROTATION_AGE_MONTHS,
    CREEP_START_DAYS,
    KIDDING_WATCH_START_DAYS,
    MIN_REST_FLUSH_DAYS,
    POSTPARTUM_CARE_LEAD_DAYS,
)
from app.models.species import GOAT_PROFILE
from app.permissions import ROLE_PRESET_CODES, ROLE_PRESETS
from app.schemas import animals, breeding, finance, health, kidding, purchases, tasks
from app.schemas.feeding import IngredientCategoryStr, ShiftStr


def _literal_values(alias: object) -> set[str]:
    return set(get_args(alias))  # type: ignore[arg-type]


def test_bucket_str_matches_bucket_enum() -> None:
    assert _literal_values(animals.BucketStr) == {b.value for b in models.Bucket}


def test_task_category_str_matches_enum() -> None:
    assert _literal_values(tasks.TaskCategoryStr) == {c.value for c in models.TaskCategory}
    assert _literal_values(tasks.TaskStatusStr) == {s.value for s in models.TaskStatus}


def test_health_event_type_str_matches_enum() -> None:
    # Wave-0 vocabulary: EXAM/FECAL_EXAM exist in the enum (and the DB CHECK)
    # before the wire schema exposes them; the literal widens when the health
    # domain agent opens the exam endpoints. The subset keeps the load-bearing
    # direction — every wire-accepted value is DB-valid.
    assert _literal_values(health.HealthEventTypeStr) <= {t.value for t in models.HealthEventType}


def test_transaction_category_str_matches_enum() -> None:
    assert _literal_values(finance.TransactionCategoryStr) == {
        c.value for c in models.TransactionCategory
    }
    assert _literal_values(finance.TransactionTypeStr) == {t.value for t in models.TransactionType}


def test_kid_status_and_ease_strs_match_enums() -> None:
    assert _literal_values(kidding.KidStatusStr) == {s.value for s in models.KidStatus}
    # CAESAREAN is enum/DB vocabulary ahead of the wire (see the health-event
    # note above); the kidding endpoint keeps rejecting it until its domain
    # agent widens KiddingEaseStr (test_kidding_ease_rejects_caesarean_per_spec
    # pins the current refusal).
    assert _literal_values(kidding.KiddingEaseStr) <= {e.value for e in models.KiddingEase}


def test_animal_vocabularies_match_enums() -> None:
    assert _literal_values(animals.AnimalStatusStr) == {s.value for s in models.AnimalStatus}
    assert _literal_values(animals.AnimalSourceStr) == {s.value for s in models.AnimalSource}
    assert _literal_values(animals.BirthTypeStr) == {b.value for b in models.BirthType}
    assert _literal_values(animals.Sex) == {s.value for s in models.Sex}


def test_breeding_vocabularies_match_enums() -> None:
    assert _literal_values(breeding.BreedingOutcomeStr) == {o.value for o in models.BreedingOutcome}
    assert _literal_values(breeding.BreedingMethodStr) == {m.value for m in models.BreedingMethod}
    # The input cause list deliberately omits the server-owned
    # ANIMAL_STATUS_CHANGE; the Out-side vocabulary includes it.
    assert _literal_values(breeding.PregnancyLossCause) == set(models.PREGNANCY_LOSS_CAUSES) - {
        "ANIMAL_STATUS_CHANGE"
    }
    assert _literal_values(breeding.PregnancyLossCauseWithSystem) == set(
        models.PREGNANCY_LOSS_CAUSES
    )


def test_feeding_vocabularies_match_enums() -> None:
    assert _literal_values(ShiftStr) == {s.value for s in models.FeedingShift}
    assert _literal_values(IngredientCategoryStr) == {c.value for c in models.IngredientCategory}


def test_sanity_caps_are_shared_from_models() -> None:
    assert purchases.MAX_BATCH_COUNT is models.MAX_BATCH_COUNT
    assert purchases.MAX_AGE_MONTHS is models.MAX_AGE_MONTHS
    assert tasks.MAX_RECUR_DAYS is models.MAX_RECUR_DAYS
    # services must import the same objects, not re-define them.
    from app import services

    assert services.MAX_BATCH_COUNT is models.MAX_BATCH_COUNT
    assert services.MAX_AGE_MONTHS is models.MAX_AGE_MONTHS
    assert services.MAX_RECUR_DAYS is models.MAX_RECUR_DAYS


def test_wire_caps_are_shared_from_models() -> None:
    """Every cap consumed by schemas traces to the single models constant."""
    import app.schemas.animals as animals_schemas

    assert animals_schemas.MAX_ANIMAL_TAG_LENGTH is models.MAX_ANIMAL_TAG_LENGTH
    assert models.MAX_WITHDRAWAL_DAYS == 730
    assert models.MAX_TASK_TITLE_LENGTH == 200
    from app.schemas.common import MAX_FREE_TEXT_LENGTH

    assert MAX_FREE_TEXT_LENGTH == 4_000


def test_goat_profile_aliases_cannot_drift() -> None:
    """The legacy constants.py names are aliases of GOAT_PROFILE fields."""
    p = GOAT_PROFILE
    assert models.GESTATION_DAYS == p.gestation_days
    assert models.KIDDING_WINDOW_DAYS == p.parturition_window_days
    assert models.MIN_GESTATION_DAYS == p.min_gestation_days
    assert models.MAX_GESTATION_DAYS == p.max_gestation_days
    assert models.ULTRASOUND_AFTER_BREEDING_DAYS == p.pregnancy_check_after_service_days
    assert models.MIN_BREEDING_AGE_MONTHS == p.min_breeding_age_months
    assert models.MIN_BREEDING_WEIGHT_KG == p.min_breeding_weight_kg
    assert models.MIN_BUCK_BREEDING_AGE_MONTHS == p.min_sire_breeding_age_months
    assert models.MIN_BUCK_BREEDING_WEIGHT_KG == p.min_sire_breeding_weight_kg
    assert models.WEANING_DAYS == p.weaning_days
    assert models.POSTPARTUM_RECOVERY_DAYS == p.postpartum_recovery_days
    assert models.MAX_FAILED_CYCLES_BEFORE_CULL == p.failed_services_before_cull
    # Husbandry-standards scheduling knobs (constants.py aliases; not yet
    # re-exported through app.models).
    assert KIDDING_WATCH_START_DAYS == p.kidding_watch_start_days
    assert BIRTHING_KIT_LEAD_DAYS == p.birthing_kit_lead_days
    assert POSTPARTUM_CARE_LEAD_DAYS == p.postpartum_care_lead_days
    assert CREEP_START_DAYS == p.creep_start_days
    assert MIN_REST_FLUSH_DAYS == p.min_rest_flush_days
    assert BUCK_ROTATION_AGE_MONTHS == p.buck_rotation_age_months


def test_species_policy_profiles_are_coherent() -> None:
    """The policy knobs the services enforce."""
    goat = GOAT_PROFILE
    assert goat.voluntary_waiting_days == 14
    assert goat.failed_services_before_cull == 2
    assert goat.max_litter_size == 4
    # SPEC: goat "day 100" EARLY→LATE exit, kidding pen ~2 weeks pre-due.
    assert goat.pregnancy_late_day == 100
    assert goat.prepartum_move_lead_days == 15
    # Husbandry-standards scheduling knobs (wave-0 defaults; the parity test
    # above pins the constants.py aliases to these same attributes).
    assert goat.kidding_watch_start_days == 5
    assert goat.birthing_kit_lead_days == 7
    assert goat.postpartum_care_lead_days == 1
    assert goat.creep_start_days == 14
    assert goat.min_rest_flush_days == 10
    assert goat.buck_rotation_age_months == 36


def test_preset_role_code_catalog_matches_seed_definitions() -> None:
    assert ROLE_PRESET_CODES == {preset["code"] for preset in ROLE_PRESETS}
