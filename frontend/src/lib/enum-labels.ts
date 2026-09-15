/**
 * Human labels for every enum the API surfaces. Raw codes
 * ("MALE_KIDS", "ANIMAL_PURCHASE", "AI_SEXED") must never reach a
 * screen — resolve them through here so wording stays consistent in one place.
 *
 * `enumLabel(kind, value, lang)` adds a Telugu map for the worker-facing
 * kinds; any value missing a Telugu entry falls back to the English label
 * (never to the raw code), matching the i18n catalog's fallback contract.
 */

import { useCallback } from "react";

import { getActiveLanguage } from "@/lib/active-language";
import { useLanguage, type Language } from "@/lib/i18n";

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
  ease: { NORMAL: "Normal", ASSISTED: "Assisted", DIFFICULT: "Difficult", CAESAREAN: "Caesarean" },
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
    EXAM: "Clinical exam",
    FECAL_EXAM: "Fecal exam",
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
    KIDDING_WATCH: "Kidding watch",
    BIRTHING_KIT: "Birthing kit check",
    HEALTH_CHECK: "Health check",
    HEAT_WATCH: "Heat watch",
    HOOF_TRIMMING: "Hoof trimming",
    SPRAYING: "Spraying",
    DISINFECTION: "Disinfection",
    WEIGHING: "Weighing",
    REBREED: "Re-breed",
    BUCK_ROTATION: "Buck rotation",
    INSURANCE: "Insurance",
  },
  // Coded mortality causes (animals.mortality_cause_code). The three
  // *_SUSPECTED entries are the notifiable-disease watches; the label says
  // "suspected" so a badge never reads as a lab confirmation.
  mortalityCause: {
    PNEUMONIA: "Pneumonia",
    DIARRHOEA: "Diarrhoea",
    COLIBACILLOSIS: "Colibacillosis",
    ENTEROTOXAEMIA: "Enterotoxaemia",
    PPR_SUSPECTED: "PPR (suspected)",
    FMD_SUSPECTED: "FMD (suspected)",
    GOAT_POX_SUSPECTED: "Goat pox (suspected)",
    PARASITISM: "Parasitism",
    COCCIDIOSIS: "Coccidiosis",
    NUTRITIONAL: "Nutritional",
    HEAT_STRESS: "Heat stress",
    PREDATION: "Predation",
    ACCIDENT: "Accident",
    DYSTOCIA: "Dystocia",
    OLD_AGE: "Old age",
    OTHER: "Other",
    UNKNOWN: "Unknown",
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
  // Bounded carcass-disposal vocabulary (animals.DisposalMethod).
  disposalMethod: {
    DEEP_BURIAL: "Deep burial",
    BURNING: "Burning / incineration",
    RENDERING: "Rendering plant",
    COMPOSTING: "Composting",
    OTHER: "Other",
  },
  outcome: {
    PENDING: "Awaiting check",
    CONFIRMED_PREGNANT: "Confirmed pregnant",
    FAILED: "Failed",
    ABORTED: "Aborted",
    UNASSESSED: "Left the herd unassessed",
  },
  // Insurance register lifecycle (finance.InsurancePolicyOut.status). The
  // wire values are lowercase ("active"); the lookup below upper-cases
  // before consulting the map so the keys stay SCREAMING_CASE like every
  // sibling kind.
  insuranceStatus: {
    ACTIVE: "Active",
    RENEWED: "Renewed",
    LAPSED: "Lapsed",
    CLAIMED: "Claimed",
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
 * Telugu labels. Every enum kind carries a full map so worker-facing pages
 * never leak English codes; anything still missing falls back to the English
 * label (never the raw code), matching the i18n catalog's fallback contract.
 */
const TE_LABELS: { [K in EnumKind]?: Record<string, string> } = {
  sex: { M: "మగ", F: "ఆడ" },
  source: { BORN: "ఫారంలో పుట్టింది", PURCHASED: "కొనుగోలు చేయబడింది" },
  status: {
    ACTIVE: "సక్రియం",
    SOLD: "అమ్మబడింది",
    DEAD: "మరణించింది",
    CULLED: "తొలగించబడింది",
  },
  kidStatus: { ALIVE: "బతికుంది", STILLBORN: "మృత జననం", DIED: "మరణించింది" },
  ease: {
    NORMAL: "సాధారణం",
    ASSISTED: "సహాయంతో",
    DIFFICULT: "కష్టమైనది",
    CAESAREAN: "సిజేరియన్",
  },
  shift: { MORNING: "ఉదయం", AFTERNOON: "మధ్యాహ్నం", NIGHT: "రాత్రి" },
  method: { NATURAL: "సహజం", AI: "AI", AI_SEXED: "AI (సెక్స్డ్)" },
  birthType: {
    SINGLE: "ఒకటి",
    TWIN: "కవలలు",
    TRIPLET: "ముగ్గురు",
    QUADRUPLET: "నలుగురు",
    MULTIPLET: "బహుళం",
  },
  eventType: {
    VACCINE: "టీకా",
    DEWORMING: "పురుగుల మందు",
    TREATMENT: "చికిత్స",
    FOOTBATH: "పాద స్నానం",
    VITAMIN: "విటమిన్ / సప్లిమెంట్",
    EXAM: "క్లినికల్ పరీక్ష",
    FECAL_EXAM: "మల పరీక్ష",
  },
  txCategory: {
    ANIMAL_SALE: "మేక అమ్మకం",
    ANIMAL_PURCHASE: "మేక కొనుగోలు",
    FEED: "మేత",
    MEDICINE: "మందులు",
    VET: "వెట్",
    LABOUR: "కూలి",
    EQUIPMENT: "పరికరాలు",
    MANURE: "ఎరువు",
    OTHER: "ఇతర",
  },
  txType: { INCOME: "ఆదాయం", EXPENSE: "ఖర్చు" },
  taskCategory: {
    VACCINE: "టీకా",
    DEWORMING: "పురుగుల మందు",
    ULTRASOUND: "అల్ట్రాసౌండ్",
    KIDDING_DUE: "పిల్లల పుట్టుక",
    WEANING: "పాలు తొలగింపు",
    BUCKET_MOVE: "పెంట మార్పు",
    QUARANTINE: "క్వారంటైన్",
    FEED: "మేత",
    CLEANING: "శుభ్రం చేయడం",
    OTHER: "ఇతర",
    KIDDING_WATCH: "పిల్లల కోసం గమనింపు",
    BIRTHING_KIT: "జనన కిట్ సరిచూడటం",
    HEALTH_CHECK: "ఆరోగ్య పరీక్ష",
    HEAT_WATCH: "తప్తు కోసం గమనింపు",
    HOOF_TRIMMING: "గిట్టు కత్తిరింపు",
    SPRAYING: "మందు పిచికలు",
    DISINFECTION: "వ్యాధి నివారణ శుభ్రం",
    WEIGHING: "బరువు చూడటం",
    REBREED: "తిరిగి సంతానం",
    BUCK_ROTATION: "మగ మేక మార్పు",
    INSURANCE: "భీమా",
  },
  mortalityCause: {
    PNEUMONIA: "న్యుమోనియా",
    DIARRHOEA: "విరేచనాలు",
    COLIBACILLOSIS: "కోలిబాసిలోసిస్",
    ENTEROTOXAEMIA: "ఎంటరోటాక్సీమియా",
    PPR_SUSPECTED: "PPR (అనుమానం)",
    FMD_SUSPECTED: "FMD (అనుమానం)",
    GOAT_POX_SUSPECTED: "మేక మచ్చలు (అనుమానం)",
    PARASITISM: "పరాన్నజీవులు",
    COCCIDIOSIS: "కొక్సిడియోసిస్",
    NUTRITIONAL: "పోషక లోపం",
    HEAT_STRESS: "వేడి ఒత్తిడి",
    PREDATION: "సంహారం",
    ACCIDENT: "ప్రమాదం",
    DYSTOCIA: "కష్టపడిన ప్రసవం",
    OLD_AGE: "ముసలితనం",
    OTHER: "ఇతర",
    UNKNOWN: "తెలియదు",
  },
  lossCause: {
    UNKNOWN: "తెలియదు",
    DISEASE: "వ్యాధి",
    INJURY: "గాయం",
    NUTRITIONAL: "పోషక లోపం",
    TRAUMA: "ట్రామా",
    ANIMAL_STATUS_CHANGE: "మంద నుండి తొలగింపు (పరిపాలనా మూసివేత)",
    OTHER: "ఇతర",
  },
  disposalMethod: {
    DEEP_BURIAL: "లోతుగా పాతడం",
    BURNING: "దహనం",
    RENDERING: "రెండరింగ్ కర్మాగారం",
    COMPOSTING: "కంపోస్టింగ్",
    OTHER: "ఇతర",
  },
  outcome: {
    PENDING: "పరీక్ష వేచి ఉంది",
    CONFIRMED_PREGNANT: "గర్భం ధృవీకరించబడింది",
    FAILED: "విఫలమైంది",
    ABORTED: "గర్భస్రావం",
    UNASSESSED: "పరీక్ష లేకుండా మంద వదిలింది",
  },
  insuranceStatus: {
    ACTIVE: "అమలులో",
    RENEWED: "నవీకరించబడింది",
    LAPSED: "రద్దైంది",
    CLAIMED: "పరిహారం అయింది",
  },
  bucket: {
    QUARANTINE: "క్వారంటైన్",
    FOUNDATION: "ఫౌండేషన్",
    BREEDING: "సంతానోత్పత్తి",
    PREGNANCY_EARLY: "గర్భం A",
    PREGNANCY_LATE: "గర్భం B",
    DELIVERY: "ప్రసూతి",
    RECOVERY: "కోలుకునే దశ",
    RESTING: "విశ్రాంతి",
    MALE_KIDS: "మగ పిల్లలు",
    FEMALE_KIDS: "ఆడ పిల్లలు",
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
  | "mortalityCause"
  | "outcome"
  | "lossCause"
  | "disposalMethod"
  | "insuranceStatus"
  | "bucket";

/**
 * Resolve an enum code to its user-facing label. Unknown values fall
 * back to Title Case of the code itself so nothing renders in SCREAMING_SNAKE.
 * With `lang: "te"` a translated label wins when one exists; everything
 * else falls back to the English label above. When `lang` is omitted the
 * active UI language (synced from the LanguageProvider into the module
 * store) is used, so call sites that never pass a language localize for free.
 */
export function enumLabel(
  kind: EnumKind,
  value: string | null | undefined,
  lang?: Language,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const language = lang ?? getActiveLanguage();
  if (language === "te") {
    const telugu = TE_LABELS[kind]?.[value] ?? TE_LABELS[kind]?.[value.toUpperCase()];
    if (telugu) return telugu;
  }
  if (kind === "bucket") {
    return BUCKET_LABELS[value] ?? titleCase(value);
  }
  // The insurance lifecycle arrives lowercase from the API; the maps above
  // stay SCREAMING_CASE like every sibling kind, so retry upper-cased.
  // Stryker disable next-line OptionalChaining: every EnumKind has a SIMPLE_LABELS entry, so the kind lookup never yields undefined
  return (
    SIMPLE_LABELS[kind]?.[value] ?? SIMPLE_LABELS[kind]?.[value.toUpperCase()] ?? titleCase(value)
  );
}

/** React binding for components: returns an enumLabel pre-bound to the
 * active language, re-rendering the component when the language changes. */
export function useEnumLabel(): (kind: EnumKind, value: string | null | undefined) => string {
  const { language } = useLanguage();
  return useCallback(
    (kind: EnumKind, value: string | null | undefined) => enumLabel(kind, value, language),
    [language],
  );
}

/** Title Case fallback exported for pages that only need humanization. */
export function humanizeEnum(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return titleCase(value);
}
