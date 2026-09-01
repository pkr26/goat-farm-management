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
    expect(enumLabel("outcome", "UNASSESSED")).toBe("Left the herd unassessed");
    expect(enumLabel("taskCategory", "KIDDING_DUE")).toBe("Birth due");
  });

  it("labels buckets per farm species", () => {
    expect(enumLabel("bucket", "PREGNANCY_EARLY", "GOAT")).toBe("Pregnancy A");
    expect(enumLabel("bucket", "PREGNANCY_EARLY", "BUFFALO_DAIRY")).toBe(
      "Milking · Pregnant 1–5 mo",
    );
    expect(enumLabel("bucket", "MALE_KIDS", "BUFFALO_DAIRY")).toBe("Male calves");
    // Unknown farm type falls back to the goat vocabulary.
    expect(enumLabel("bucket", "RECOVERY", null)).toBe("Recovery");
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
});
