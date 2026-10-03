import type { CalibrationEvidence, MetricExplanation, ReportSection } from "@/api/generated/models";
import type { Language, MessageKey, TFn } from "@/lib/i18n";

const metricKeys = [
  "project_cost", "loan_amount", "subsidy_amount", "equity", "npv", "mirr", "irr", "bcr",
  "avg_dscr", "min_dscr", "peak_capacity_head", "terminal_value", "tax_total",
  "accounting_profit_total", "minimum_cash_balance", "operating_margin", "payback_month",
  "break_even_meat_price_per_kg",
] as const;
type MetricKey = typeof metricKeys[number];

function isMetricKey(key: string): key is MetricKey {
  return metricKeys.some((candidate) => candidate === key);
}

/** Preserve quantitative context not represented in the structured figures
 * (for example the final debt balloon or a ledger denominator). Never return
 * a server-written English paragraph as Telugu fallback text. */
function quantitativeContext(text: string, t: TFn): string {
  const quantities = [...new Set(text.match(/-?₹?\d[\d,]*(?:\.\d+)?(?:%|F\+\d+M)?(?:\s+(?:lakh|crore))?(?:\/kg)?/g) ?? [])]
    .map((value) => value.replace(/lakh/g, t("simulation.evidence.unit.lakh"))
      .replace(/crore/g, t("simulation.evidence.unit.crore"))
      .replace(/\/kg/g, `/${t("simulation.evidence.unit.kg")}`));
  const provenance = [...new Set(text.match(/https?:\/\/[^\s)]+/g) ?? [])];
  return [quantities.length ? t("simulation.evidence.quantities", { values: quantities.join(" · ") }) : "",
    provenance.length ? t("simulation.evidence.provenance", { sources: provenance.join(" · ") }) : ""]
    .filter(Boolean).join(" ");
}

/** The caller keeps the original entry's structured figures and formats them
 * with localized field labels. English retains the complete server narrative. */
export function metricExplanationText(entry: MetricExplanation, t: TFn, language: Language): {
  title: string; explanation: string;
} {
  if (language === "en") return { title: entry.title, explanation: entry.explanation };
  if (!isMetricKey(entry.key)) return {
    title: t("simulation.evidence.metricUnknownTitle"),
    explanation: [t("simulation.evidence.metricUnknownBasis"), quantitativeContext(entry.explanation, t)].filter(Boolean).join(" "),
  };
  const titleKey: MessageKey = `simulation.evidence.metric.${entry.key}.title`;
  const basisKey: MessageKey = `simulation.evidence.metric.${entry.key}.basis`;
  const figures = entry.figures ?? {};
  let context = "";
  if (entry.key === "npv" && typeof figures.npv === "number") context = t(figures.npv > 0
    ? "simulation.evidence.npvPositive" : figures.npv < 0 ? "simulation.evidence.npvNegative" : "simulation.evidence.npvZero");
  if (["mirr", "irr", "bcr", "avg_dscr", "min_dscr", "operating_margin"].includes(entry.key) && figures[entry.key] == null)
    context = t(entry.key === "avg_dscr" || entry.key === "min_dscr"
      ? "simulation.evidence.noRepaymentYears" : "simulation.evidence.undefinedMetric");
  if (entry.key === "loan_amount" && /balloon repayment/.test(entry.explanation)) context = t("simulation.evidence.loanBalloon");
  if (entry.key === "payback_month") context = t(figures.payback_month == null
    ? "simulation.evidence.noPayback" : figures.terminal_driven === 1
      ? "simulation.evidence.terminalPayback" : "simulation.evidence.operatingPayback");
  if (entry.key === "break_even_meat_price_per_kg" && figures.break_even_meat_price_per_kg == null)
    context = t(/not computed/.test(entry.explanation)
      ? "simulation.evidence.breakEvenNotComputed" : "simulation.evidence.breakEvenCeiling");
  if (entry.key === "terminal_value" && /disabled/.test(entry.explanation)) context = t("simulation.evidence.noTerminalRecovery");
  return { title: t(titleKey), explanation: [t(basisKey), context, quantitativeContext(entry.explanation, t)].filter(Boolean).join(" ") };
}

