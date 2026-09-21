/**
 * Goat-farm UI vocabulary.
 *
 * The app manages Osmanabadi goat (meat) farms. The backend keeps species
 * biology (gestation, weaning, breeding gates); this module keeps the words a
 * screen shows so no page hardcodes goat nouns inconsistently.
 */

/** Species breeding-entry rules, mirroring backend/app/models/species.py
 * (GOAT_PROFILE). The frontend form gates must show the same numbers the
 * backend enforces, with the right nouns. */
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
  /** True when newborn kids stay with the dam (RECOVERY) instead of being
   * separated into sexed pens (young_stay_with_dam). */
  youngStayWithDam: boolean;
  /** Biological cap on litter size (max_litter_size): goat ≤4. */
  maxLitterSize: number;
  /** First-service age floor (min_breeding_age_months); pinned to the
   * backend species profile by backend-constants-parity.test.ts (P2-17). */
  minBreedingAgeMonths: number;
  /** Credible adult body-weight ceiling (max_adult_weight_kg): the backend
   * rejects recorded weights and purchase averages above it (goat 150 kg). */
  maxWeightKg: number;
  /** Credible newborn weight band (birth_weight_kg_range): the backend
   * rejects recorded birth weights outside it (goat 0.5–8 kg). */
  birthWeightKg: { min: number; max: number };
}

export interface FarmVocabulary {
  /** "Goat farm" — used on cards and pickers. */
  typeLabel: string;
  /** Species noun: "goat". */
  species: string;
  /** Species plural: "goats". */
  speciesPlural: string;
  /** Adult female: "doe". */
  femaleAdult: string;
  /** Adult female plural: "does". */
  femaleAdultPlural: string;
  /** Adult male: "buck". */
  maleAdult: string;
  /** Young animal singular/plural: "kid(s)". */
  young: string;
  youngPlural: string;
  /** Parturition noun: "kidding". */
  parturition: string;
  parturitionCap: string;
  /** Parturition past participle: "kidded". */
  parturitionPast: string;
  /** "Kidding due" duty label. */
  dueLabel: string;
  /** Breeding-gate copy shown on eligibility hints. */
  breedingGateCopy: string;
  /** Breed preselected for new animals (backend species default_breed). */
  defaultBreed: string;
  /** Prefix of auto-generated tag numbers. The backend issues "G-XXXXX"
   * (services/animals.generate_unique_tag); purchase tags use a separate
   * "B<batch>" scheme. */
  tagPrefix: string;
  /** Minimum age/weight to import an animal straight into BREEDING. */
  breedingEntry: BreedingEntryRules;
  /** Gestation/weaning facts stated in kidding and breeding copy. */
  facts: SpeciesFacts;
}

/** The single goat vocabulary every screen resolves through. */
export const farmVocabulary: FarmVocabulary = {
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
  // P2-17: the doe gate mirrors the species profile exactly — the backend
  // enforces 12 months (GOAT_PROFILE.min_breeding_age_months: field puberty
  // ~11.5 months, first-kidding norms 19-20 months). The old "10 months"
  // copy/gate passed a 10-11-month doe client-side and 422'd her on submit;
  // pinned by backend-constants-parity.test.ts.
  breedingGateCopy: "A doe must be at least 12 months old and 22 kg to breed; bucks 12 months and 25 kg.",
  defaultBreed: "Osmanabadi",
  tagPrefix: "G",
  breedingEntry: {
    female: { minMonths: 12, minWeightKg: 22 },
    male: { minMonths: 12, minWeightKg: 25 },
  },
  facts: {
    gestationWindowDays: { min: 100, max: 200 },
    pregnancyCheckDays: 32,
    weaningDays: 60,
    youngStayWithDam: true,
    maxLitterSize: 4,
    maxWeightKg: 150,
    birthWeightKg: { min: 0.5, max: 8 },
    minBreedingAgeMonths: 12,
  },
};

/** Label shown on farm cards and pickers. */
export const farmTypeLabel: string = farmVocabulary.typeLabel;
