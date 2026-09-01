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

export interface FarmVocabulary {
  /** "Goat farm" / "Buffalo dairy" — used on cards and pickers. */
  typeLabel: string;
  /** Species noun: "goat" / "buffalo" — milk, meat and other produce copy. */
  species: string;
  /** Species plural: "goats" / "buffalo" — group and purchase copy. */
  speciesPlural: string;
  /** Adult female: "doe" / "milking buffalo". */
  femaleAdult: string;
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
}

const GOAT_VOCABULARY: FarmVocabulary = {
  typeLabel: "Goat farm",
  species: "goat",
  speciesPlural: "goats",
  femaleAdult: "doe",
  maleAdult: "buck",
  young: "kid",
  youngPlural: "kids",
  parturition: "kidding",
  parturitionCap: "Kidding",
  parturitionPast: "kidded",
  dueLabel: "Kidding due",
  breedingGateCopy: "A doe must be at least 10 months old and 22 kg to breed; bucks 12 months and 25 kg.",
  dairy: false,
};

const BUFFALO_VOCABULARY: FarmVocabulary = {
  typeLabel: "Buffalo dairy",
  species: "buffalo",
  speciesPlural: "buffalo",
  femaleAdult: "milking buffalo",
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
