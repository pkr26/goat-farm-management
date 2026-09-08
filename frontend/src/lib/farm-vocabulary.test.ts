import { describe, expect, it } from "vitest";

import { farmTypeLabel, farmVocabulary } from "@/lib/farm-vocabulary";

describe("farmVocabulary — goat farm", () => {
  it("uses goat nouns everywhere", () => {
    const v = farmVocabulary;
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
    expect(v.defaultBreed).toBe("Osmanabadi");
    expect(v.tagPrefix).toBe("G");
  });

  it("mirrors the backend goat breeding gates and species facts", () => {
    const v = farmVocabulary;
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
      maxWeightKg: 150,
      birthWeightKg: { min: 0.5, max: 8 },
    });
  });
});

describe("farmTypeLabel", () => {
  it("labels the goat farm type", () => {
    expect(farmTypeLabel).toBe("Goat farm");
  });

  it("agrees with the vocabulary it is derived from", () => {
    expect(farmTypeLabel).toBe(farmVocabulary.typeLabel);
  });
});
