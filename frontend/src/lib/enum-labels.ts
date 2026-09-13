/**
 * Human labels for every enum the API surfaces. Raw codes
 * ("MALE_KIDS", "ANIMAL_PURCHASE", "AI_SEXED") must never reach a
 * screen — resolve them through here so wording stays consistent in one place.
 *
 * `enumLabel(kind, value, lang)` adds a Telugu map for the worker-facing
 * kinds; any value missing a Telugu entry falls back to the English label
 * (never to the raw code), matching the i18n catalog's fallback contract.
 */

import type { Language } from "@/lib/i18n";

// Stryker disable next-line Regex: the chained .filter(Boolean) below already drops the empty strings a single-character split would produce, so the greedy quantifier never changes the result
const SEPARATOR_RUN = /[_\s-]+/;

function titleCase(value: string): string {
  return value
    .toLowerCase()
    .split(SEPARATOR_RUN)
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
  birthType: {
    SINGLE: "Single",
    TWIN: "Twin",
    TRIPLET: "Triplet",
    QUADRUPLET: "Quadruplet",
    MULTIPLET: "Multiplet",
  },
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
  lossCause: {
    UNKNOWN: "Unknown",
    DISEASE: "Disease",
    INJURY: "Injury",
    NUTRITIONAL: "Nutritional",
    TRAUMA: "Trauma",
    ANIMAL_STATUS_CHANGE: "Herd exit (administrative close)",
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

/** Bucket short labels for the goat herd-flow wards. */
const BUCKET_LABELS: Record<string, string> = {
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
};

/**
 * Telugu labels — deliberately partial. Only codes with well-established
 * Telugu terms (audit glossary or common farm usage) are translated; the
 * rest render the English label rather than an invented transliteration.
 */
const TE_LABELS: { [K in EnumKind]?: Record<string, string> } = {
  sex: { M: "మగ", F: "ఆడ" },
  taskCategory: {
    VACCINE: "టీకా",
    DEWORMING: "పురుగుల మందు",
    KIDDING_DUE: "పిల్లల పుట్టుక",
    FEED: "మేత",
    CLEANING: "శుభ్రం చేయడం",
    OTHER: "ఇతర",
  },
  shift: { MORNING: "ఉదయం", AFTERNOON: "మధ్యాహ్నం", NIGHT: "రాత్రి" },
  txType: { INCOME: "ఆదాయం", EXPENSE: "ఖర్చు" },
  bucket: {
    MALE_KIDS: "మగ పిల్లలు",
    FEMALE_KIDS: "ఆడ పిల్లలు",
    BREEDING: "సంతానోత్పత్తి",
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
  | "birthType"
  | "txCategory"
  | "txType"
  | "taskCategory"
  | "outcome"
  | "lossCause"
  | "bucket";

/**
 * Resolve an enum code to its user-facing label. Unknown values fall
 * back to Title Case of the code itself so nothing renders in SCREAMING_SNAKE.
 * With `lang: "te"` a translated label wins when one exists; everything
 * else falls back to the English label above.
 */
export function enumLabel(
  kind: EnumKind,
  value: string | null | undefined,
  // Stryker disable next-line StringLiteral: the empty string only fails the "te" comparison below, which yields the English catalog exactly like "en"
  lang: Language = "en",
): string {
  if (value === null || value === undefined || value === "") return "—";
  if (lang === "te") {
    const telugu = TE_LABELS[kind]?.[value];
    if (telugu) return telugu;
  }
  if (kind === "bucket") {
    return BUCKET_LABELS[value] ?? titleCase(value);
  }
  // Stryker disable next-line OptionalChaining: every EnumKind has a SIMPLE_LABELS entry, so the kind lookup never yields undefined
  return SIMPLE_LABELS[kind]?.[value] ?? titleCase(value);
}

/** Title Case fallback exported for pages that only need humanization. */
export function humanizeEnum(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return titleCase(value);
}
