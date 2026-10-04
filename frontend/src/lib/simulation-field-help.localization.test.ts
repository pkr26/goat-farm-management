import { describe, expect, it } from "vitest";
import { farmVocabulary } from "@/lib/farm-vocabulary";
import { translate, type TFn } from "@/lib/i18n";
import { SIMULATION_HELP_PATHS, SIMULATION_SECTION_HELP, simulationFieldHelp, simulationSectionHelp } from "@/lib/simulation-field-help";

const te: TFn = (key, vars) => translate("te", key, vars);
const en: TFn = (key, vars) => translate("en", key, vars);
const vocabulary = farmVocabulary;

describe("Simulation help language and domain coverage", () => {
  it("renders Telugu explanations for every documented field and section", () => {
    for (const path of SIMULATION_HELP_PATHS) {
      const body = simulationFieldHelp(path, vocabulary, te)?.help.body;
      expect(body, path).toMatch(/[\u0C00-\u0C7F]/);
      expect(body, path).not.toContain("simulation.help.");
      expect(body, path).not.toMatch(/\{\w+\}/);
      expect(body, path).not.toBe(simulationFieldHelp(path, vocabulary, en)?.help.body);
    }
    for (const section of Object.keys(SIMULATION_SECTION_HELP)) expect(simulationSectionHelp(section, vocabulary, te), section).toMatch(/[\u0C00-\u0C7F]/);
  });
  it("explains the actual phase units and unsupported grant evidence in Telugu", () => {
    expect(simulationFieldHelp("mortality.kid_post_weaning", vocabulary, te)?.help.body).toContain("3–5");
    const nlm = simulationFieldHelp("finance.nlm_subsidy", vocabulary, te)!.help.body;
    expect(nlm).toContain("మంజూరు కాదు"); expect(nlm).toContain("50%");
    expect(nlm).toContain("100"); expect(nlm).toContain("500");
    expect(simulationFieldHelp("risk.monte_carlo_runs", vocabulary, te)?.help.body).toContain("200–500");
    for (const suffix of ["enabled", "low", "high"]) expect(simulationFieldHelp(`risk.meat_price.${suffix}`, vocabulary, te)?.help.body).toMatch(/[\u0C00-\u0C7F]/);
  });
  it("does not invent AI capacity in the localized sire guidance", () => {
    const bucks = simulationFieldHelp("herd.bucks", vocabulary, te)!.help.body;
    const autoPurchase = simulationFieldHelp("herd.auto_purchase_bucks", vocabulary, te)!.help.body;
    expect(`${bucks} ${autoPurchase}`).not.toMatch(/AI|కృత్రిమ గర్భధారణ/i);
    expect(autoPurchase).toContain("సామర్థ్యం సున్నా");
  });
});
