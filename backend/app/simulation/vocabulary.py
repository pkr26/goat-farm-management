"""Species nouns for simulation text.

Every piece of user-facing text the simulation produces (narrative report,
monthly event log, planner notes) renders its nouns through this set, so the
product vocabulary has one source. The product is goat-only.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeciesNouns:
    """Count-neutral nouns for the product's species vocabulary."""

    female: str  # adult breeding female: "doe"
    female_counted: str  # "… 60 does"
    female_plural: str  # "breeding does"
    male: str  # adult male: "buck" / "bull"
    male_counted: str  # "… 2 bucks" / "… 2 bulls"
    male_plural: str  # "bulls"
    young: str  # "kid" / "calf"
    young_counted: str  # "kid(s)" / "calf(s)"
    young_plural: str  # "kids" / "calves"
    parturition: str  # "kidding" / "calving"
    species: str  # "goat"

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

__all__ = ["GOAT_NOUNS", "SpeciesNouns"]
