/**
 * Plain-language help for every simulation assumption field.
 *
 * The simulation editor renders whatever keys the backend defaults payload
 * carries, so its labels were raw snake_case humanizations ("Foundation Doe
 * Age Min Months") with no explanation. This module gives every field:
 *
 * - a short "what it is and what the values mean" explanation
 *   (`simulationFieldHelp`) shown by the "?" button next to each label,
 *   alongside the unit and allowed range the editor already derives.
 *
 * Help text mirrors the authoritative field documentation in
 * backend/app/simulation/assumptions.py; wording drift here shows farmers a
 * model the engine does not run.
 */

import type { FarmVocabulary } from "@/lib/farm-vocabulary";


/** The vocabulary type carries no male plural; bucks takes a plain -s. */
function malePlural(v: FarmVocabulary): string {
  return `${v.maleAdult}s`;
}

export interface FieldHelp {
  /** One to three sentences: what the term means and how to read the values. */
  body: string;
}

/** Humanized field label as displayed. */
export function speciesAwareLabel(base: string): string {
  return base;
}

/** Section-level help shown by the "?" beside each assumptions section. */
export const SIMULATION_SECTION_HELP: Record<string, (v: FarmVocabulary) => string> = {
  meta: () =>
    "How long the projection runs and the calendar month it starts from. Seasonal prices (festivals, lean seasons) are placed on real months from these two values.",
  herd: (v) =>
    `The animals on the ground on day 1, their purchase prices, and the replacement policy: how many home-grown females are kept as breeding stock and whether the breeding pool has a ceiling. A bought-in adult ${v.femaleAdult} first spends a settling period acclimatising before her first service.`,
  reproduction: (v) =>
    `Breeding biology at monthly resolution: conception per service, gestation length, ${v.parturition} interval, and the repeat-breeder cull rule.`,
  mortality: () =>
    `Death rates by class. Kid and weaner rates are whole-phase rates (the share of a crop lost across the whole 3-month class); grower and adult rates are annual.`,
  culling: (v) =>
    `Why animals leave the breeding pool on purpose: the annual cull rate, the maximum ${v.femaleAdult} age, sire rotation, and how many ${v.femaleAdultPlural} one ${v.maleAdult} serves.`,
  growth: () =>
    `The live-weight curve from birth to adult and the age at which surplus young stock is sold for meat. Sale weight — and therefore sale revenue — comes straight off this curve.`,
  sales: (v) =>
    `Market prices and selling terms: meat price per kg live weight, cull prices, festival timing and uplift, seasonality curves, and what happens to male ${v.youngPlural}.`,
  feed: () =>
    "Ration physics and prices: dry-matter intake per class as a share of body weight, concentrate/green/dry splits, fodder you grow versus buy, and how the feed budget escalates over the years.",
  costs: () =>
    "Recurring running costs (vet, labour, insurance, overheads) and the capital cost of sheds and equipment per animal place, including how capacity is sized.",
  finance: () =>
    "How the project is funded and judged: loan share, interest, term and moratorium, subsidy, the discount rate for NPV, working capital, tax, and what closing assets are worth at the end.",
  risk: () =>
    "Monte Carlo controls: how many risk runs, the random seed, the triangular spread each uncertain parameter is drawn from, and the probability/severity of disease, drought and market crashes.",
  optimization: () =>
    "The bounded search that varies herd size, sale age, retention and loan share to find the best feasible plan under your DSCR, cost and funding-gap constraints.",
};

