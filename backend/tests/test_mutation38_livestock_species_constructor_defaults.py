"""The original profile constructor keeps its additive husbandry defaults."""

import pytest

from app.models.species import SpeciesProfile


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """The native immutable profile constructor has no database dependency."""


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Constructing a profile never writes storage."""


def test_original_profile_constructor_retains_the_documented_additive_husbandry_defaults() -> None:
    # Supply the original required profile fields while deliberately omitting
    # the six later additions. The class documents this as a supported additive
    # constructor contract, independently of GOAT_PROFILE's explicit overrides.
    profile = SpeciesProfile(
        default_breed="Osmanabadi",
        parturition="kidding",
        young="kid",
        young_plural="kids",
        gestation_days=150,
        parturition_window_days=(145, 155),
        min_gestation_days=100,
        max_gestation_days=200,
        pregnancy_check_after_service_days=32,
        min_breeding_age_months=12,
        min_breeding_weight_kg=22.0,
        min_sire_breeding_age_months=12,
        min_sire_breeding_weight_kg=25.0,
        weaning_days=60,
        postpartum_recovery_days=14,
        pregnancy_late_day=100,
        prepartum_move_lead_days=15,
        young_stay_with_dam=True,
        voluntary_waiting_days=14,
        failed_services_before_cull=2,
        max_litter_size=4,
        birth_weight_kg_range=(0.5, 8.0),
        max_adult_weight_kg=150.0,
    )
    assert (
        profile.kidding_watch_start_days,
        profile.birthing_kit_lead_days,
        profile.postpartum_care_lead_days,
        profile.creep_start_days,
        profile.min_rest_flush_days,
        profile.buck_rotation_age_months,
    ) == (5, 7, 1, 14, 10, 36)
