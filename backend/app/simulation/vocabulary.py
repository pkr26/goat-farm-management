"""Species nouns for simulation text.

The engine is species-agnostic: ``does``/``bucks``/``kids`` in the code are
biological roles, not words a dairy operator should ever read. Every piece of
user-facing text the simulation produces (narrative report, monthly event log,
planner notes) renders its nouns through one of these sets, so a Murrah dairy
reads "milking buffalo", "bull" and "calf" while a goat farm keeps "doe",
"buck" and "kid".

The farm's ``farm_type`` (models/species.py) selects the set; unknown types
fall back to the goat nouns, matching the frontend's farm-vocabulary module.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeciesNouns:
    """Count-neutral nouns for one species (goat or buffalo dairy)."""

    female: str  # adult breeding female: "doe" / "milking buffalo"
    female_counted: str  # "… 60 does" / "… 60 milking buffalo"
    female_plural: str  # "breeding does" / "breeding milking buffalo"
    male: str  # adult male: "buck" / "bull"
    male_counted: str  # "… 2 bucks" / "… 2 bulls"
    male_plural: str  # "bulls"
    young: str  # "kid" / "calf"
    young_counted: str  # "kid(s)" / "calf(s)"
    young_plural: str  # "kids" / "calves"
    parturition: str  # "kidding" / "calving"
    species: str  # "goat" / "buffalo"

    def event_label(self, animal_class: str) -> str:
        """Human label for one HerdEventAssumptions.animal_class value."""
        young = self.young_counted
        labels = {
            "doe": self.female_counted,
            "buck": self.male_counted,
            "female_kid": f"female {young}",
            "male_kid": f"male {young}",
            "female_weaner": "female weaner(s)",
            "male_weaner": "male weaner(s)",
            "female_grower": "female grower(s)",
            "male_grower": "male grower(s)",
        }
        return labels[animal_class]


GOAT_NOUNS = SpeciesNouns(
    female="doe",
    female_counted="doe(s)",
    female_plural="does",
    male="buck",
    male_counted="buck(s)",
    male_plural="bucks",
    young="kid",
    young_counted="kid(s)",
    young_plural="kids",
    parturition="kidding",
    species="goat",
)

BUFFALO_NOUNS = SpeciesNouns(
    female="milking buffalo",
    female_counted="milking buffalo",
    female_plural="milking buffalo",
    male="bull",
    male_counted="bull(s)",
    male_plural="bulls",
    young="calf",
    young_counted="calf(s)",
    young_plural="calves",
    parturition="calving",
    species="buffalo",
)

_NOUNS_BY_FARM_TYPE = {
    "BUFFALO_DAIRY": BUFFALO_NOUNS,
}


def nouns_for_farm_type(farm_type: str | None) -> SpeciesNouns:
    """Nouns for a farm type; anything unrecognised reads as the goat set
    (the same default-everywhere rule the frontend vocabulary applies)."""
    return _NOUNS_BY_FARM_TYPE.get(farm_type or "GOAT", GOAT_NOUNS)


__all__ = ["BUFFALO_NOUNS", "GOAT_NOUNS", "SpeciesNouns", "nouns_for_farm_type"]
