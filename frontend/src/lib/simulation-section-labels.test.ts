import { describe, expect, it } from "vitest";

import type { SimulationAssumptions } from "@/api/generated/models";
import { translate } from "@/lib/i18n";
import { simulationSectionLabel } from "@/lib/simulation-field-help";

// Completeness follows the generated API shape: a supported section added to
// the contract must acquire a localized label before this fixture compiles.
const labels = {
  meta: ["Meta", "ప్రాథమిక వివరాలు"],
  herd: ["Herd", "మంద"],
  reproduction: ["Reproduction", "సంతానోత్పత్తి"],
  mortality: ["Mortality", "మరణాల రేటు"],
  culling: ["Culling", "మంద నుంచి తొలగింపు"],
  growth: ["Growth", "పెరుగుదల"],
  sales: ["Sales", "అమ్మకాలు"],
  feed: ["Feed", "మేత"],
  costs: ["Costs", "ఖర్చులు"],
  finance: ["Finance", "ఆర్థిక వివరాలు"],
  risk: ["Risk", "ప్రమాదం"],
  optimization: ["Optimization", "ఉత్తమ ఎంపికల విశ్లేషణ"],
} satisfies Record<Exclude<keyof SimulationAssumptions, "events">, [string, string]>;

describe("Simulation section labels", () => {
  it.each(["en", "te"] as const)("covers every current assumption section in %s", (language) => {
    const t = (key: Parameters<typeof translate>[1]) => translate(language, key);
    for (const [section, expected] of Object.entries(labels)) {
      expect(simulationSectionLabel(section, t)).toBe(expected[language === "en" ? 0 : 1]);
    }
  });

  it.each(["future_model_section", "events", "constructor", "toString"])("leaves %s available for the editor's generic fallback", (section) => {
    expect(simulationSectionLabel(section, key => translate("te", key))).toBeNull();
  });
});