const sourceKeys: Readonly<Record<string, MessageKey>> = {
  animals: "simulation.evidence.source.animals",
  weight_records: "simulation.evidence.source.weight_records",
  breeding_records: "simulation.evidence.source.breeding_records",
  kidding_records: "simulation.evidence.source.kidding_records",
  kid_entries: "simulation.evidence.source.kid_entries",
  transactions: "simulation.evidence.source.transactions",
  feed_inventory: "simulation.evidence.source.feed_inventory",
};

const methodKeys: Readonly<Record<string, MessageKey>> = {
  "Exact ACTIVE-animal cohort count on the reference date": "simulation.evidence.method.activeCohort",
  "Median recorded weight per age-month, isotonic-fitted; unobserved ages interpolated between fitted points and the preset shape rescaled outside them": "simulation.evidence.method.growthCurve",
  "Median latest adult weight per animal": "simulation.evidence.method.adultWeight",
  "Pregnant-or-aborted services divided by assessed services": "simulation.evidence.method.conception",
  "Mean total kids recorded per kidding": "simulation.evidence.method.litter",
  "Median breeding-to-kidding interval rounded to model months": "simulation.evidence.method.gestation",
  "Median recorded live-born kid birth weight": "simulation.evidence.method.birthWeight",
  "Stillborn kid entries divided by all kid entries, capped at the model ceiling": "simulation.evidence.method.stillbirth",
  "Female live-born entries divided by all live-born entries": "simulation.evidence.method.femaleRatio",
  "Dependent-kid deaths as the observed whole-phase pre-weaning fraction, counting only kids born early enough to have completed it and deaths reported within that three-month window": "simulation.evidence.method.preWeaningDeaths",
  "Deaths per animal-month at risk, converted to the whole three-month post-weaning phase probability": "simulation.evidence.method.postWeaningDeaths",
  "Deaths per animal-month at risk in the class, converted to an annual rate": "simulation.evidence.method.annualDeaths",
  "Median recorded per-head purchase price": "simulation.evidence.method.purchasePrice",
  "Median sale amount divided by latest pre-sale recorded live weight (Bakrid-month observations deflated to the plain market level when festival pricing will re-apply the premium)": "simulation.evidence.method.salePrice",
  "Calendar-month median live-weight price divided by overall median (festival-deflated, renormalised to mean 1.0)": "simulation.evidence.method.seasonalPrice",
  "Median cull sale amount divided by latest pre-sale live weight": "simulation.evidence.method.cullPrice",
  "Quantity-weighted structured feed purchase unit price": "simulation.evidence.method.feedPrice",
  "Raised to the heaviest calibrated yearling weight; the breed preset's adult weight was below the growth this farm actually records": "simulation.evidence.method.adultWeightFloor",
  "Age zero set from median recorded live-born birth weight, later ages raised only where the curve would otherwise decrease": "simulation.evidence.method.birthCurve",
};

