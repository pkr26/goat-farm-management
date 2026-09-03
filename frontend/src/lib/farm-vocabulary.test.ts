import { describe, expect, it } from "vitest";

import { farmTypeLabel, farmVocabulary } from "@/lib/farm-vocabulary";

describe("farmVocabulary — goat farm", () => {
  it("uses goat nouns everywhere", () => {
    const v = farmVocabulary("GOAT");
    expect(v.typeLabel).toBe("Goat farm");
    expect(v.species).toBe("goat");
    expect(v.speciesPlural).toBe("goats");
    expect(v.femaleAdult).toBe("doe");
    expect(v.femaleAdultPlural).toBe("does");
    expect(v.maleAdult).toBe("buck");
    expect(v.young).toBe("kid");
    expect(v.youngPlural).toBe("kids");
    expect(v.parturition).toBe("kidding");
    expect(v.parturitionCap).toBe("Kidding");
    expect(v.parturitionPast).toBe("kidded");
    expect(v.dueLabel).toBe("Kidding due");
    expect(v.breedingGateCopy).toContain("A doe must be at least 10 months old and 22 kg");
    expect(v.dairy).toBe(false);
    expect(v.defaultBreed).toBe("Osmanabadi");
    expect(v.tagPrefix).toBe("G");
  });

  it("mirrors the backend goat breeding gates and species facts", () => {
    const v = farmVocabulary("GOAT");
    expect(v.breedingEntry).toEqual({
      female: { minMonths: 10, minWeightKg: 22 },
      male: { minMonths: 12, minWeightKg: 25 },
    });
    expect(v.facts).toEqual({
      gestationWindowDays: { min: 100, max: 200 },
      pregnancyCheckDays: 32,
      weaningDays: 60,
      youngStayWithDam: true,
      maxLitterSize: 4,
    });
  });
});

describe("farmVocabulary — buffalo dairy", () => {
  it("uses dairy nouns everywhere, including the irregular calf plural", () => {
    const v = farmVocabulary("BUFFALO_DAIRY");
    expect(v.typeLabel).toBe("Buffalo dairy");
    expect(v.species).toBe("buffalo");
    expect(v.speciesPlural).toBe("buffalo");
    expect(v.femaleAdult).toBe("milking buffalo");
    expect(v.femaleAdultPlural).toBe("milking buffalo");
    expect(v.maleAdult).toBe("bull");
    expect(v.young).toBe("calf");
    expect(v.youngPlural).toBe("calves");
    expect(v.parturition).toBe("calving");
    expect(v.parturitionCap).toBe("Calving");
    expect(v.parturitionPast).toBe("calved");
    expect(v.dueLabel).toBe("Calving due");
    expect(v.breedingGateCopy).toContain("Heifers are bred at 24 months and ≥340 kg");
    expect(v.dairy).toBe(true);
    expect(v.defaultBreed).toBe("Murrah");
    expect(v.tagPrefix).toBe("G");
  });

  it("mirrors the backend buffalo breeding gates and species facts", () => {
    const v = farmVocabulary("BUFFALO_DAIRY");
    expect(v.breedingEntry).toEqual({
      female: { minMonths: 24, minWeightKg: 340 },
      male: { minMonths: 24, minWeightKg: 350 },
    });
    expect(v.facts).toEqual({
      gestationWindowDays: { min: 270, max: 350 },
      pregnancyCheckDays: 60,
      weaningDays: 90,
      youngStayWithDam: false,
      maxLitterSize: 2,
    });
  });
});

describe("farmVocabulary — fallbacks", () => {
  it("defaults to the goat vocabulary for missing and unknown farm types", () => {
    const candidates: (string | null | undefined)[] = [
      null,
      undefined,
      "",
      "PIG_FARM",
      "BUFFALO",
    ];
    for (const farmType of candidates) {
      const v = farmVocabulary(farmType);
      expect(v.typeLabel).toBe("Goat farm");
      expect(v.femaleAdult).toBe("doe");
      expect(v.youngPlural).toBe("kids");
      expect(v.dairy).toBe(false);
    }
  });
});

describe("farmTypeLabel", () => {
  it("labels each farm type", () => {
    expect(farmTypeLabel("GOAT")).toBe("Goat farm");
    expect(farmTypeLabel("BUFFALO_DAIRY")).toBe("Buffalo dairy");
  });

  it("falls back to the goat label for missing and unknown farm types", () => {
    expect(farmTypeLabel(null)).toBe("Goat farm");
    expect(farmTypeLabel(undefined)).toBe("Goat farm");
    expect(farmTypeLabel("NOT_A_FARM_TYPE")).toBe("Goat farm");
    expect(farmTypeLabel("")).toBe("Goat farm");
  });

  it("agrees with the vocabulary it is derived from", () => {
    expect(farmTypeLabel("GOAT")).toBe(farmVocabulary("GOAT").typeLabel);
    expect(farmTypeLabel("BUFFALO_DAIRY")).toBe(farmVocabulary("BUFFALO_DAIRY").typeLabel);
  });
});
