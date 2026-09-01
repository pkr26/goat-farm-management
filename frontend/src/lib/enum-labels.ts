/**
 * Human labels for every enum the API surfaces. Raw codes
 * ("MALE_KIDS", "ANIMAL_PURCHASE", "AI_SEXED") must never reach a
 * screen — resolve them through here so wording stays consistent and
 * species-aware in one place.
 */

import type { FarmType } from "@/lib/farm-vocabulary";

function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) =>
      /^(ai)$/i.test(word) ? word.toUpperCase() : word.charAt(0).toUpperCase() + word.slice(1),
    )
    .join(" ");
}

const SIMPLE_LABELS: Record<string, Record<string, string>> = {
  sex: { M: "Male", F: "Female" },
  source: { BORN: "Born on farm", PURCHASED: "Purchased" },
  status: { ACTIVE: "Active", SOLD: "Sold", DEAD: "Dead", CULLED: "Culled" },
  kidStatus: { ALIVE: "Alive", STILLBORN: "Stillborn", DIED: "Died" },
  ease: { NORMAL: "Normal", ASSISTED: "Assisted", DIFFICULT: "Difficult" },
  shift: { MORNING: "Morning", AFTERNOON: "Afternoon", NIGHT: "Night" },
  method: { NATURAL: "Natural", AI: "AI", AI_SEXED: "AI (sexed)" },
  eventType: {
    VACCINE: "Vaccination",
    DEWORMING: "Deworming",
    TREATMENT: "Treatment",
    FOOTBATH: "Foot bath",
    VITAMIN: "Vitamin / supplement",
  },
  txCategory: {
    ANIMAL_SALE: "Animal sale",
    ANIMAL_PURCHASE: "Animal purchase",
    FEED: "Feed",
    MEDICINE: "Medicine",
    VET: "Vet",
    LABOUR: "Labour",
    EQUIPMENT: "Equipment",
    MILK: "Milk",
    MANURE: "Manure",
    OTHER: "Other",
  },
  txType: { INCOME: "Income", EXPENSE: "Expense" },
  taskCategory: {
    VACCINE: "Vaccination",
    DEWORMING: "Deworming",
    ULTRASOUND: "Ultrasound",
    KIDDING_DUE: "Birth due",
    WEANING: "Weaning",
    BUCKET_MOVE: "Bucket move",
    QUARANTINE: "Quarantine",
    FEED: "Feeding",
    CLEANING: "Cleaning",
    OTHER: "Other",
  },
  outcome: {
    PENDING: "Awaiting check",
    CONFIRMED_PREGNANT: "Confirmed pregnant",
    FAILED: "Failed",
    ABORTED: "Aborted",
    UNASSESSED: "Left the herd unassessed",
  },
};

/** Bucket short labels are species-specific (dairy pens vs goat wards). */
const BUCKET_LABELS: Record<FarmType, Record<string, string>> = {
  GOAT: {
    QUARANTINE: "Quarantine",
    FOUNDATION: "Foundation",
    BREEDING: "Breeding",
    PREGNANCY_EARLY: "Pregnancy A",
    PREGNANCY_LATE: "Pregnancy B",
    DELIVERY: "Delivery",
    RECOVERY: "Recovery",
    RESTING: "Resting",
    MALE_KIDS: "Male kids",
    FEMALE_KIDS: "Female kids",
  },
  BUFFALO_DAIRY: {
    QUARANTINE: "Quarantine",
    FOUNDATION: "Heifers",
    BREEDING: "Awaiting AI",
    PREGNANCY_EARLY: "Milking · Pregnant 1–5 mo",
    PREGNANCY_LATE: "Milking · Pregnant 5–8 mo",
    DELIVERY: "Dry / Calving",
    RECOVERY: "Fresh pen",
    RESTING: "Post-fresh",
    MALE_KIDS: "Male calves",
    FEMALE_KIDS: "Heifer calves",
  },
};

export type EnumKind =
  | "sex"
  | "source"
  | "status"
  | "kidStatus"
  | "ease"
  | "shift"
  | "method"
  | "eventType"
  | "txCategory"
  | "txType"
  | "taskCategory"
  | "outcome"
  | "bucket";

/**
 * Resolve an enum code to its user-facing label. Unknown values fall
 * back to Title Case of the code itself so nothing renders in SCREAMING_SNAKE.
 */
export function enumLabel(
  kind: EnumKind,
  value: string | null | undefined,
  farmType?: FarmType | string | null,
): string {
  if (value === null || value === undefined || value === "") return "—";
  if (kind === "bucket") {
    const labels = BUCKET_LABELS[(farmType as FarmType) ?? "GOAT"] ?? BUCKET_LABELS.GOAT;
    return labels[value] ?? titleCase(value);
  }
  return SIMPLE_LABELS[kind]?.[value] ?? titleCase(value);
}

/** Title Case fallback exported for pages that only need humanization. */
export function humanizeEnum(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return titleCase(value);
}