function calibrationMethod(method: string, t: TFn): string {
  const suffix = "; age zero set from median recorded birth weight";
  const base = method.endsWith(suffix) ? method.slice(0, -suffix.length) : method;
  const key = methodKeys[base];
  if (key) return [t(key), method.endsWith(suffix) ? t("simulation.evidence.method.birthCurveAdjustment") : ""].filter(Boolean).join(" ");
  const labour = /^Total labour expense divided by the (\d+) month\(s\) of ledger history, then split across the ([\d.]+) attendant unit\(s\) implied by (\d+) adult female\(s\) \(the engine's labour basis, matching one worker per ~([\d.]+) does with progeny\)$/.exec(method);
  if (labour) return t("simulation.evidence.method.labour", { months: labour[1], units: labour[2], females: labour[3], threshold: labour[4] });
  const noLabour = /^Labour expense of ([\d.]+) was NOT spread onto a per-attendant wage: with (\d+) adult female\(s\) the engine's labour basis implies 0 paid attendant units, so the configured wage was left unchanged\.$/.exec(method);
  if (noLabour) return t("simulation.evidence.method.noLabour", { expense: noLabour[1], females: noLabour[2] });
  const vet = /^Vet and medicine spend divided by the (\d+) month\(s\) of ledger history, annualized and divided by active head$/.exec(method);
  if (vet) return t("simulation.evidence.method.vet", { months: vet[1] });
  const overhead = /^Total other operating expense divided by the (\d+) month\(s\) of ledger history$/.exec(method);
  if (overhead) return t("simulation.evidence.method.overhead", { months: overhead[1] });
  return [t("simulation.evidence.method.unknown"), quantitativeContext(method, t)].filter(Boolean).join(" ");
}

/** Raw source identifiers remain alongside their Telugu labels for traceable
 * provenance. Sample sizes, values and evidence dates stay in the caller. */
export function calibrationEvidenceText(item: CalibrationEvidence, t: TFn, language: Language): {
  source: string; method: string;
} {
  if (language === "en") return { source: item.source, method: item.method };
  const localizedSources = item.source.split("/").map((source) => sourceKeys[source]
    ? t(sourceKeys[source]) : t("simulation.evidence.source.unknown"));
  return { source: `${localizedSources.join(" / ")} (${item.source})`, method: calibrationMethod(item.method, t) };
}

/** Calibration limits are part of the evidence, so their warnings follow the
 * same language boundary as the calculation methods. */
export function calibrationWarningText(warning: string, t: TFn, language: Language): string {
  if (language === "en") return warning;
  if (warning === "No active animals were found; herd counts were calibrated to zero.") return t("simulation.evidence.warning.noAnimals");
  const history = /^(Animal-history|Weight|Reproduction|Kidding|Cost) calibration used the ([\d,]+) most recent (?:rows|records|services|kid records|ledger rows)(.*)\.$/.exec(warning);
  if (history) {
    const date = /\d{4}-\d{2}-\d{2}/.exec(history[3])?.[0];
    const groupKey: MessageKey = history[1] === "Animal-history" ? "simulation.evidence.group.herd"
      : history[1] === "Weight" ? "simulation.evidence.group.growth"
        : history[1] === "Cost" ? "simulation.evidence.group.costs" : "simulation.evidence.group.reproduction";
    return [t("simulation.evidence.warning.historyLimit", { count: history[2], group: t(groupKey) }),
      history[1] === "Animal-history" ? t("simulation.evidence.warning.herdExact") : "",
      date ? t("simulation.evidence.warning.historyStart", { date }) : ""].filter(Boolean).join(" ");
  }
  const estimated = /^(\d+) sale-price observations used an estimated exit weight from a routine weighing within (\d+) days before sale\.$/.exec(warning);
  if (estimated) return t("simulation.evidence.warning.estimatedSaleWeight", { count: estimated[1], days: estimated[2] });
  const excluded = /^(\d+) sale-price observations were excluded: no recorded sale weight or routine weighing within (\d+) days\.$/.exec(warning);
  if (excluded) return t("simulation.evidence.warning.excludedSaleWeight", { count: excluded[1], days: excluded[2] });
  const months = /^Recurring costs were averaged over the (\d+) month\(s\) of ledger history that exist, not the (\d+) month\(s\) requested\.$/.exec(warning);
  if (months) return t("simulation.evidence.warning.ledgerMonths", { actual: months[1], requested: months[2] });
  const low = /^(\d+) calibrated input\(s\) have low confidence; review them before lending or investment decisions\.$/.exec(warning);
  if (low) return t("simulation.evidence.warning.lowConfidence", { count: low[1] });
  const stillbirth = /^Observed stillbirth rate (\d+)% exceeds the model ceiling of 50%; it was capped at 50%\.$/.exec(warning);
  if (stillbirth) return t("simulation.evidence.warning.stillbirthCeiling", { rate: stillbirth[1] });
  const missing = /^No sufficient farm evidence for: (.+); preset values remain\.$/.exec(warning);
  if (missing) {
    const groups: Record<string, MessageKey> = {
      herd: "simulation.evidence.group.herd", growth: "simulation.evidence.group.growth",
      reproduction: "simulation.evidence.group.reproduction", mortality: "simulation.evidence.group.mortality",
      sales: "simulation.evidence.group.sales", feed: "simulation.evidence.group.feed", costs: "simulation.evidence.group.costs",
    };
    return t("simulation.evidence.warning.missingEvidence", { groups: missing[1].split(", ")
      .map((group) => groups[group] ? t(groups[group]) : t("simulation.evidence.source.unknown")).join(" / ") });
  }
  return [t("simulation.evidence.warning.unknown"), quantitativeContext(warning, t)].filter(Boolean).join(" ");
}

type NarrativeKey = { [K in MessageKey]: K extends `simulation.narrative.${infer S}` ? S : never }[MessageKey];
function narrative(t: TFn, key: NarrativeKey, vars?: Record<string, string | number>): string {
  return t(`simulation.narrative.${key}`, vars);
}
function narrativeValue(value: string, t: TFn): string {
  return value.replace(/lakh/g, t("simulation.evidence.unit.lakh"))
    .replace(/crore/g, t("simulation.evidence.unit.crore"));
}
function narrativeTime(value: string, t: TFn): string | null {
  if (value === "project start (month 0)") return narrative(t, "timeZero");
  const match = /^month (\d+) \(year (\d+)\)$/.exec(value);
  return match ? narrative(t, "time", { month: match[1], year: match[2] }) : null;
}
const narrativeTitles: Readonly<Record<string, NarrativeKey>> = {
  overview: "title.overview", herd_trajectory: "title.herd_trajectory", revenue_mix: "title.revenue_mix",
  cost_mix: "title.cost_mix", viability_verdict: "title.viability_verdict", risks: "title.risks", optimization: "title.optimization",
};
const narrativeLabels: Readonly<Record<string, NarrativeKey>> = {
  "meat sales": "label.meat_sales", "cull sales": "label.cull_sales", "surplus milk": "label.surplus_milk",
  manure: "label.manure", feed: "label.feed", labour: "label.labour", "stock purchases": "label.stock_purchases",
  selling: "label.selling", vet: "label.vet", insurance: "label.insurance", overheads: "label.overheads",
};
const narrativeParameters: Readonly<Record<string, NarrativeKey>> = {
  "meat price": "parameter.meat_price", "milk price": "parameter.milk_price", "feed prices": "parameter.feed_prices",
  "kid pre weaning mortality": "parameter.kid_pre_weaning_mortality", "litter size": "parameter.litter_size",
  "conception rate": "parameter.conception_rate", "sale age months": "parameter.sale_age_months",
  "labour cost": "parameter.labour_cost", "interest rate": "parameter.interest_rate",
};
const narrativeStatuses: Readonly<Record<string, NarrativeKey>> = {
  unique: "status.unique", multiple_roots: "status.multiple_roots", no_root: "status.no_root", indeterminate: "status.indeterminate",
};
const narrativeVerdicts: Readonly<Record<string, NarrativeKey>> = {
  VIABLE: "verdict.VIABLE", "VIABLE WITH CAUTION": "verdict.VIABLE WITH CAUTION", "NOT VIABLE": "verdict.NOT VIABLE",
};
const narrativeObjectives: Readonly<Record<string, NarrativeKey>> = {
  balanced: "objective.balanced", npv: "objective.npv", liquidity: "objective.liquidity",
};

/** Parse ranked entries rather than rebuilding them from figures: the server's
 * sorted order, rounded currencies and percentage shares are all meaningful. */
function narrativeRanked(value: string, t: TFn): string | null {
  const entries = [...value.matchAll(/(meat sales|cull sales|surplus milk|manure|feed|labour|stock purchases|selling|vet|insurance|overheads) (-?₹[\d,.]+(?: (?:lakh|crore))?) \(([^)]+)\)/g)];
  if (!entries.length) return null;
  const remainder = value.replace(/(meat sales|cull sales|surplus milk|manure|feed|labour|stock purchases|selling|vet|insurance|overheads) (-?₹[\d,.]+(?: (?:lakh|crore))?) \(([^)]+)\)/g, "").replace(/, | and /g, "");
  if (remainder) return null;
  return entries.map((entry) => `${narrative(t, narrativeLabels[entry[1]])} ${narrativeValue(entry[2], t)} (${entry[3]})`).join(" · ");
}

