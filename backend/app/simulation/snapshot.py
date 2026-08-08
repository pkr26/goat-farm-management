"""Group a farm's live herd into simulation starting cohorts (pure Python).

The API layer queries the farm's ACTIVE animals and feeds ``(sex,
age_months)`` pairs here; the bucketing rules (the same class ages the
engine uses) live in the simulation package, not the router.
"""

from collections.abc import Iterable

COHORT_KEYS = (
    "does",
    "bucks",
    "f_kids",
    "f_weaners",
    "f_growers",
    "m_kids",
    "m_weaners",
    "m_growers",
)


def herd_cohorts(
    animals: Iterable[tuple[str, int | None]], doe_adult_age: int, buck_adult_age: int = 12
) -> dict[str, int]:
    """Bucket ``(sex, age_months)`` pairs into the engine's starting cohorts.

    Kid 0-2 m, weaner 3-5 m, grower from 6 m up to breeding age, adult at
    breeding age (does at the breed's age-at-first-breeding, bucks at
    ``buck_adult_age``); an unknown age counts as adult. ``sex`` is the
    domain's ``"F"``/``"M"`` code.
    """
    counts = dict.fromkeys(COHORT_KEYS, 0)
    for sex, age in animals:
        adult_age = doe_adult_age if sex == "F" else buck_adult_age
        if age is None or age >= adult_age:
            counts["does" if sex == "F" else "bucks"] += 1
        elif age >= 6:
            counts["f_growers" if sex == "F" else "m_growers"] += 1
        elif age >= 3:
            counts["f_weaners" if sex == "F" else "m_weaners"] += 1
        else:
            counts["f_kids" if sex == "F" else "m_kids"] += 1
    return counts
