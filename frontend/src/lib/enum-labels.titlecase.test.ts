import { describe, expect, it } from "vitest";

import { enumLabel, humanizeEnum } from "@/lib/enum-labels";

describe("titleCase fallback", () => {
  it("splits on underscores, spaces and hyphens", () => {
    expect(humanizeEnum("FOO_BAR")).toBe("Foo Bar");
    expect(humanizeEnum("FOO BAR")).toBe("Foo Bar");
    expect(humanizeEnum("FOO-BAR")).toBe("Foo Bar");
    expect(humanizeEnum("FOO_BAR-BAZ QUX")).toBe("Foo Bar Baz Qux");
  });

  it("lower-cases the code before Title-Casing it", () => {
    expect(humanizeEnum("alreadyMixedCase")).toBe("Alreadymixedcase");
    expect(humanizeEnum("noSCREAMINGcodes")).toBe("Noscreamingcodes");
    expect(enumLabel("sex", "sOmEtHiNg")).toBe("Something");
  });

  it("drops empty tokens from leading, trailing and doubled separators", () => {
    expect(humanizeEnum("-VACCINE-")).toBe("Vaccine");
    expect(humanizeEnum("__DEWORMING__")).toBe("Deworming");
    expect(humanizeEnum("AI--SEXED")).toBe("AI Sexed");
    expect(humanizeEnum(" - ")).toBe("");
  });

  it("upper-cases only the exact word 'ai', not words merely containing it", () => {
    expect(humanizeEnum("AI")).toBe("AI");
    expect(humanizeEnum("AI_SEXED")).toBe("AI Sexed");
    expect(humanizeEnum("OPEN_AI")).toBe("Open AI");
    // Starts with "ai" but is a longer word — not the acronym.
    expect(humanizeEnum("AIRDROP")).toBe("Airdrop");
    // Ends with "ai" but is a longer word — not the acronym.
    expect(humanizeEnum("BONSAI")).toBe("Bonsai");
    expect(humanizeEnum("OSMANABADI_HERD")).toBe("Osmanabadi Herd");
  });

  it("renders an em dash for null, undefined and empty-string values", () => {
    expect(humanizeEnum(null)).toBe("—");
    expect(humanizeEnum(undefined)).toBe("—");
    expect(humanizeEnum("")).toBe("—");
  });
});

describe("enumLabel bucket labels", () => {
  it("labels every goat ward", () => {
    const expected: Record<string, string> = {
      QUARANTINE: "Quarantine",
      FOUNDATION: "Foundation",
      BREEDING: "Breeding",
      PREGNANCY_EARLY: "Pregnancy A",
      PREGNANCY_LATE: "Pregnancy B",
      DELIVERY: "Delivery",
      RECOVERY: "Recovery",
      RESTING: "Resting",
      MALE_KIDS: "Male kids",
      FEMALE_KIDS: "Female kids",
    };
    for (const [code, label] of Object.entries(expected)) {
      expect(enumLabel("bucket", code)).toBe(label);
    }
  });



  it("Title-Cases unknown bucket codes instead of screaming enums", () => {
    expect(enumLabel("bucket", "ISOLATION_WARD")).toBe("Isolation Ward");
    expect(enumLabel("bucket", "SICK_PEN")).toBe("Sick Pen");
  });
});