/** Per-field help, keyed by "section.key" exactly as the editor addresses it. */
const FIELD_HELP: Record<string, (v: FarmVocabulary) => FieldHelp> = {
  // --- meta ---------------------------------------------------------------
  "meta.horizon_months": () => ({
    body: "How many months the projection runs. 120 months = 10 years, the standard appraisal window. All cost, revenue and loan figures cover exactly this span.",
  }),
  "meta.start_year_month": () => ({
    body: "The calendar month simulation month 1 falls in (format YYYY-MM). Festival months and the January-indexed seasonal price curves are placed on real calendar months from this anchor.",
  }),

  // --- herd ---------------------------------------------------------------
  "herd.does": (v) => ({
    body: `Adult breeding ${v.femaleAdultPlural} in the starting herd. These are the foundation animals: they are spread across ages and reproductive states per the foundation settings below, and the whole projection grows from them.`,
  }),
  "herd.bucks": (v) => ({
    body: `Adult ${malePlural(v)} in the starting herd. 0 is normal for an AI-first herd (no sire battery carried) — service capacity is then unlimited; for natural service keep roughly one ${v.maleAdult} per buck-doe-ratio females.`,
  }),
  "herd.female_growers": () => ({
    body: `Female young stock aged 6 months up to first-breeding age already on the ground at month 1. They graduate into the breeding pool per the retention fraction.`,
  }),
  "herd.male_growers": () => ({
    body: `Male young stock aged 6 months up to the sale age already on the ground at month 1. They are sold for meat when they reach the sale age.`,
  }),
  "herd.female_weaners": () => ({
    body: "Female weaners (age 3–5 months) in the starting herd. Placed mid-class; they grow on and age into the grower pool.",
  }),
  "herd.male_weaners": () => ({
    body: "Male weaners (age 3–5 months) in the starting herd. They grow on to the sale age and are sold for meat.",
  }),
  "herd.female_kids": (v) => ({
    body: `Female ${v.youngPlural} (age 0–2 months) in the starting herd. Placed mid-class; they face the pre-weaning mortality rate first.`,
  }),
  "herd.male_kids": (v) => ({
    body: `Male ${v.youngPlural} (age 0–2 months) in the starting herd. The at-birth sale fraction applies only to newborns during the run, not to this opening count.`,
  }),
  "herd.female_retention_fraction": (v) => ({
    body: `Share of home-grown females reaching breeding age that are KEPT as replacements; the rest are sold for meat at that age. Raise it to grow the ${v.femaleAdult} pool, lower it to cash surplus young stock. It must cover the cull + mortality outflow or the herd shrinks.`,
  }),
  "herd.max_breeding_does": (v) => ({
    body: `Ceiling on the breeding-${v.femaleAdult} pool. 0 means unlimited — the herd grows as far as retention takes it. The default 50 holds a goat unit at the NABARD 50+2 size and sells every surplus replacement.`,
  }),
  "herd.doe_purchase_price": (v) => ({
    body: `Purchase price of one adult ${v.femaleAdult} (₹). Prices the starting stock for the project cost and any scheduled or planner purchases of adult females.`,
  }),
  "herd.buck_purchase_price": (v) => ({
    body: `Purchase price of one adult ${v.maleAdult} (₹). Used when auto-purchase replaces sires or a scheduled event buys ${malePlural(v)}.`,
  }),
  "herd.auto_purchase_bucks": (v) => ({
    body: `When on, the model automatically buys ${malePlural(v)} whenever the sire ratio falls short, so breeding is never sire-limited. Turn off for an AI programme (technician-limited service) — then 0 ${malePlural(v)} still breeds the whole herd.`,
  }),
  "herd.foundation_doe_age_min_months": (v) => ({
    body: `Youngest age (months) of the foundation ${v.femaleAdultPlural} bought at month 1 — with the max below, the opening animals are spread uniformly across this window. Buying young proven animals avoids an immediate max-age cull wave. Must be ≤ the max.`,
  }),
  "herd.foundation_doe_age_max_months": (v) => ({
    body: `Oldest age (months) of the foundation ${v.femaleAdultPlural}. Animals near the max ${v.femaleAdult} age face age culling soon — a window close to the max age sends a rolling cull wave through the first years.`,
  }),
  "herd.purchased_doe_settling_months": (v) => ({
    body: `Months a bought-in adult ${v.femaleAdult} spends settling (transport stress, new ration) before her first service. She is fed, insured and mortal during settling but cannot conceive. 0 = same-month breeding.`,
  }),
  "herd.foundation_flock_state": (v) => ({
    body: `Reproductive spread of the foundation ${v.femaleAdultPlural} at month 1. "Mixed" spreads them uniformly across the cycle — a realistic purchased flock with some pregnant, some lactating, some open, so income starts in year 1. "Open" starts every animal empty and ready to breed in month 1 (cleaner projection start).`,
  }),

  // --- reproduction -------------------------------------------------------
  "reproduction.conception_rate": () => ({
    body: "Share of services that conceive, per service. ~0.85 for naturally served goats; AI services run lower under field conditions. Every failed service pushes the next conception attempt a month later.",
  }),
  "reproduction.gestation_months": (v) => ({
    body: `Months from conception to ${v.parturition} (~5 for goats). Whole months only.`,
  }),
  "reproduction.lactation_months": (v) => ({
    body: `Months the ${v.femaleAdult} stays in the nursing pool after each ${v.parturition} before her rebreed wait begins — the weaning-plus-rebreed interval, not a saleable-milk length. Default 2 matches the day-60 wean; 90-day weaning derives 3.`,
  }),
  "reproduction.weaning_days": () => ({
    body: "When kids are weaned off the dam. 60 days is the operational standard (the ~8-month kidding interval); 90 is the conservative research standard — better kid thrift, but it stretches the cycle by a month.",
  }),
  "reproduction.months_open_before_breeding": (v) => ({
    body: `Months after ${v.parturition} before the female is served again (voluntary waiting period). ~1 for goats.`,
  }),
  "reproduction.litter_size": (v) => ({
    body: `${v.youngPlural} born per ${v.parturition} (average — 1.6 means twins roughly half the time for goats). The model caps litters at the species maximum (4 for goats).`,
  }),
  "reproduction.sex_ratio_female": () => ({
    body: "Share of births that are female under natural service (~0.5).",
  }),
  "reproduction.age_at_first_breeding_months": () => ({
    body: `Age at which a home-grown female is first served (and the age at which surplus females are sold). ~10 months/22 kg for goats.`,
  }),
  "reproduction.stillbirth_rate": () => ({
    body: "Share of births born dead — lost before any meat value accrues.",
  }),
  "reproduction.max_services_before_cull": () => ({
    body: `A female failing this many consecutive services is culled as a repeat breeder (standard 3-service discipline). 0 disables — females are re-served indefinitely. Repeat breeders leave the breeding pool immediately.`,
  }),

  // --- mortality ------------------------------------------------------------
  "mortality.kid_pre_weaning": (v) => ({
    body: `Share of each ${v.young} crop lost from birth to weaning (whole 0–2 month phase, the way literature quotes it: 5–15% stall-fed). Not an annual rate.`,
  }),
  "mortality.kid_post_weaning": (v) => ({
    body: `Share of the ${v.young} crop lost across the whole weaner phase (months 3–5).`,
  }),
  "mortality.grower": () => ({
    body: `Annual death rate of growers (young stock from 6 months to sale/first breeding), compounded monthly.`,
  }),
  "mortality.adult": (v) => ({
    body: `Annual death rate of adult ${v.femaleAdultPlural} and ${malePlural(v)}, compounded monthly.`,
  }),

  // --- culling --------------------------------------------------------------
  "culling.doe_cull_rate_annual": () => ({
    body: `Share of the breeding pool culled each year on purpose (age, teeth, udder, productivity). Applied from month 13 — the foundation stock gets one full year of grace. Together with mortality and any repeat-breeder rule it sets herd life: 20%/yr ≈ a 5-year breeding life.`,
  }),
  "culling.max_doe_age_months": (v) => ({
    body: `Maximum ${v.femaleAdult} age: animals reaching it are culled. Goats stay productive to roughly 72 months (6 years). Must be at least 36.`,
  }),
  "culling.buck_rotation_years": (v) => ({
    body: `Years a ${v.maleAdult} serves before being rotated out (replaced) to avoid inbreeding.`,
  }),
  "culling.buck_doe_ratio": (v) => ({
    body: `Females per ${v.maleAdult}: one ${v.maleAdult} can serve this many ${v.femaleAdultPlural} (published 1:20–30; the app's operational limit is 1:20). Service capacity constrains conception when the battery is short.`,
  }),

  // --- growth ---------------------------------------------------------------
  "growth.birth_weight_kg": () => ({
    body: `Live weight at birth. Must equal the first value of the weight-by-age curve (the editor keeps them in sync). ~2.5–3.5 kg for goats.`,
  }),
  "growth.adult_weight_doe_kg": (v) => ({
    body: `Mature live weight of an adult ${v.femaleAdult} — drives feed intake, cull revenue and stock value. ~33 kg Osmanabadi.`,
  }),
  "growth.adult_weight_buck_kg": (v) => ({
    body: `Mature live weight of an adult ${v.maleAdult}. ~42 kg Osmanabadi.`,
  }),
  "growth.weight_by_age_months": () => ({
    body: `Exactly 13 comma-separated live weights (kg) at ages 0–12 months — the growth curve every young-animal weight is read from. Must not decrease with age, and age 0 must equal the birth weight. After month 12 the curve approaches the adult weight linearly.`,
  }),
  "growth.adult_weight_age_months": () => ({
    body: `Age at which the animal reaches its adult weight (curve matures). Goats ~24 months — maturing them too early overstates weights, feed and sale value.`,
  }),
  "growth.growth_regime": () => ({
    body: "Which calibrated growth curve the weight-by-age table defaults to: stall_fed (managed-herd curve that finishes males into the 8–9 month sale window) or semi_intensive (grazing field curve — roughly half the early gains, so field males sell later and lighter).",
  }),
  "growth.young_male_weight_premium": () => ({
    body: "How much heavier young males run than female contemporaries, as a fraction (0.10 = 10% heavier in goats). Adult male weight is set explicitly, not via this premium.",
  }),
  "growth.sale_age_months": () => ({
    body: `Age at which surplus young stock is sold for meat at its live-weight price. 8–9 months for stall-fed goats (24–28 kg). Must be ≥ 6 so animals pass through the grower stage.`,
  }),

  // --- sales ----------------------------------------------------------------
  "sales.meat_price_per_kg": () => ({
    body: `Annual-mean farm-gate price per kg LIVE weight for young stock sold for meat (₹/kg). Seasonal multipliers and festival uplift move months around this mean; cull animals have their own prices below. This is the single price the break-even metric searches.`,
  }),
  "sales.cull_doe_price_per_kg": (v) => ({
    body: `Price per kg live weight for culled ${v.femaleAdultPlural} (₹/kg) — spent animals price below young stock. Cull revenue is a material income line.`,
  }),
  "sales.cull_buck_price_per_kg": (v) => ({
    body: `Price per kg live weight for culled/rotated-out ${malePlural(v)} (₹/kg).`,
  }),
  "sales.monthly_meat_price_multipliers": () => ({
    body: "Twelve January-indexed multipliers (Jan first) scaling the mean meat price by calendar month. Auto-normalized to average exactly 1.0 so the base price stays the annual mean.",
  }),
  "sales.annual_livestock_price_growth_rate": () => ({
    body: "Nominal annual growth of meat and cull prices (0.04 = 4%/yr). Enter as a fraction; negative values model a declining market.",
  }),
  "sales.eid_month": () => ({
    body: "Legacy way to schedule the Bakrid price uplift: the calendar MONTH (1–12) it applies in every year. Prefer the explicit festival months list below; 0 disables this field.",
  }),
  "sales.eid_price_uplift": () => ({
    body: "Bakrid (Eid al-Adha) sacrificial-demand premium on the live price in festival months (0.35 = +35%, the documented 30–60% range's conservative mid). Applies only to young-stock meat sales, not culls.",
  }),
  "sales.festival_sale_months": () => ({
    body: "Explicit 1-based SIMULATION months in which the festival premium applies — the accurate way to model a lunar festival over a multi-year plan (auto-filled from the Bakrid calendar for meat breeds). An empty list disables festival pricing; months beyond the horizon are pruned, not rejected.",
  }),
  "sales.festival_hold_months": () => ({
    body: "Males whose sale age falls this many months BEFORE a festival month are held (still growing, eating and mortal) and sold in the festival month at the festival price — Telangana herds finish into Bakrid. 0 = sell every male the month he finishes.",
  }),
  "sales.selling_cost_fraction": () => ({
    body: "Direct selling cost as a fraction of livestock revenue (mandi commission etc., 0.05 = 5%). Reported as a cost line, never netted out of the price you enter.",
  }),
  "sales.transport_cost_per_head": () => ({
    body: "Transport/handling per head sold (₹). Added to selling cost per animal sold.",
  }),
  "sales.milk_sale_litres_per_doe_day": (v) => ({
    body: `Saleable surplus milk per lactating ${v.femaleAdult} per day (litres). 0 = nothing sold (the default — this is a meat projection with a small milk side-line, not a dairy model). Osmanabadi does genuinely yield 0.5–1.5 kg/day over the ~60–90 day nursing window.`,
  }),
  "sales.milk_price_per_litre": () => ({
    body: "₹/litre for the surplus-milk line (~₹30 farm-gate for goat milk sold locally in Telangana). Only applies when the litres-per-doe-day above is above zero.",
  }),
  "sales.manure_income_per_adult_per_year": () => ({
    body: `Yearly income per adult animal from manure/dung (₹ — slurry, biogas savings, or sale). A small but real income line; counted separately from meat.`,
  }),

  // --- feed -------------------------------------------------------------------
  "feed.dmi_kid_creep": (v) => ({
    body: `Dry-matter intake of a suckling ${v.young} as a share of its body weight (creep feed). Fraction of body weight per day, not kg.`,
  }),
  "feed.dmi_weaner": () => ({
    body: "Dry-matter intake of a weaner (3–5 months) as a share of body weight per day.",
  }),
  "feed.dmi_grower": () => ({
    body: `Dry-matter intake of a grower (6 months to sale/first breeding) as a share of body weight per day.`,
  }),
  "feed.dmi_doe_maintenance": (v) => ({
    body: `Dry-matter intake of a dry/non-milking adult ${v.femaleAdult} at maintenance, as a share of body weight per day.`,
  }),
  "feed.dmi_doe_pregnant": (v) => ({
    body: `Dry-matter intake of a pregnant ${v.femaleAdult} as a share of body weight per day (above maintenance).`,
  }),
  "feed.dmi_doe_lactating": (v) => ({
    body: `Dry-matter intake of a nursing ${v.femaleAdult} as a share of body weight per day — the biggest feed line while ${v.youngPlural} are on her.`,
  }),
  "feed.dmi_buck": (v) => ({
    body: `Dry-matter intake of an adult ${v.maleAdult} as a share of body weight per day.`,
  }),
  "feed.concentrate_share_kid_creep": (v) => ({
    body: `Concentrate's share of the suckling ${v.young}'s dry matter (creep feed is mostly concentrate). The rest splits green:dry fodder 2:1.`,
  }),
  "feed.concentrate_share_weaner": () => ({
    body: "Concentrate share of a weaner's dry matter.",
  }),
  "feed.concentrate_share_grower": () => ({
    body: "Concentrate share of a grower's dry matter.",
  }),
  "feed.concentrate_share_doe_maintenance": (v) => ({
    body: `Concentrate share of a maintenance (dry) ${v.femaleAdult}'s dry matter — near zero in a straw-based dry period.`,
  }),
  "feed.concentrate_share_doe_pregnant": (v) => ({
    body: `Concentrate share of a pregnant ${v.femaleAdult}'s dry matter (steamed-up before kidding).`,
  }),
  "feed.concentrate_share_doe_lactating": (v) => ({
    body: `Concentrate share of a nursing ${v.femaleAdult}'s dry matter — the ration's cost driver.`,
  }),
  "feed.concentrate_share_buck": (v) => ({
    body: `Concentrate share of an adult ${v.maleAdult}'s dry matter.`,
  }),
  "feed.green_dm_pct": () => ({
    body: "Dry-matter content of green fodder as a fraction of the as-fed weight (fresh maize/napier ≈ 0.20–0.25) — converts kg DM into as-fed kg you actually cut and carry.",
  }),
  "feed.dry_dm_pct": () => ({
    body: "Dry-matter content of dry fodder (paddy straw ≈ 0.88) as a fraction of as-fed weight.",
  }),
  "feed.concentrate_dm_pct": () => ({
    body: "Dry-matter content of concentrate as a fraction of as-fed weight (≈0.90).",
  }),
  "feed.green_price_per_kg": () => ({
    body: "HOME-GROWN production cost per kg as-fed green fodder (₹) — what growing it costs you. Keep separate from the purchase price below: land you own is not free, but it is cheaper than the market.",
  }),
  "feed.purchased_green_price_per_kg": () => ({
    body: "Market price per kg as-fed green fodder (₹) paid when your cultivated supply falls short.",
  }),
  "feed.dry_price_per_kg": () => ({
    body: "Price per kg as-fed dry fodder (₹) — paddy straw runs ₹4–6/kg.",
  }),
  "feed.concentrate_price_per_kg": () => ({
    body: "Price per kg as-fed concentrate (₹) — commercial goat feed ₹22–28.",
  }),
  "feed.annual_feed_price_growth_rate": () => ({
    body: "Nominal annual growth of ALL feed prices (0.06 = 6%/yr — the maize/ethanol structural driver). Feed is the largest cost line; this rate moves NPV more than most revenue levers.",
  }),
  "feed.monthly_green_price_multipliers": () => ({
    body: "Twelve January-indexed multipliers for green-fodder prices by calendar month (lean-season spikes).",
  }),
  "feed.monthly_dry_price_multipliers": () => ({
    body: "Twelve January-indexed multipliers for dry-fodder prices by calendar month.",
  }),
  "feed.monthly_concentrate_price_multipliers": () => ({
    body: "Twelve January-indexed multipliers for concentrate prices by calendar month.",
  }),
  "feed.water_litres_kid_per_day": (v) => ({
    body: `Planning water demand for a suckling ${v.young} (litres/head/day). The engine reports monthly litres and the herd's peak daily demand from these per-class rates.`,
  }),
  "feed.water_litres_weaner_per_day": () => ({
    body: "Planning water demand for a weaner (3–5 months), litres/head/day.",
  }),
  "feed.water_litres_grower_per_day": () => ({
    body: "Planning water demand for a grower (6 months to sale/first breeding), litres/head/day.",
  }),
  "feed.water_litres_doe_per_day": (v) => ({
    body: `Planning water demand for a dry adult ${v.femaleAdult} (litres/head/day; Deccan guidance runs 5–10 L).`,
  }),
  "feed.water_litres_lactating_doe_per_day": (v) => ({
    body: `Planning water demand for a nursing ${v.femaleAdult} (litres/head/day) — the single hardest summer line: a nursing ${v.femaleAdult} in the Telangana summer genuinely drinks 10–15 L/day.`,
  }),
  "feed.water_litres_buck_per_day": (v) => ({
    body: `Planning water demand for an adult ${v.maleAdult} (litres/head/day).`,
  }),
  "feed.grazing_dm_fraction": () => ({
    body: "Share of dry matter obtained free from grazing (0 = stall-fed; ~0.3 = semi-intensive). Grazed DM costs nothing but field exposure raises mortality in the semi-intensive system preset.",
  }),
  "feed.cultivated_fodder_acres": () => ({
    body: "Acres of your own fodder plot. Supply = acres × yield below, spread over the year by the monthly yield curve; shortfalls are bought at the purchase price.",
  }),
  "feed.fodder_yield_t_dm_per_acre_year": () => ({
    body: "Tonnes of fodder DRY MATTER per acre per year (single-cut maize ≈ 5; multi-cut napier/BMR sorghum 8–16). Sets how much land the herd's green DM actually needs.",
  }),
  "feed.monthly_fodder_yield_multipliers": () => ({
    body: "Twelve January-indexed multipliers for fodder growth by calendar month (rainy flush, summer trough). Normalized so the annual yield stays exactly what you stated.",
  }),
  "feed.initial_fodder_stock_kg_dm": () => ({
    body: "Green-fodder dry matter already in storage at month 1 (kg DM). Cannot exceed the storage capacity below.",
  }),
  "feed.fodder_storage_capacity_kg_dm": () => ({
    body: "Maximum fodder dry matter storage (kg DM) — silage/hay capacity. Production above it is wasted.",
  }),
  "feed.fodder_storage_loss_fraction_monthly": () => ({
    body: "Share of STORED fodder dry matter lost per month (spoilage; silage ≈ 2%).",
  }),

  // --- costs ------------------------------------------------------------------
  "costs.vet_per_animal_per_year": () => ({
    body: "Veterinary cost per animal per year (₹) — retainer, vaccines, dewormers. 2025-26 private-practice rates run ₹400–600/head.",
  }),
  "costs.labour_per_month": () => ({
    body: "Monthly wage per worker (₹). The number of workers scales with herd size per the threshold below.",
  }),
  "costs.labour_per_head_threshold": () => ({
    body: `Head per worker: one labourer is hired per this many animals. ~50 for a stall-fed goat unit. Very large values mean labour never scales with the herd.`,
  }),
  "costs.insurance_pct_stock_value_annual": () => ({
    body: "Annual livestock insurance premium as a fraction of herd value (0.04 = 4%/yr). Charged on the current stock value every month.",
  }),
  "costs.misc_overhead_per_month": () => ({
    body: "Fixed monthly overhead beyond feed/labour/vet/insurance (₹) — electricity, repairs, office.",
  }),
  "costs.operating_cost_growth_rate_annual": () => ({
    body: "Annual escalation of labour, vet and overheads (0.05 = 5%/yr, recent Indian CPI). Feed and prices have their own growth rates.",
  }),
  "costs.shed_cost_per_animal_place": () => ({
    body: "Construction cost per animal PLACE in the shed (₹) — raised-floor goat housing ₹5,500–7,000.",
  }),
  "costs.equipment_cost_per_animal": () => ({
    body: "Equipment cost per animal place (₹) — feeders, waterers and other pen equipment.",
  }),
  "costs.capacity_basis": () => ({
    body: "\"Projected peak\" funds housing/equipment for the largest headcount the projection reaches plus a reserve (recommended). \"Planned capacity\" funds your stated number. \"Opening herd\" funds only month-1 stock (legacy comparisons).",
  }),
  "costs.planned_capacity_head": () => ({
    body: "Your own capacity number (head), used only when the capacity basis is \"Planned capacity\". Must be > 0 then.",
  }),
  "costs.capacity_buffer_fraction": () => ({
    body: "Reserve places added on top of the capacity basis (0.10 = 10% spare housing).",
  }),
  "costs.shed_useful_life_years": () => ({
    body: "Depreciable life of the shed (years) — straight-line depreciation and residual value come from it.",
  }),
  "costs.equipment_useful_life_years": () => ({
    body: "Depreciable life of equipment (years).",
  }),
  "costs.shed_residual_fraction": () => ({
    body: "Share of shed cost recoverable at the end (salvage; 0.10 = 10%).",
  }),
  "costs.equipment_residual_fraction": () => ({
    body: "Share of equipment cost recoverable at the end.",
  }),

  // --- finance ------------------------------------------------------------------
  "finance.initial_stock_cost": () => ({
    body: "Explicit cost of the starting herd (₹). 0 = auto-computed from the starting head counts × purchase prices.",
  }),
  "finance.loan_fraction_of_project_cost": () => ({
    body: "Share of the project cost borrowed from the bank (0.85 = 85%, the NABARD refinance structure). Loan + subsidy cannot exceed 100%.",
  }),
  "finance.interest_rate_annual": () => ({
    body: "Annual loan interest rate (0.09 = 9%, agri term lending).",
  }),
  "finance.loan_term_months": () => ({
    body: "Loan repayment period in months. If the term outlives the projection, the outstanding balance is charged as a balloon in the final month.",
  }),
  "finance.moratorium_months": () => ({
    body: "Interest-only months at the start of the loan (NABARD schemes ≈ 12) — breathing room while the herd starts producing. Must be shorter than the term.",
  }),
  "finance.subsidy_fraction": () => ({
    body: "Capital subsidy as a share of project cost (0.02 = 2%) — money you neither invest nor repay; reduces your equity.",
  }),
  "finance.discount_rate_annual": () => ({
    body: "The return you require on your money (0.12 = 12%) — the rate NPV discounts future cash flows at. NPV > 0 means the project beats this rate.",
  }),
  "finance.nlm_subsidy": () => ({
    body: "National Livestock Mission goat-unit toggle: when on, the engine replaces the subsidy fraction with the scheme's 50% back-ended capital subsidy, capped per unit size (eligible capital ~₹10,000 per breeding head), and loan + subsidy never exceed the project cost.",
  }),
  "finance.working_capital_months": () => ({
    body: "Months of year-1 running costs held inside the project cost as the opening cash buffer. A breeding-start unit sells nothing for ~a year — 12 months is the honest default.",
  }),
  "finance.income_tax_rate": () => ({
    body: "Tax rate on profits (0.0 = tax-free agricultural income default). Applied after interest and depreciation, with optional loss carry-forward.",
  }),
  "finance.tax_loss_carryforward": () => ({
    body: "When on, early-year losses offset later profits before tax is charged (the way real farm accounts work).",
  }),
  "finance.include_terminal_value": () => ({
    body: "Count the closing herd, sheds, equipment and working capital as a final recovery in the last month — a continuing business still owns these. Off = nothing recovered at the horizon.",
  }),
  "finance.terminal_livestock_realization_fraction": () => ({
    body: "Share of closing herd VALUE actually realizable if sold at the horizon (0.90 = 90% — a rushed sale discounts).",
  }),
  "finance.terminal_asset_realization_fraction": () => ({
    body: "Share of closing shed/equipment cost recoverable at the horizon (use the residuals; 1.0 = full).",
  }),
  "finance.terminal_working_capital_recovery_fraction": () => ({
    body: "Share of the working-capital reserve recovered at the end (1.0 = it is still cash).",
  }),
  "finance.reinvestment_rate_annual": () => ({
    body: "Rate positive cash flows are assumed to earn when computing MIRR (0.08 = 8%) — the conservative reinvestment assumption that keeps MIRR single-valued.",
  }),

  // --- risk ----------------------------------------------------------------------
  "risk.monte_carlo_runs": () => ({
    body: "Number of randomized re-runs behind the Monte Carlo distributions (P5–P95, P(NPV<0)...). More runs = smoother percentiles and longer compute; 200–500 is plenty for planning.",
  }),
  "risk.seed": () => ({
    body: "Random seed — the same seed reproduces the same risk paths exactly, so comparisons between runs are apples-to-apples. Change it to sample a different set of futures.",
  }),
  "risk.correlation_strength": () => ({
    body: "How strongly the risk factors move together (0 = independent, 0.95 = near-locked). Real farms see bad years cluster — disease WITH feed scarcity WITH price crashes; higher correlation fattens the tail risks.",
  }),
  "risk.disease_outbreak_probability_annual": () => ({
    body: "Chance of a disease outbreak (FMD, PPR, LSD) in any year (0.10 = once a decade-ish). During an outbreak the multipliers below apply for its duration.",
  }),
  "risk.disease_outbreak_duration_months": () => ({
    body: "How long an outbreak lasts when it happens (months).",
  }),
  "risk.disease_adult_mortality_multiplier": () => ({
    body: "How many times higher adult mortality runs during an outbreak (2.0 = double).",
  }),
  "risk.disease_kid_mortality_multiplier": (v) => ({
    body: `How many times higher ${v.young}/weaner mortality runs during an outbreak (2.5 = two-and-a-half times).`,
  }),
  "risk.disease_conception_multiplier": () => ({
    body: "Conception-rate multiplier during an outbreak (0.70 = 30% fewer conceptions per service).",
  }),
  "risk.drought_probability_annual": () => ({
    body: "Chance of a drought/failed monsoon in any year. Triggers the fodder-yield crash and feed-price spike below for its duration.",
  }),
  "risk.drought_duration_months": () => ({
    body: "How long a drought's effects last (months).",
  }),
  "risk.drought_fodder_yield_multiplier": () => ({
    body: "Fodder-yield multiplier during a drought (0.50 = half your usual fodder harvest).",
  }),
  "risk.drought_feed_price_multiplier": () => ({
    body: "Feed-price multiplier during a drought (1.30 = +30% — scarcity pricing).",
  }),
  "risk.market_crash_probability_annual": () => ({
    body: "Chance of a livestock-price crash in any year; during one, meat/cull prices take the multiplier below.",
  }),
  "risk.market_crash_duration_months": () => ({
    body: "How long a market crash lasts (months).",
  }),
  "risk.market_crash_price_multiplier": () => ({
    body: "Livestock-price multiplier during a crash (0.75 = animals sell at 25% off).",
  }),

  // --- optimization ------------------------------------------------------------
  "optimization.objective": () => ({
    body: "\"balanced\" ranks plans on a blend of NPV, cash and DSCR; \"npv\" maximizes NPV regardless of liquidity; \"liquidity\" prefers plans whose worst cash year is safest.",
  }),
  "optimization.max_candidates": () => ({
    body: "How many candidate plans the search evaluates (each is a full simulation — more candidates, more compute).",
  }),
  "optimization.minimum_dscr": () => ({
    body: "The debt-service coverage a plan must clear in every repaying year to count as feasible (banks usually want ≥1.2).",
  }),
  "optimization.maximum_project_cost": () => ({
    body: "Reject plans whose total project cost exceeds this (₹). Blank = no ceiling.",
  }),
  "optimization.maximum_funding_gap": () => ({
    body: "Reject plans needing more than this additional working capital (₹). Blank = no ceiling.",
  }),
  "optimization.doe_scale_low": (v) => ({
    body: `Smallest multiplier applied to the starting ${v.femaleAdult} count when searching plans (0.75 = a quarter smaller).`,
  }),
  "optimization.doe_scale_high": (v) => ({
    body: `Largest multiplier applied to the starting ${v.femaleAdult} count (1.25 = a quarter larger).`,
  }),
  "optimization.doe_scale_steps": () => ({
    body: `How many herd sizes to try between the low and high multipliers (3 = small/mid/large).`,
  }),
  "optimization.sale_age_radius_months": () => ({
    body: "How many months either side of your sale age the search also tries (2 = tries sale-age −2, −1, …, +2).",
  }),
  "optimization.retention_step": () => ({
    body: "Granularity of the female-retention values the search tries (0.25 = tries retention in steps of a quarter).",
  }),
  "optimization.loan_fraction_step": () => ({
    body: "Granularity of the loan-share values the search tries (0.15 = tries 60%, 75%, 85%... within limits).",
  }),
};

