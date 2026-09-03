import { describe, expect, it } from "vitest";

import { farmVocabulary } from "@/lib/farm-vocabulary";
import {
  SIMULATION_HELP_PATHS,
  simulationFieldHelp,
  speciesAwareLabel,
} from "@/lib/simulation-field-help";

const goat = farmVocabulary("GOAT");
const buffalo = farmVocabulary("BUFFALO_DAIRY");

describe("speciesAwareLabel — goat farms are the identity", () => {
  it("returns goat labels byte-identical, including the ones a dairy overrides", () => {
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
      expect(speciesAwareLabel(base, goat)).toBe(base);
    }
  });
});

describe("speciesAwareLabel — buffalo dairy overrides", () => {
  it("maps every explicit override to its dairy wording", () => {
    expect(speciesAwareLabel("Buck Doe Ratio", buffalo)).toBe("Milking buffalo per bull");
    expect(speciesAwareLabel("Adult Weight Doe Kg", buffalo)).toBe("Adult female weight (kg)");
    expect(speciesAwareLabel("Adult Weight Buck Kg", buffalo)).toBe("Adult male weight (kg)");
    expect(speciesAwareLabel("Doe Scale Low", buffalo)).toBe("Herd scale low");
    expect(speciesAwareLabel("Doe Scale High", buffalo)).toBe("Herd scale high");
    expect(speciesAwareLabel("Doe Scale Steps", buffalo)).toBe("Herd scale steps");
  });

  it("falls back to word-level substitution when no override applies", () => {
    expect(speciesAwareLabel("Does", buffalo)).toBe("Milking Buffalo");
    expect(speciesAwareLabel("Doe", buffalo)).toBe("Milking Buffalo");
    expect(speciesAwareLabel("Bucks", buffalo)).toBe("Bulls");
    expect(speciesAwareLabel("Buck", buffalo)).toBe("Bull");
    expect(speciesAwareLabel("Kids", buffalo)).toBe("Calves");
    expect(speciesAwareLabel("Kid", buffalo)).toBe("Calf");
    expect(speciesAwareLabel("Kidding Interval", buffalo)).toBe("Calving Interval");
    // Several substitutions in one label.
    expect(speciesAwareLabel("Buck Doe Ratio Report", buffalo)).toBe(
      "Bull Milking Buffalo Ratio Report",
    );
    // Partial words must not be touched (\b guards).
    expect(speciesAwareLabel("Bucky Feedstock", buffalo)).toBe("Bucky Feedstock");
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

  it.each(SIMULATION_HELP_PATHS)("explains %s for both species", (path) => {
    for (const v of [goat, buffalo]) {
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
    expect(simulationFieldHelp("unknown.thing", buffalo)).toBeNull();
    expect(simulationFieldHelp("totally.unknown.path", goat)).toBeNull();
    expect(simulationFieldHelp("risk", goat)).toBeNull();
  });

  it("reserves subfield addressing for the risk section only", () => {
    // "section.key.subkey" is documented as a risk-variable address; the same
    // shape anywhere else is an unknown path, not an enabled/low/high subfield.
    expect(simulationFieldHelp("herd.does.enabled", goat)).toBeNull();
    expect(simulationFieldHelp("meta.horizon_months.low", goat)).toBeNull();
    expect(simulationFieldHelp("feed.dmi_doe_maintenance.high", buffalo)).toBeNull();
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

  it("relabells buffalo fields with the dairy vocabulary", () => {
    expect(simulationFieldHelp("herd.does", buffalo)!.label).toBe("Milking Buffalo");
    expect(simulationFieldHelp("herd.bucks", buffalo)!.label).toBe("Bulls");
    expect(simulationFieldHelp("herd.female_kids", buffalo)!.label).toBe("Female Calves");
    expect(simulationFieldHelp("herd.male_kids", buffalo)!.label).toBe("Male Calves");
    expect(simulationFieldHelp("culling.buck_rotation_years", buffalo)!.label).toBe(
      "Bull Rotation Years",
    );
    // The six explicit overrides, reached through humanized field keys.
    expect(simulationFieldHelp("culling.buck_doe_ratio", buffalo)!.label).toBe(
      "Milking buffalo per bull",
    );
    expect(simulationFieldHelp("growth.adult_weight_doe_kg", buffalo)!.label).toBe(
      "Adult female weight (kg)",
    );
    expect(simulationFieldHelp("growth.adult_weight_buck_kg", buffalo)!.label).toBe(
      "Adult male weight (kg)",
    );
    expect(simulationFieldHelp("optimization.doe_scale_low", buffalo)!.label).toBe(
      "Herd scale low",
    );
    expect(simulationFieldHelp("optimization.doe_scale_high", buffalo)!.label).toBe(
      "Herd scale high",
    );
    expect(simulationFieldHelp("optimization.doe_scale_steps", buffalo)!.label).toBe(
      "Herd scale steps",
    );
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

  it("writes buffalo nouns for a dairy", () => {
    expect(simulationFieldHelp("herd.bucks", buffalo)!.help.body).toContain(
      "Adult bulls in the starting herd",
    );
    expect(simulationFieldHelp("herd.does", buffalo)!.help.body).toContain(
      "Adult breeding milking buffalo in the starting herd",
    );
    expect(simulationFieldHelp("culling.buck_doe_ratio", buffalo)!.help.body).toContain(
      "Milking buffalo per bull: one bull can serve this many females",
    );
    expect(simulationFieldHelp("feed.dmi_grower", buffalo)!.help.body).toContain(
      "heifer/male grower",
    );
  });
});

describe("simulationFieldHelp — risk variables", () => {
  it("labels the risk variable parent row per species", () => {
    expect(simulationFieldHelp("risk.meat_price", goat)!.label).toBe("Meat price");
    expect(simulationFieldHelp("risk.conception_rate", goat)!.label).toBe("Conception rate");
    expect(simulationFieldHelp("risk.adult_mortality", goat)!.label).toBe("Adult doe mortality");
    expect(simulationFieldHelp("risk.adult_mortality", buffalo)!.label).toBe(
      "Adult milking buffalo mortality",
    );
    expect(simulationFieldHelp("risk.kid_mortality", goat)!.label).toBe("Kid mortality");
    expect(simulationFieldHelp("risk.kid_mortality", buffalo)!.label).toBe("Calf mortality");
    expect(simulationFieldHelp("risk.litter_size", goat)!.label).toBe(
      "Litter size (kids per birth)",
    );
    expect(simulationFieldHelp("risk.litter_size", buffalo)!.label).toBe(
      "Litter size (calves per birth)",
    );
  });

  it("describes the Monte Carlo draw with the lower-cased variable name", () => {
    const goatBody = simulationFieldHelp("risk.adult_mortality", goat)!.help.body;
    expect(goatBody).toContain("uncertainty on the adult doe mortality:");
    expect(goatBody).toContain("between the low and high values below");
    const buffaloBody = simulationFieldHelp("risk.fodder_yield", buffalo)!.help.body;
    expect(buffaloBody).toContain("uncertainty on the fodder yield:");
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