function narrativeProblems(value: string, t: TFn): string | null {
  const fixed: Readonly<Record<string, NarrativeKey>> = {
    "the NPV is negative": "problem.npvNegative",
    "the NPV is exactly zero — the project only just clears the discount rate, with no margin": "problem.npvZero",
    "the benefit-cost ratio is below 1.0": "problem.bcr",
    "the IRR is below your discount rate": "problem.irrLow",
    "the weakest debt year has a non-positive DSCR (no operating surplus to pay the instalment from)": "problem.dscrNonpositive",
    "the weakest debt year has a DSCR below 1.0": "problem.dscrLow",
    "the equity is never paid back inside the horizon": "problem.noPayback",
    "the funded working-capital reserve becomes negative and additional liquidity is needed": "problem.liquidity",
  };
  const parts = value.split(/; (?!use NPV and MIRR)/).map((part) => {
    if (fixed[part]) return narrative(t, fixed[part]);
    const irr = /^a unique IRR is not established \(assessment: (\w+)\); use NPV and MIRR$/.exec(part);
    if (irr && narrativeStatuses[irr[1]]) return narrative(t, "problem.irrMissing", { status: narrative(t, narrativeStatuses[irr[1]]) });
    const capacity = /^the projected peak herd exceeds funded housing and equipment capacity by ([\d.]+) head$/.exec(part);
    return capacity ? narrative(t, "problem.capacity", { head: capacity[1] }) : null;
  });
  return parts.every((part) => part !== null) ? parts.join("; ") : null;
}