/** Help for the low/high/enabled subfields inside one risk variable. */
const RISK_SUBFIELD_HELP: Record<string, string> = {
  enabled: "Turn this uncertainty on/off in the Monte Carlo draws. Off = the parameter stays fixed at its base value in every risk run.",
  low: "The lowest multiplier drawn — 0.8 means the parameter can fall to 80% of its base value in a bad draw. Must bracket 1.0.",
  high: "The highest multiplier drawn — 1.2 means the parameter can rise to 120% of its base value. Must bracket 1.0.",
};

/** Names shown for each risk variable's parent row. */
const RISK_VARIABLE_LABELS: Record<string, (v: FarmVocabulary) => string> = {
  meat_price: () => "Meat price",
  feed_price: () => "Feed price",
  adult_mortality: (v) => `Adult ${v.femaleAdult} mortality`,
  kid_mortality: () => `Kid mortality`,
  litter_size: (v) => `Litter size (${v.youngPlural} per birth)`,
  conception_rate: () => "Conception rate",
  fodder_yield: () => "Fodder yield",
  operating_cost: () => "Operating cost",
};

/**
 * Help for one field. `path` is "section.key" (or "section.key.subkey" for a
 * risk variable subfield). Returns null when nothing is known — the editor
 * then shows only the derived unit/range facts.
 */
