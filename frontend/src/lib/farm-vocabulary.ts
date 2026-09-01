/**
 * Species-aware UI vocabulary.
 *
 * The app manages two farm types — Osmanabadi goats (meat) and Murrah buffalo
 * (dairy) — over the same operational modules. The backend keeps species
 * biology (gestation, weaning, breeding gates); this module keeps the words a
 * screen shows. Every farm-type-dependent label resolves through here so no
 * page hardcodes goat nouns for a dairy operator.
 */

export type FarmType = "GOAT" | "BUFFALO_DAIRY";

/** Species breeding-entry rules, mirroring backend/app/models/species.py
 * (GOAT_PROFILE / BUFFALO_DAIRY_PROFILE). The frontend form gates must show
 * the same numbers the backend enforces, with the right nouns. */
export interface BreedingEntryRules {
  female: { minMonths: number; minWeightKg: number };
  male: { minMonths: number; minWeightKg: number };
}

/** Species facts the UI states in copy (gestation bands, weaning offsets).
 * Mirrors backend/app/models/species.py — drift here shows operators biology
 * the server does not enforce. */
export interface SpeciesFacts {
  /** Recording sanity band after the fact (min/max_gestation_days). */
  gestationWindowDays: { min: number; max: number };
  /** Planned pregnancy-check task offset (pregnancy_check_after_service_days). */
  pregnancyCheckDays: number;
  /** Weaning task offset after parturition (weaning_days). */
  weaningDays: number;
  /** True when newborn young stay with the dam (RECOVERY) instead of being
   * separated into sexed pens (young_stay_with_dam). */
  youngStayWithDam: boolean;
}

export interface FarmVocabulary {
  /** "Goat farm" / "Buffalo dairy" — used on cards and pickers. */
  typeLabel: string;
  /** Species noun: "goat" / "buffalo" — milk, meat and other produce copy. */
  species: string;
  /** Species plural: "goats" / "buffalo" — group and purchase copy. */
  speciesPlural: string;
  /** Adult female: "doe" / "milking buffalo". */
  femaleAdult: string;
  /** Adult female plural: "does" / "milking buffalo". */
  femaleAdultPlural: string;
  /** Adult male: "buck" / "bull". */
  maleAdult: string;
  /** Young animal singular/plural: "kid(s)" / "calf(calves)". */
  young: string;
  youngPlural: string;
  /** Parturition noun: "kidding" / "calving". */
  parturition: string;
  parturitionCap: string;
  /** Parturition past participle: "kidded" / "calved". */
  parturitionPast: string;
  /** e.g. "Kidding due" / "Calving due" duty label. */
  dueLabel: string;
  /** Breeding-gate copy shown on eligibility hints. */
  breedingGateCopy: string;
  /** True when the farm's operations include a milking parlour. */
  dairy: boolean;
  /** Breed preselected for new animals (backend species default_breed). */
  defaultBreed: string;
  /** Prefix of auto-generated tag numbers. The backend issues "G-XXXXX"
   * for every species (services/animals.generate_unique_tag); purchase tags
   * use a separate "B<batch>" scheme, so this stays "G" for both. */
  tagPrefix: string;
  /** Minimum age/weight to import an animal straight into BREEDING. */
  breedingEntry: BreedingEntryRules;
  /** Gestation/weaning facts stated in kidding and breeding copy. */
  facts: SpeciesFacts;
}

const GOAT_VOCABULARY: FarmVocabulary = {
  typeLabel: "Goat farm",
  species: "goat",
  speciesPlural: "goats",
  femaleAdult: "doe",
  femaleAdultPlural: "does",
  maleAdult: "buck",
  young: "kid",
  youngPlural: "kids",
  parturition: "kidding",
  parturitionCap: "Kidding",
  parturitionPast: "kidded",
  dueLabel: "Kidding due",
  breedingGateCopy: "A doe must be at least 10 months old and 22 kg to breed; bucks 12 months and 25 kg.",
  dairy: false,
  defaultBreed: "Osmanabadi",
  tagPrefix: "G",
  breedingEntry: {
    female: { minMonths: 10, minWeightKg: 22 },
    male: { minMonths: 12, minWeightKg: 25 },
  },
  facts: {
    gestationWindowDays: { min: 100, max: 200 },
    pregnancyCheckDays: 32,
    weaningDays: 60,
    youngStayWithDam: true,
  },
};

const BUFFALO_VOCABULARY: FarmVocabulary = {
  typeLabel: "Buffalo dairy",
  species: "buffalo",
  speciesPlural: "buffalo",
  femaleAdult: "milking buffalo",
  femaleAdultPlural: "milking buffalo",
  maleAdult: "bull",
  young: "calf",
  youngPlural: "calves",
  parturition: "calving",
  parturitionCap: "Calving",
  parturitionPast: "calved",
  dueLabel: "Calving due",
  breedingGateCopy:
    "Heifers are bred at 22–24 months and ≥340 kg (AI at 60 days post-calving; max 3 services before cull review).",
  dairy: true,
  defaultBreed: "Murrah",
  tagPrefix: "G",
  breedingEntry: {
    female: { minMonths: 22, minWeightKg: 340 },
    male: { minMonths: 24, minWeightKg: 350 },
  },
  facts: {
    gestationWindowDays: { min: 270, max: 350 },
    pregnancyCheckDays: 60,
    weaningDays: 90,
    youngStayWithDam: false,
  },
};

const VOCABULARIES: Record<string, FarmVocabulary> = {
  GOAT: GOAT_VOCABULARY,
  BUFFALO_DAIRY: BUFFALO_VOCABULARY,
};

export function farmVocabulary(farmType: string | null | undefined): FarmVocabulary {
  return VOCABULARIES[farmType ?? "GOAT"] ?? GOAT_VOCABULARY;
}

export function farmTypeLabel(farmType: string | null | undefined): string {
  return farmVocabulary(farmType).typeLabel;
}