/** Current backend warning templates from simulation/engine.py. User-supplied
 * source provenance is retained as a literal identifier, not rewritten. */
function narrativeWarning(value: string, t: TFn): string | null {
  const fixed: Readonly<Record<string, NarrativeKey>> = {
    "NLM estimates are conditional policy calculations, not applicant approval. Only explicitly supplied approved installments are booked, on their scheduled months; arrange up-front/bridge funding until those receipts arrive. Eligible costs exclude working capital, personal vehicles and land purchase/rent/lease.": "nlm",
    "NLM unit is outside the exact published bands; no subsidy is estimated.": "nlmUnsupported",
    "Ordinary IRR uniqueness is unproven in this solver domain; use NPV and MIRR.": "irrUnproven",
    "Embedded future festival dates are projections requiring local confirmation; moon sighting can change the sale month near a month boundary. 2039 has both January and December occurrences.": "festivalProjected",
  };
  if (fixed[value]) return narrative(t, fixed[value]);
  const coverage = /^Festival calendar covers through (\d+); months beyond that carry no Bakrid uplift\.$/.exec(value);
  if (coverage) return narrative(t, "festivalCoverage", { year: coverage[1] });
  const source = /^Festival dates are explicit user overrides; source: (.+)$/.exec(value);
  if (source) return narrative(t, "festivalOverrides", { source: source[1] === "not supplied / not independently verified" ? narrative(t, "unverifiedSource") : source[1] });
  return null;
}