export function simulationFieldHelp(
  path: string,
  v: FarmVocabulary,
): { label: string; help: FieldHelp } | null {
  const [section, key, subKey] = path.split(".");
  if (subKey !== undefined && section === "risk") {
    const variableLabel = RISK_VARIABLE_LABELS[key]?.(v) ?? key.replace(/_/g, " ");
    const body = RISK_SUBFIELD_HELP[subKey];
    if (!body) return null;
    return {
      label: `${variableLabel} — ${subKey}`,
      help: { body },
    };
  }
  if (section === "risk" && key !== undefined) {
    const variableLabel = RISK_VARIABLE_LABELS[key]?.(v) ?? key.replace(/_/g, " ");
    return {
      label: variableLabel,
      help: {
        body: `Monte Carlo uncertainty on the ${variableLabel.toLowerCase()}: each risk run draws a random multiplier between the low and high values below (centred on the current base value), so the NPV distribution reflects how this parameter's luck moves the whole farm. Toggle it off to hold the parameter fixed.`,
      },
    };
  }
  const factory = FIELD_HELP[path];
  if (!factory) return null;
  return {
    label: speciesAwareLabel(
      key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
    ),
    help: factory(v),
  };
}

/**
 * Every path FIELD_HELP covers — lets tests pin that no assumption field lost
 * its explanation when the backend payload grows.
 */
export const SIMULATION_HELP_PATHS: readonly string[] = Object.keys(FIELD_HELP);
