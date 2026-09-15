import { describe, expect, it } from "vitest";

import { enumLabel, humanizeEnum } from "@/lib/enum-labels";

describe("enumLabel", () => {
  it("labels every simple kind", () => {
    expect(enumLabel("sex", "M")).toBe("Male");
    expect(enumLabel("sex", "F")).toBe("Female");
    expect(enumLabel("source", "BORN")).toBe("Born on farm");
    expect(enumLabel("method", "AI_SEXED")).toBe("AI (sexed)");
    expect(enumLabel("eventType", "VACCINE")).toBe("Vaccination");
    expect(enumLabel("shift", "MORNING")).toBe("Morning");
    expect(enumLabel("txCategory", "ANIMAL_PURCHASE")).toBe("Animal purchase");
    expect(enumLabel("txType", "INCOME")).toBe("Income");
    expect(enumLabel("kidStatus", "STILLBORN")).toBe("Stillborn");
    expect(enumLabel("ease", "DIFFICULT")).toBe("Difficult");
    expect(enumLabel("ease", "CAESAREAN")).toBe("Caesarean");
    expect(enumLabel("outcome", "UNASSESSED")).toBe("Left the herd unassessed");
    expect(enumLabel("taskCategory", "KIDDING_DUE")).toBe("Birth due");
  });

  it("labels the eleven husbandry duty categories", () => {
    expect(enumLabel("taskCategory", "KIDDING_WATCH")).toBe("Kidding watch");
    expect(enumLabel("taskCategory", "BIRTHING_KIT")).toBe("Birthing kit check");
    expect(enumLabel("taskCategory", "HEALTH_CHECK")).toBe("Health check");
    expect(enumLabel("taskCategory", "HEAT_WATCH")).toBe("Heat watch");
    expect(enumLabel("taskCategory", "HOOF_TRIMMING")).toBe("Hoof trimming");
    expect(enumLabel("taskCategory", "SPRAYING")).toBe("Spraying");
    expect(enumLabel("taskCategory", "DISINFECTION")).toBe("Disinfection");
    expect(enumLabel("taskCategory", "WEIGHING")).toBe("Weighing");
    expect(enumLabel("taskCategory", "REBREED")).toBe("Re-breed");
    expect(enumLabel("taskCategory", "BUCK_ROTATION")).toBe("Buck rotation");
    expect(enumLabel("taskCategory", "INSURANCE")).toBe("Insurance");
  });

  it("labels the coded mortality causes", () => {
    expect(enumLabel("mortalityCause", "PNEUMONIA")).toBe("Pneumonia");
    expect(enumLabel("mortalityCause", "PPR_SUSPECTED")).toBe("PPR (suspected)");
    expect(enumLabel("mortalityCause", "GOAT_POX_SUSPECTED")).toBe("Goat pox (suspected)");
    expect(enumLabel("mortalityCause", "OLD_AGE")).toBe("Old age");
    expect(enumLabel("mortalityCause", "UNKNOWN")).toBe("Unknown");
    // An unmapped code still humanizes instead of screaming.
    expect(enumLabel("mortalityCause", "NEW_CAUSE")).toBe("New Cause");
  });

  it("labels the bounded disposal methods, in Telugu too", () => {
    expect(enumLabel("disposalMethod", "DEEP_BURIAL")).toBe("Deep burial");
    expect(enumLabel("disposalMethod", "COMPOSTING")).toBe("Composting");
    expect(enumLabel("disposalMethod", "DEEP_BURIAL", "te")).toBe("లోతుగా పాతడం");
  });

  it("labels the goat buckets", () => {
    expect(enumLabel("bucket", "PREGNANCY_EARLY")).toBe("Pregnancy A");
    expect(enumLabel("bucket", "MALE_KIDS")).toBe("Male kids");
    expect(enumLabel("bucket", "RECOVERY")).toBe("Recovery");
  });

  it("Title-Cases unknown codes instead of screaming enums", () => {
    expect(enumLabel("status", "SOMETHING_NEW")).toBe("Something New");
    expect(enumLabel("txCategory", "NEW_CODE")).toBe("New Code");
  });

  it("renders an em dash for missing values", () => {
    expect(enumLabel("sex", null)).toBe("—");
    expect(enumLabel("sex", undefined)).toBe("—");
    expect(enumLabel("sex", "")).toBe("—");
  });

  it("humanizeEnum Title-Cases arbitrary codes", () => {
    expect(humanizeEnum("BUCKET_MOVE")).toBe("Bucket Move");
    expect(humanizeEnum(null)).toBe("—");
  });

  it("labels the insurance register lifecycle", () => {
    expect(enumLabel("insuranceStatus", "active")).toBe("Active");
    expect(enumLabel("insuranceStatus", "renewed")).toBe("Renewed");
    expect(enumLabel("insuranceStatus", "lapsed")).toBe("Lapsed");
    expect(enumLabel("insuranceStatus", "claimed")).toBe("Claimed");
  });

  it("translates the worker-facing duty categories to Telugu", () => {
    expect(enumLabel("taskCategory", "KIDDING_WATCH", "te")).toBe("పిల్లల కోసం గమనింపు");
    expect(enumLabel("taskCategory", "BIRTHING_KIT", "te")).toBe("జనన కిట్ సరిచూడటం");
    expect(enumLabel("taskCategory", "HOOF_TRIMMING", "te")).toBe("గిట్టు కత్తిరింపు");
    expect(enumLabel("taskCategory", "WEIGHING", "te")).toBe("బరువు చూడటం");
    expect(enumLabel("taskCategory", "INSURANCE", "te")).toBe("భీమా");
    // Every kind now carries a Telugu map; unknown codes still humanize.
    expect(enumLabel("mortalityCause", "PNEUMONIA", "te")).toBe("న్యుమోనియా");
    expect(enumLabel("insuranceStatus", "lapsed", "te")).toBe("రద్దైంది");
  });
});