function narrativeParagraph(value: string, t: TFn): string | null {
  let match: RegExpExecArray | null;
  match = /^This projection runs (\d+) months \(([\d.]+) years\) from (\d{4}-\d{2})\. You start with ([\d.]+) does? and ([\d.]+) bucks?(?: plus ([\d.]+) kids, ([\d.]+) weaners and ([\d.]+) growers)? — ([\d.]+) head on the ground in month 1\.(?: You have scheduled (\d+) herd event\(s\) along the way — (.+)\.)?$/.exec(value);
  if (match) {
    let events = "";
    if (match[10]) {
      const actions = match[11].split(" and ").map((action) => {
        const item = /^(buy|sell) ([\d,.]+) head$/.exec(action);
        return item ? narrative(t, item[1] === "buy" ? "buy" : "sell", { head: item[2] }) : null;
      });
      if (actions.some((action) => action === null)) return null;
      events = narrative(t, "events", { count: match[10], actions: actions.join(" · ") });
    }
    return narrative(t, "overview", { months: match[1], years: match[2], start: match[3], does: match[4], bucks: match[5],
      young: match[6] ? narrative(t, "young", { kids: match[6], weaners: match[7], growers: match[8] }) : "", head: match[9], events });
  }
  match = /^The project needs (.+?) in total: (.+?) from the bank and (.+?) from your own pocket(?:; declared approved NLM receipts of (.+?) arrive later\.|, with (.+?) assumed up-front subsidy\.)$/.exec(value);
  if (match) return narrative(t, "funding", { cost: narrativeValue(match[1], t), loan: narrativeValue(match[2], t), equity: narrativeValue(match[3], t),
    subsidy: narrative(t, match[4] ? "subsidyLater" : "subsidyUpfront", { amount: narrativeValue(match[4] ?? match[5], t) }) });
  match = /^The herd grows from (\d+) head in month 1 to a peak of (\d+) in (month \d+ \(year \d+\)), ending at (\d+) head \((\d+) breeding does and (\d+) bucks\)\.$/.exec(value);
  if (match) return narrative(t, "herd", { start: match[1], peak: match[2], when: narrativeTime(match[3], t) ?? "", end: match[4], does: match[5], bucks: match[6] });
  match = /^Over the ([\d.]+) years the farm produces (\d+) kids, loses (\d+) animals to mortality, sells (\d+) for meat and disposes of (\d+) as culls\.$/.exec(value);
  if (match) return narrative(t, "production", { years: match[1], births: match[2], deaths: match[3], sold: match[4], culled: match[5] });
  match = /^(.*?) First-time does \(parity 1\) run lighter — about ([\d.]+) on average, mostly singles — so a maiden crop of singles is normal, not a problem\.$/.exec(value);
  if (match) {
    const high = /^Expect multiples at kidding: this breed twins in about 35-40% of kiddings and triplets run 5-13%, so the average mature litter is ~([\d.]+) kids\.$/.exec(match[1]);
    const mid = /^Expect the occasional twin at kidding: this herd's average mature litter is ~([\d.]+) kids — transitional between mostly singles and routine multiples\.$/.exec(match[1]);
    const low = /^Kids are mostly singles at kidding: the average mature litter is only ~([\d.]+)\.$/.exec(match[1]);
    const litter = high ?? mid ?? low;
    if (!litter) return null;
    return narrative(t, high ? "litterHigh" : mid ? "litterMid" : "litterLow", { mature: litter[1] }) + narrative(t, "maiden", { maiden: match[2] });
  }
  match = /^Total revenue over ([\d.]+) years is (.+?), largest source first: (.+)\.$/.exec(value);
  if (match) {
    const items = narrativeRanked(match[3], t);
    return items === null ? null : narrative(t, "revenue", { years: match[1], amount: narrativeValue(match[2], t), items });
  }
  match = /^Bakrid pricing \(\+([\d.]+)% on the base rate\) applies in months ([\d, ]+), but the plan sells no animals in those months — timing sales into the festival is the single largest pricing lever available\.$/.exec(value);
  if (match) return narrative(t, "festivalNoSales", { uplift: match[1], months: match[2] });
  match = /^Bakrid pricing \(\+([\d.]+)% on the base rate\) applies in months ([\d, ]+); (\d+) head sell inside them\.$/.exec(value);
  if (match) return narrative(t, "festivalSales", { uplift: match[1], months: match[2], head: match[3] });
  match = /^Operating costs total (.+?) over ([\d.]+) years, largest first: (.+)\.$/.exec(value);
  if (match) {
    const items = narrativeRanked(match[3], t);
    return items === null ? null : narrative(t, "costs", { amount: narrativeValue(match[1], t), years: match[2], items });
  }
  match = /^Buying breeding stock cost (.+?) in cash; it is capitalized and depreciated over (\d+) months rather than expensed, so EBITDA above excludes it\.$/.exec(value);
  if (match) return narrative(t, "breedingCapex", { amount: narrativeValue(match[1], t), months: match[2] });
  match = /^No hired labour is charged: the plan assumes family labour\. At the configured (₹[\d,]+)\/month wage the same attendance would cost about (.+?) over the projection — profit is earned on unpaid family work, not the market\.$/.exec(value);
  if (match) return narrative(t, "familyLabour", { wage: match[1], amount: narrativeValue(match[2], t) });
  match = /^Growing green fodder needs about ([\d.]+) acre\(s\) on average\.$/.exec(value);
  if (match) return narrative(t, "fodder", { acres: match[1] });
  match = /^Growing green fodder needs about ([\d.]+) acre\(s\) on average; your cultivated area falls short in (\d+) month\(s\)\. This is a physical and financial shortfall: ([\d,]+) kg as-fed is bought at the configured market price, while on-farm supply is costed separately\.$/.exec(value);
  if (match) return narrative(t, "fodderDeficit", { acres: match[1], months: match[2], kg: match[3] });
  match = /^Water demand runs about ([\d,]+) litres\/day on average, peaking near ([\d,]+) litres\/day — a lactating doe needs 10-15 L\/day in the Deccan summer, so plan storage and supply for the peak, not the average\.$/.exec(value);
  if (match) return narrative(t, "water", { average: match[1], peak: match[2] });
  match = /^At these assumptions the project is (VIABLE|VIABLE WITH CAUTION|NOT VIABLE)\. Over ([\d.]+) years it earns (.+?) against (.+?) of operating costs — an operating surplus \(EBITDA\) of (.+?)\.$/.exec(value);
  if (match) return narrative(t, "viability", { verdict: narrative(t, narrativeVerdicts[match[1]]), years: match[2], revenue: narrativeValue(match[3], t), costs: narrativeValue(match[4], t), ebitda: narrativeValue(match[5], t) });
  match = /^Watch out: (.+)\.$/.exec(value);
  if (match) {
    const problems = narrativeProblems(match[1], t);
    return problems === null ? null : narrative(t, "watch", { problems });
  }
  match = /^All standard checks pass: NPV (.+?) is positive, BCR is ([\d.]+)(?:, IRR is ([\d.]+%))?, and the equity is recovered in (.+?)\.$/.exec(value);
  if (match) {
    const when = narrativeTime(match[4], t);
    return when === null ? null : narrative(t, "checks", { npv: narrativeValue(match[1], t), bcr: match[2], irr: match[3] ? narrative(t, "irr", { value: match[3] }) : "", when });
  }
  match = /^Across (\d+) correlated Monte Carlo runs \(varying prices, feed, fodder yield, operating cost, mortality and reproduction(, plus monthly adverse events and year-to-year price swings within each run)?\), the NPV averages (.+?) with a 90% range of (.+?) to (.+?)\. The project loses money in ([\d.]+%) of runs and runs short of operating cash in ([\d.]+%)\.(.+)$/.exec(value);
  if (match) {
    let events: string;
    if (match[8] === " No adverse-event draws are configured, so this spread reflects parameter uncertainty only.") events = narrative(t, "noRiskEvents");
    else {
      const active = /^ Every risk path also draws (.+?) events, so this distribution sits below the headline \(no-disaster\) figures — compare runs against each other, not against the deterministic base case\.$/.exec(match[8]);
      if (!active) return null;
      const keys: Readonly<Record<string, NarrativeKey>> = { "disease events": "event.disease", "drought events": "event.drought", "market-crash events": "event.market-crash" };
      const items = active[1].split("/").map((event) => keys[event] ? narrative(t, keys[event]) : null);
      if (items.some((event) => event === null)) return null;
      events = narrative(t, "riskEvents", { events: items.join(" / ") });
    }
    return narrative(t, "monteCarlo", { runs: match[1], variation: match[2] ? narrative(t, "variation") : "", mean: narrativeValue(match[3], t), low: narrativeValue(match[4], t), high: narrativeValue(match[5], t), loss: match[6], liquidity: match[7], events });
  }
  if (value === "No Monte Carlo run contained a principal-repaying year, so the debt-service coverage breach probability is not measurable for this financing shape — it is reported as unavailable, not as zero.") return narrative(t, "noDscr");
  match = /^These figures carry sampling noise at (\d+) runs: the 5th-percentile NPV is (.+?) with a bootstrap 95% interval of (.+?) to (.+?), and the loss probability of ([\d.]+%) has a standard error of ([\d.]+%)\. More runs narrow both\.$/.exec(value);
  if (match) return narrative(t, "sampling", { runs: match[1], p5: narrativeValue(match[2], t), low: narrativeValue(match[3], t), high: narrativeValue(match[4], t), loss: match[5], error: match[6] });
  match = /^The assumptions that move NPV the most: (.+)\.$/.exec(value);
  if (match) {
    const entries = [...match[1].matchAll(/([a-z ]+) \(([^\n]+?) moves NPV by (-?₹[\d,.]+(?: (?:lakh|crore))?)\)/g)];
    if (!entries.length || match[1].replace(/([a-z ]+) \(([^\n]+?) moves NPV by (-?₹[\d,.]+(?: (?:lakh|crore))?)\)/g, "").replace(/,\s*/g, "")) return null;
    const items = entries.map((entry) => {
      const parameter = narrativeParameters[entry[1].trim()];
      if (!parameter) return null;
      // Backend sale-age labels use signed month(s), unlike price percentages.
      const months = /^([+-]\d+) month\(s\)$/.exec(entry[2]);
      if (!months && !/^[+-][\d.]+%$/.test(entry[2])) return null;
      const change = months ? narrative(t, "monthsDelta", { value: months[1] }) : entry[2];
      return narrative(t, "sensitivityItem", { parameter: narrative(t, parameter), change, amount: narrativeValue(entry[3], t) });
    });
    return items.some((item) => item === null) ? null : narrative(t, "sensitivity", { items: items.join(" · ") });
  }
  if (value === "Run with Monte Carlo and sensitivity enabled to see how robust these results are to price swings, disease and poor breeding years.") return narrative(t, "noRiskAnalysis");
  match = /^None of the (\d+) tested plans met every configured financing, liquidity, capacity and DSCR constraint\. No plan is labelled as recommended; revise the constraints or economics before acting\.$/.exec(value);
  if (match) return narrative(t, "noFeasiblePlan", { count: match[1] });
  match = /^Under the (balanced|npv|liquidity) objective, the highest-ranked feasible plan starts with (\d+) does? and (\d+) bucks?, targets (\d+) breeding does, sells at (\d+) months, retains ([\d.]+%) of eligible females and uses ([\d.]+%) debt\. Its NPV is (.+?) with a minimum DSCR of (-?[\d.]+|N\/A)\.$/.exec(value);
  if (match) return narrative(t, "recommended", { objective: narrative(t, narrativeObjectives[match[1]]), does: match[2], bucks: match[3], target: match[4], age: match[5], retention: match[6], debt: match[7], npv: narrativeValue(match[8], t), dscr: match[9] === "N/A" ? narrative(t, "unavailable") : match[9] });
  return narrativeWarning(value, t);
}

/** Translate the complete current backend report templates, including facts
 * not represented in section.figures. A future unrecognized paragraph is
 * explicitly marked incomplete; numerical extraction is never a substitute
 * for the original paragraph's meaning. English is preserved verbatim. */
export function narrativeSectionText(section: ReportSection, t: TFn, language: Language): {
  title: string; paragraphs: string[]; complete: boolean;
} {
  if (language === "en") return { title: section.title, paragraphs: section.paragraphs, complete: true };
  let complete = Boolean(narrativeTitles[section.key]);
  const paragraphs = section.paragraphs.map((paragraph) => {
    const translated = narrativeParagraph(paragraph, t);
    if (translated !== null) return translated;
    complete = false;
    return [narrative(t, "unknown"), quantitativeContext(paragraph, t)].filter(Boolean).join(" ");
  });
  return { title: narrative(t, narrativeTitles[section.key] ?? "title.unknown"), paragraphs, complete };
}
