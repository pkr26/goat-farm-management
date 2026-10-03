import { describe, expect, it } from "vitest";

import type { ReportSection } from "@/api/generated/models";
import { translate, type TFn } from "@/lib/i18n";
import fixtures from "@/test/fixtures/simulation-narrative.json";
import { narrativeSectionText } from "./simulation-evidence-text";

const te: TFn = (key, vars) => translate("te", key, vars);
const en: TFn = (key, vars) => translate("en", key, vars);
const reports = fixtures.map((fixture) => ({ name: fixture.name, sections: fixture.sections.map(({ key, title, paragraphs }): ReportSection => ({ key, title, paragraphs })) }));
const section = (key: string, ...paragraphs: string[]): ReportSection => ({ key, title: "Exact server heading", paragraphs });
function localized(key: string, paragraph: string): string {
  const translated = narrativeSectionText(section(key, paragraph), te, "te");
  expect(translated.complete, paragraph).toBe(true);
  expect(translated.title).toMatch(/[\u0c00-\u0c7f]/);
  expect(translated.paragraphs[0]).toMatch(/[\u0c00-\u0c7f]/);
  expect(translated.paragraphs[0]).not.toContain("simulation.narrative.");
  return translated.paragraphs[0];
}

describe("complete simulation narrative translations", () => {
  // These reports were produced by the actual Python engine and explain.py,
  // covering current defaults, young stock/events, family labour, and Monte
  // Carlo with/without adverse events. This catches source-template drift.
  for (const fixture of reports) {
    it.each(fixture.sections)(`${fixture.name}: translates the complete $key section and every reported number`, (entry) => {
      const result = narrativeSectionText(entry, te, "te");
      expect(result.complete, JSON.stringify(entry.paragraphs)).toBe(true);
      expect(result.paragraphs).toHaveLength(entry.paragraphs.length);
      for (const [index, original] of entry.paragraphs.entries()) {
        const paragraph = result.paragraphs[index];
        expect(paragraph).toMatch(/[\u0c00-\u0c7f]/);
        expect(paragraph).not.toContain("simulation.narrative.");
        expect(paragraph).not.toContain("పూర్తి అనువాదం ఇంకా");
        for (const amount of original.match(/-?\d[\d,]*(?:\.\d+)?%?/g) ?? []) {
          expect(paragraph, `missing ${amount}: ${original}`).toContain(amount);
        }
      }
    });
  }

  it("leaves every English title and paragraph unchanged", () => {
    for (const fixture of reports) for (const entry of fixture.sections) {
      expect(narrativeSectionText(entry, en, "en")).toEqual({ title: entry.title, paragraphs: entry.paragraphs, complete: true });
    }
  });

  it.each([
    ["Expect multiples at kidding: this breed twins in about 35-40% of kiddings and triplets run 5-13%, so the average mature litter is ~1.75 kids.", "35-40%", "5-13%", "1.75"],
    ["Expect the occasional twin at kidding: this herd's average mature litter is ~1.45 kids — transitional between mostly singles and routine multiples.", "అప్పుడప్పుడు", "మధ్య ఉంది", "1.45"],
    ["Kids are mostly singles at kidding: the average mature litter is only ~1.20.", "ఒక్క పిల్లే", "కేవలం", "1.20"],
  ])("preserves the mature and first-kidding distinctions: %s", (lead, ...facts) => {
    const text = localized("herd_trajectory", `${lead} First-time does (parity 1) run lighter — about 0.95 on average, mostly singles — so a maiden crop of singles is normal, not a problem.`);
    for (const fact of [...facts, "0.95", "సమస్య కాదు"]) expect(text).toContain(fact);
  });

  it.each(["no_root", "multiple_roots", "indeterminate"])("keeps the %s IRR assessment, liquidity and capacity warnings", (status) => {
    const text = localized("viability_verdict", `Watch out: the NPV is exactly zero — the project only just clears the discount rate, with no margin; the benefit-cost ratio is below 1.0; a unique IRR is not established (assessment: ${status}); use NPV and MIRR; the weakest debt year has a non-positive DSCR (no operating surplus to pay the instalment from); the equity is never paid back inside the horizon; the funded working-capital reserve becomes negative and additional liquidity is needed; the projected peak herd exceeds funded housing and equipment capacity by 12.5 head.`);
    for (const fact of ["ఖచ్చితంగా సున్నా", "మార్జిన్ లేదు", "1.0", "NPV, MIRR", "వాయిదా", "తిరిగి రాదు", "అదనపు నగదు", "12.5"]) expect(text).toContain(fact);
    expect(text).not.toContain("Watch out");
  });

  it("retains successful checks, project-start payback and the optional IRR clause", () => {
    const atStart = localized("viability_verdict", "All standard checks pass: NPV ₹2.50 lakh is positive, BCR is 1.20, and the equity is recovered in project start (month 0).");
    expect(atStart).toContain("₹2.50 లక్షలు");
    expect(atStart).toContain("ప్రాజెక్టు ప్రారంభం (0వ నెల)");
    expect(atStart).not.toContain("IRR");
    const later = localized("viability_verdict", "All standard checks pass: NPV ₹2.50 lakh is positive, BCR is 1.20, IRR is 25.5%, and the equity is recovered in month 14 (year 2).");
    expect(later).toContain("IRR 25.5%");
    expect(later).toContain("14వ నెల (2వ సంవత్సరం)");
  });

  it("retains exact sensitivity deltas rather than assuming every perturbation is 20%", () => {
    const text = localized("risks", "The assumptions that move NPV the most: sale age months (-2 month(s) moves NPV by -₹1.20 lakh), conception rate (+17.6% moves NPV by ₹65,000), milk price (+0% moves NPV by ₹0).");
    for (const fact of ["అమ్మకపు వయసు", "-2 నెలలు", "-₹1.20 లక్షలు", "+17.6%", "₹65,000", "+0%", "₹0"]) expect(text).toContain(fact);
    expect(text).not.toContain("20%");
  });

  it("keeps zero-cost ranks, negative cash amounts and unavailable debt coverage", () => {
    const costs = localized("cost_mix", "Operating costs total ₹0 over 1 years, largest first: feed ₹0 (—), labour ₹0 (—), stock purchases ₹0 (—), selling ₹0 (—), vet ₹0 (—), insurance ₹0 (—) and overheads ₹0 (—).");
    expect(costs).toContain("మేత ₹0 (—)");
    expect(costs.indexOf("మేత")).toBeLessThan(costs.indexOf("కూలి"));
    expect(localized("risks", "No Monte Carlo run contained a principal-repaying year, so the debt-service coverage breach probability is not measurable for this financing shape — it is reported as unavailable, not as zero.")).toContain("సున్నాగా కాదు");
  });

  it("retains subsidy timing, conditional approval and excluded costs", () => {
    const funding = localized("overview", "The project needs ₹1.25 crore in total: ₹50.00 lakh from the bank and ₹75.00 lakh from your own pocket; declared approved NLM receipts of ₹25.00 lakh arrive later.");
    for (const fact of ["₹1.25 కోట్లు", "₹50.00 లక్షలు", "₹75.00 లక్షలు", "₹25.00 లక్షలు", "తరువాతి నెలల్లో"]) expect(funding).toContain(fact);
    const caveat = localized("risks", "NLM estimates are conditional policy calculations, not applicant approval. Only explicitly supplied approved installments are booked, on their scheduled months; arrange up-front/bridge funding until those receipts arrive. Eligible costs exclude working capital, personal vehicles and land purchase/rent/lease.");
    for (const fact of ["ఆమోదం కావు", "ఆమోదిత వాయిదాలను మాత్రమే", "మధ్యంతర నిధులు", "వ్యక్తిగత వాహనాలు", "లీజు"]) expect(caveat).toContain(fact);
    expect(localized("risks", "NLM unit is outside the exact published bands; no subsidy is estimated.")).toContain("సబ్సిడీని అంచనా వేయలేదు");
  });

  it("keeps festival no-sale advice and source/calendar caveats", () => {
    const noSale = localized("revenue_mix", "Bakrid pricing (+30% on the base rate) applies in months 5, 17, but the plan sells no animals in those months — timing sales into the festival is the single largest pricing lever available.");
    for (const fact of ["+30%", "5, 17", "అమ్మకాలు లేవు", "అతిపెద్ద"]) expect(noSale).toContain(fact);
    expect(localized("risks", "Festival calendar covers through 2045; months beyond that carry no Bakrid uplift.")).toContain("2045 వరకు మాత్రమే");
    expect(localized("risks", "Festival dates are explicit user overrides; source: local-announcement-2027")).toContain("local-announcement-2027");
    expect(localized("risks", "Festival dates are explicit user overrides; source: not supplied / not independently verified")).toContain("స్వతంత్రంగా ధృవీకరించలేదు");
    expect(localized("risks", "Ordinary IRR uniqueness is unproven in this solver domain; use NPV and MIRR.")).toContain("నిరూపణ కాలేదు");
  });

  it.each(["balanced", "npv", "liquidity"])("preserves every %s recommendation decision including unavailable DSCR", (objective) => {
    const text = localized("optimization", `Under the ${objective} objective, the highest-ranked feasible plan starts with 1 doe and 1 buck, targets 50 breeding does, sells at 9 months, retains 25.5% of eligible females and uses 60.0% debt. Its NPV is ₹2.50 lakh with a minimum DSCR of N/A.`);
    for (const fact of ["1 ఆడ", "1 మగ", "50", "9", "25.5%", "60.0%", "₹2.50 లక్షలు", "అందుబాటులో లేదు"]) expect(text).toContain(fact);
  });

  it("does not recommend an infeasible plan", () => {
    const text = localized("optimization", "None of the 64 tested plans met every configured financing, liquidity, capacity and DSCR constraint. No plan is labelled as recommended; revise the constraints or economics before acting.");
    for (const fact of ["64", "ఏదీ", "షరతులన్నిటినీ", "సిఫార్సు చేయలేదు", "ముందు"]) expect(text).toContain(fact);
  });

  it("marks future or changed text incomplete without pretending numbers are its full meaning", () => {
    const text = narrativeSectionText(section("risks", "A new unrecognized economic caveat costs ₹3.20 lakh in 2030."), te, "te");
    expect(text.complete).toBe(false);
    expect(text.paragraphs[0]).toContain("పూర్తి అనువాదం ఇంకా అందుబాటులో లేదు");
    expect(text.paragraphs[0]).toContain("అసలు ఆంగ్ల పాఠాన్ని పరిశీలించండి");
    expect(text.paragraphs[0]).toContain("₹3.20 లక్షలు");
    expect(text.paragraphs[0]).not.toContain("A new unrecognized");
    expect(narrativeSectionText(section("new_section", "Unknown text"), te, "te").complete).toBe(false);
  });
});
