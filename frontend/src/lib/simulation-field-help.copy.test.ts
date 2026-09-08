import { describe, expect, it } from "vitest";

import { farmVocabulary } from "@/lib/farm-vocabulary";
import {
  SIMULATION_HELP_PATHS,
  simulationFieldHelp,
  speciesAwareLabel,
} from "@/lib/simulation-field-help";

const goat = farmVocabulary;

describe("speciesAwareLabel", () => {
  it("returns humanized labels byte-identical", () => {
    for (const base of [
      "Buck Doe Ratio",
      "Adult Weight Doe Kg",
      "Adult Weight Buck Kg",
      "Doe Scale Low",
      "Doe Scale High",
      "Doe Scale Steps",
      "Does",
      "Bucks",
      "Female Kids",
      "Kidding Interval",
    ]) {
      expect(speciesAwareLabel(base)).toBe(base);
    }
  });
});

describe("simulationFieldHelp — every documented field key resolves", () => {
  it("covers all 160 assumption fields across all 12 sections", () => {
    expect(SIMULATION_HELP_PATHS).toHaveLength(160);
    for (const path of [
      "meta.horizon_months",
      "herd.does",
      "herd.bucks",
      "herd.foundation_flock_state",
      "reproduction.max_services_before_cull",
      "mortality.kid_pre_weaning",
      "culling.buck_doe_ratio",
      "growth.weight_by_age_months",
      "sales.milk_price_per_kg_fat",
      "feed.fodder_storage_loss_fraction_monthly",
      "costs.capacity_basis",
      "finance.reinvestment_rate_annual",
      "risk.market_crash_price_multiplier",
      "optimization.loan_fraction_step",
    ]) {
      expect(SIMULATION_HELP_PATHS).toContain(path);
    }
  });

  it.each(SIMULATION_HELP_PATHS)("explains %s", (path) => {
    for (const v of [goat]) {
      const result = simulationFieldHelp(path, v);
      expect(result, `${path} (${v.typeLabel})`).not.toBeNull();
      expect(result!.label.trim()).not.toBe("");
      expect(result!.help.body).toMatch(/\S/);
      expect(result!.help.body.length).toBeGreaterThan(20);
    }
  });

  it("returns null for unknown fields and sections", () => {
    expect(simulationFieldHelp("meta.warp_drive", goat)).toBeNull();
    expect(simulationFieldHelp("herd.does_typo", goat)).toBeNull();
    expect(simulationFieldHelp("totally.unknown.path", goat)).toBeNull();
    expect(simulationFieldHelp("risk", goat)).toBeNull();
  });

  it("reserves subfield addressing for the risk section only", () => {
    // "section.key.subkey" is documented as a risk-variable address; the same
    // shape anywhere else is an unknown path, not an enabled/low/high subfield.
    expect(simulationFieldHelp("herd.does.enabled", goat)).toBeNull();
    expect(simulationFieldHelp("meta.horizon_months.low", goat)).toBeNull();
    expect(simulationFieldHelp("culling.buck_doe_ratio.enabled", goat)).toBeNull();
  });
});

describe("simulationFieldHelp — species-aware labels through the editor path", () => {
  it("labels goat fields with the raw goat vocabulary", () => {
    expect(simulationFieldHelp("herd.does", goat)!.label).toBe("Does");
    expect(simulationFieldHelp("herd.bucks", goat)!.label).toBe("Bucks");
    expect(simulationFieldHelp("herd.female_kids", goat)!.label).toBe("Female Kids");
    expect(simulationFieldHelp("culling.buck_rotation_years", goat)!.label).toBe(
      "Buck Rotation Years",
    );
    expect(simulationFieldHelp("culling.buck_doe_ratio", goat)!.label).toBe("Buck Doe Ratio");
  });

});

describe("simulationFieldHelp — species-aware body copy", () => {
  it("writes goat nouns for a goat farm", () => {
    expect(simulationFieldHelp("herd.bucks", goat)!.help.body).toContain(
      "Adult bucks in the starting herd",
    );
    expect(simulationFieldHelp("herd.does", goat)!.help.body).toContain(
      "Adult breeding does in the starting herd",
    );
    expect(simulationFieldHelp("culling.buck_doe_ratio", goat)!.help.body).toContain(
      "Females per buck: one buck can serve this many does",
    );
    expect(simulationFieldHelp("feed.dmi_grower", goat)!.help.body).toContain(
      "Dry-matter intake of a grower",
    );
  });

});

describe("simulationFieldHelp — risk variables", () => {
  it("labels the risk variable parent row", () => {
    expect(simulationFieldHelp("risk.meat_price", goat)!.label).toBe("Meat price");
    expect(simulationFieldHelp("risk.conception_rate", goat)!.label).toBe("Conception rate");
    expect(simulationFieldHelp("risk.adult_mortality", goat)!.label).toBe("Adult doe mortality");
    expect(simulationFieldHelp("risk.kid_mortality", goat)!.label).toBe("Kid mortality");
    expect(simulationFieldHelp("risk.litter_size", goat)!.label).toBe(
      "Litter size (kids per birth)",
    );
  });

  it("describes the Monte Carlo draw with the lower-cased variable name", () => {
    const goatBody = simulationFieldHelp("risk.adult_mortality", goat)!.help.body;
    expect(goatBody).toContain("uncertainty on the adult doe mortality:");
    expect(goatBody).toContain("between the low and high values below");
    expect(simulationFieldHelp("risk.fodder_yield", goat)!.help.body).toContain(
      "uncertainty on the fodder yield:",
    );
  });

  it("humanizes risk variables the table does not know", () => {
    const unknown = simulationFieldHelp("risk.mastitis_somatic_score", goat);
    expect(unknown!.label).toBe("mastitis somatic score");
    expect(unknown!.help.body).toContain("uncertainty on the mastitis somatic score:");
    const unknownSub = simulationFieldHelp("risk.mastitis_somatic_score.enabled", goat);
    expect(unknownSub!.label).toBe("mastitis somatic score — enabled");
    expect(unknownSub!.help.body).toBe(
      "Turn this uncertainty on/off in the Monte Carlo draws. Off = the parameter stays fixed at its base value in every risk run.",
    );
  });

  it("explains each low/high/enabled subfield", () => {
    expect(simulationFieldHelp("risk.seed.enabled", goat)!.label).toBe("seed — enabled");
    expect(simulationFieldHelp("risk.seed.enabled", goat)!.help.body).toBe(
      "Turn this uncertainty on/off in the Monte Carlo draws. Off = the parameter stays fixed at its base value in every risk run.",
    );
    expect(simulationFieldHelp("risk.seed.low", goat)!.label).toBe("seed — low");
    expect(simulationFieldHelp("risk.seed.low", goat)!.help.body).toBe(
      "The lowest multiplier drawn — 0.8 means the parameter can fall to 80% of its base value in a bad draw. Must bracket 1.0.",
    );
    expect(simulationFieldHelp("risk.seed.high", goat)!.label).toBe("seed — high");
    expect(simulationFieldHelp("risk.seed.high", goat)!.help.body).toBe(
      "The highest multiplier drawn — 1.2 means the parameter can rise to 120% of its base value. Must bracket 1.0.",
    );
    expect(simulationFieldHelp("risk.feed_price.high", goat)!.label).toBe("Feed price — high");
  });

  it("returns null for unknown risk subfields and bare section names", () => {
    expect(simulationFieldHelp("risk.seed.bogus", goat)).toBeNull();
    expect(simulationFieldHelp("risk.mastitis_somatic_score.bogus", goat)).toBeNull();
  });
});
