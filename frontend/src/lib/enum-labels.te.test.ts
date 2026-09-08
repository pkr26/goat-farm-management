/**
 * Telugu enum labels: the translated kinds resolve in Telugu, untranslated
 * codes fall back to their English label (never the raw code), and the
 * default stays English so pre-existing call sites are unchanged.
 */

import { describe, expect, it } from "vitest";

import { enumLabel } from "./enum-labels";

describe("enumLabel — Telugu map", () => {
  it("translates worker-facing codes", () => {
    expect(enumLabel("sex", "M", "te")).toBe("మగ");
    expect(enumLabel("sex", "F", "te")).toBe("ఆడ");
    expect(enumLabel("taskCategory", "FEED", "te")).toBe("మేత");
    expect(enumLabel("taskCategory", "CLEANING", "te")).toBe("శుభ్రం చేయడం");
    expect(enumLabel("shift", "MORNING", "te")).toBe("ఉదయం");
    expect(enumLabel("shift", "NIGHT", "te")).toBe("రాత్రి");
    expect(enumLabel("bucket", "MALE_KIDS", "te")).toBe("మగ పిల్లలు");
  });

  it("falls back to the English label for codes with no Telugu entry", () => {
    // Deliberately untranslated (no well-established Telugu term).
    expect(enumLabel("taskCategory", "QUARANTINE", "te")).toBe("Quarantine");
    expect(enumLabel("bucket", "QUARANTINE", "te")).toBe("Quarantine");
    // Unknown values still never render as SCREAMING_SNAKE.
    expect(enumLabel("taskCategory", "SOME_NEW_CODE", "te")).toBe("Some New Code");
  });

  it("defaults to English for every existing call site", () => {
    expect(enumLabel("sex", "M")).toBe("Male");
    expect(enumLabel("sex", "M", "en")).toBe("Male");
    expect(enumLabel("taskCategory", "FEED")).toBe("Feeding");
    expect(enumLabel("taskCategory", null, "te")).toBe("—");
  });
});
